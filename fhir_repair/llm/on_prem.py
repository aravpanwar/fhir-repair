"""On-premise provider adapter for OpenAI-compatible servers.

This is the adapter for deployments where no data may leave the network at
all: a self-hosted vLLM, Ollama, llama.cpp, or TGI server, or a vendor
gateway that speaks the same protocol. All of them expose the OpenAI Chat
Completions wire format, so this adapter reuses the `openai` SDK and only
changes what is required of the caller.

Two differences from the hosted OpenAI adapter justify a separate module
rather than a flag:

  - `endpoint` is required. Without it the SDK would silently fall back to
    api.openai.com, which for an on-prem deployment means sending clinical
    data to a third party. That failure has to be loud.
  - The API key is optional. Self-hosted servers commonly run without
    authentication inside a trusted network; a placeholder is sent because
    the SDK requires the header to be present.

`openai` is an optional dependency; install with
`pip install "fhir-repair[openai]"`.
"""

from __future__ import annotations

import os
import time
from typing import Any

from fhir_repair.core.models import PromptSegment
from fhir_repair.llm.base import Completion

# The OpenAI SDK requires a non-empty api_key. Unauthenticated local servers
# ignore the header, so a placeholder keeps the SDK happy without implying a
# credential exists.
_UNAUTHENTICATED_PLACEHOLDER = "not-used"


class OnPremProvider:
    """OpenAI-protocol client pointed at a self-hosted model server."""

    def __init__(
        self,
        endpoint: str | None = None,
        model: str = "",
        api_key: str | None = None,
    ) -> None:
        # Imported lazily so the package loads on systems without the
        # optional `openai` dependency installed.
        try:
            import openai
        except ImportError as exc:
            raise ImportError(
                "OnPremProvider requires the optional `openai` package, which "
                "provides the client for OpenAI-compatible servers. "
                "Install with: pip install 'fhir-repair[openai]'"
            ) from exc

        resolved_endpoint = endpoint or os.environ.get("LLM_ENDPOINT")
        if not resolved_endpoint:
            # Defaulting here would send data to api.openai.com, defeating
            # the reason for choosing an on-prem provider.
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

        self._client = openai.OpenAI(
            api_key=api_key or os.environ.get("LLM_API_KEY") or _UNAUTHENTICATED_PLACEHOLDER,
            base_url=resolved_endpoint,
        )
        self._model = model
        self._endpoint = resolved_endpoint

    def complete(
        self,
        segments: list[PromptSegment],
        **kwargs: Any,
    ) -> Completion:
        """Send segments to the on-prem server and return a Completion.

        Identical wire format to the OpenAI adapter. Whether the server
        caches prefixes depends on how it was built and configured, so no
        caching is claimed and no cache hint is sent.
        """
        if not any(seg.role != "system" for seg in segments):
            raise ValueError(
                "OnPremProvider received only system segments; the runner "
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

        # Local servers vary in how completely they populate `usage`; some
        # omit it entirely. Report zeros rather than failing the repair.
        usage = getattr(response, "usage", None)
        return Completion(
            text=text,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0 if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0 if usage else 0,
            latency_ms=latency_ms,
            model=self._model,
            provider="on-prem",
        )

    def supports_caching(self) -> bool:
        # Some servers do cache prefixes, but there is no portable way to
        # detect it and no primitive to apply, so claim nothing.
        return False
