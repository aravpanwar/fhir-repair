"""OpenAI provider adapter.

Wraps the official `openai` SDK Chat Completions API. Unlike Anthropic,
OpenAI caches long prompt prefixes automatically (prefix caching) with no
per-segment markup, so this adapter ignores the `stable` hint on incoming
segments and reports `supports_caching()` as False: it applies no caching
primitive of its own.

`OnPremProvider` drives a self-hosted server (vLLM, Ollama, llama.cpp, TGI,
or a gateway) that speaks the same protocol, and differs only in what it
requires of the caller.

The `openai` package is an optional dependency; install the `openai` extra
(see Installation in the README). Set a private or Azure-style endpoint with
the `endpoint` argument (mapped to the SDK `base_url`).
"""

from __future__ import annotations

import os
import time
from typing import Any

from fhir_repair.core.models import PromptSegment
from fhir_repair.llm.base import Completion, vendor_options


class OpenAIProvider:
    """Thin wrapper around the official `openai` SDK."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
        endpoint: str | None = None,
        provider_name: str = "openai",
    ) -> None:
        # Imported lazily so the package can be loaded on systems without
        # the optional `openai` dependency installed.
        try:
            import openai
        except ImportError as exc:
            raise ImportError(
                "OpenAIProvider requires the optional `openai` package. "
                "Install the `openai` extra; see Installation in the README."
            ) from exc

        resolved_key = api_key or os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not resolved_key:
            raise ValueError(
                "OpenAIProvider requires an API key. Set LLM_API_KEY or "
                "OPENAI_API_KEY in the environment, or pass api_key="
            )

        # `base_url` lets advanced deployers point at a private proxy or an
        # Azure/OpenAI-compatible gateway.
        # max_retries=0: the LLM strategies retry transient failures
        # themselves, and leaving the SDK's own retries on would multiply
        # the attempts.
        self._client = openai.OpenAI(
            api_key=resolved_key,
            base_url=endpoint,
            max_retries=0,
        )
        self._model = model
        # Reported in the audit log and the leaderboard. Overridable because
        # several vendors serve this exact wire format from their own
        # endpoint, and a run against one of them should not claim to be an
        # OpenAI run.
        self._provider_name = provider_name

    def complete(
        self,
        segments: list[PromptSegment],
        **kwargs: Any,
    ) -> Completion:
        """Send segments to OpenAI and return a Completion.

        All segments map onto the single Chat Completions `messages` list,
        keyed by role. The `stable` hint is ignored: OpenAI's prefix cache
        engages automatically when prompts share a leading prefix.
        """
        if not any(seg.role != "system" for seg in segments):
            # The runner always sends a user segment. A system-only request
            # is a caller bug; reject it the same way the Anthropic adapter
            # does so the failure mode is identical across providers.
            raise ValueError(
                "OpenAIProvider received only system segments; the runner "
                "must include at least one user segment."
            )

        request: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": seg.role, "content": seg.text} for seg in segments],
            "max_tokens": kwargs.get("max_tokens", 1024),
        }
        # 0.0 unless the caller sets one; a null asks for none, for models
        # that reject the parameter or ignore it while thinking.
        temperature = kwargs.get("temperature", 0.0)
        if temperature is not None:
            request["temperature"] = temperature
        # The SDK rejects keyword arguments it does not know, so vendor
        # options such as DeepSeek's `reasoning_effort` go in the body.
        extra = vendor_options(kwargs)
        if extra:
            request["extra_body"] = extra

        start = time.perf_counter()
        response = self._client.chat.completions.create(**request)
        latency_ms = int((time.perf_counter() - start) * 1000)

        text = response.choices[0].message.content or ""

        # OpenAI types `usage` as optional, and self-hosted servers vary in
        # how completely they fill it in. Missing counts report zero rather
        # than failing the repair.
        usage = getattr(response, "usage", None)
        return Completion(
            text=text,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            cached_tokens=_cached_tokens(usage) if usage else 0,
            latency_ms=latency_ms,
            # The id the server reports, which shows when a requested alias
            # is served by a different model.
            model=getattr(response, "model", None) or self._model,
            provider=self._provider_name,
        )

    def supports_caching(self) -> bool:
        return False


# The OpenAI SDK requires a non-empty api_key. Unauthenticated local servers
# ignore the header, so a placeholder keeps the SDK happy without implying a
# credential exists.
_UNAUTHENTICATED_PLACEHOLDER = "not-used"


class OnPremProvider(OpenAIProvider):
    """OpenAI-protocol client pointed at a self-hosted model server.

    For deployments where no data may leave the network. Two requirements
    differ from the hosted adapter:

      - `endpoint` is required. Without it the SDK would silently fall back
        to api.openai.com, which for an on-prem deployment means sending
        clinical data to a third party. That failure has to be loud.
      - The API key is optional. Self-hosted servers commonly run without
        authentication inside a trusted network.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        model: str = "",
        api_key: str | None = None,
    ) -> None:
        resolved_endpoint = endpoint or os.environ.get("LLM_ENDPOINT")
        if not resolved_endpoint:
            raise ValueError(
                "OnPremProvider requires an endpoint pointing at your model "
                "server (for example http://localhost:8000/v1). Set "
                "LLM_ENDPOINT in the environment, or pass endpoint="
            )

        if not model:
            raise ValueError(
                "OnPremProvider requires a model name matching the one your "
                "server serves. Set LLM_MODEL in the environment, or pass model="
            )

        super().__init__(
            api_key=api_key or os.environ.get("LLM_API_KEY") or _UNAUTHENTICATED_PLACEHOLDER,
            model=model,
            endpoint=resolved_endpoint,
            provider_name="on-prem",
        )


def _cached_tokens(usage: Any) -> int:
    """Read cached prompt tokens from a usage object, defensively.

    Newer OpenAI responses expose `usage.prompt_tokens_details.cached_tokens`.
    Older ones omit it. DeepSeek reports `prompt_cache_hit_tokens` instead.
    The details payload may be an object or a dict depending on SDK version,
    so handle both and fall back to 0.
    """
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None:
        return int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0)
    if isinstance(details, dict):
        return int(details.get("cached_tokens", 0) or 0)
    return int(getattr(details, "cached_tokens", 0) or 0)
