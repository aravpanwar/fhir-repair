"""OpenAI provider adapter.

Wraps the official `openai` SDK Chat Completions API. Unlike Anthropic,
OpenAI caches long prompt prefixes automatically (prefix caching) with no
per-segment markup, so this adapter ignores the `stable` hint on incoming
segments and reports `supports_caching()` as False: it applies no caching
primitive of its own.

The `openai` package is an optional dependency; install with
`pip install "fhir-repair[openai]"`. Set a private or Azure-style endpoint
with the `endpoint` argument (mapped to the SDK `base_url`).
"""

from __future__ import annotations

import os
import time
from typing import Any

from fhir_repair.core.models import PromptSegment
from fhir_repair.llm.base import Completion


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
                "Install with: pip install 'fhir-repair[openai]'"
            ) from exc

        resolved_key = api_key or os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not resolved_key:
            raise ValueError(
                "OpenAIProvider requires an API key. Set LLM_API_KEY or "
                "OPENAI_API_KEY in the environment, or pass api_key="
            )

        # `base_url` lets advanced deployers point at a private proxy or an
        # Azure/OpenAI-compatible gateway.
        self._client = openai.OpenAI(
            api_key=resolved_key,
            base_url=endpoint,
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
            "temperature": kwargs.get("temperature", 0.0),
        }

        start = time.perf_counter()
        response = self._client.chat.completions.create(**request)
        latency_ms = int((time.perf_counter() - start) * 1000)

        text = response.choices[0].message.content or ""

        # OpenAI types `usage` as optional; guard rather than assume it is
        # populated. Missing usage simply reports zero tokens.
        usage = response.usage
        return Completion(
            text=text,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            cached_tokens=_cached_tokens(usage) if usage else 0,
            latency_ms=latency_ms,
            model=self._model,
            provider=self._provider_name,
        )

    def supports_caching(self) -> bool:
        return False


def _cached_tokens(usage: Any) -> int:
    """Read cached prompt tokens from a usage object, defensively.

    Newer OpenAI responses expose `usage.prompt_tokens_details.cached_tokens`.
    Older ones omit it. The details payload may be an object or a dict
    depending on SDK version, so handle both and fall back to 0.
    """
    details = getattr(usage, "prompt_tokens_details", None)
    if details is None:
        return 0
    if isinstance(details, dict):
        return int(details.get("cached_tokens", 0) or 0)
    return int(getattr(details, "cached_tokens", 0) or 0)
