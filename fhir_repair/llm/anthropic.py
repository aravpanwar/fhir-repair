"""Anthropic provider adapter.

This is the only file in the codebase that knows about Anthropic-specific
caching syntax. Stable PromptSegments become text blocks tagged with
`cache_control: {"type": "ephemeral"}`, which engages Anthropic's prompt
cache (~10% input cost on cache hit, 5-minute TTL).

The `anthropic` package is an optional dependency; install with
`pip install "fhir-repair[anthropic]"`.
"""

from __future__ import annotations

import os
import time
from typing import Any

from fhir_repair.core.models import PromptSegment
from fhir_repair.llm.base import Completion


class AnthropicProvider:
    """Thin wrapper around the official `anthropic` SDK."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
        endpoint: str | None = None,
    ) -> None:
        # Imported lazily so the package can be loaded on systems without
        # the optional `anthropic` dependency installed.
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "AnthropicProvider requires the optional `anthropic` package. "
                "Install with: pip install 'fhir-repair[anthropic]'"
            ) from exc

        resolved_key = (
            api_key or os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        )
        if not resolved_key:
            raise ValueError(
                "AnthropicProvider requires an API key. Set LLM_API_KEY or "
                "ANTHROPIC_API_KEY in the environment, or pass api_key="
            )

        # `base_url` lets advanced deployers point at a private proxy or
        # a Bedrock-style API gateway.
        self._client = anthropic.Anthropic(
            api_key=resolved_key,
            base_url=endpoint,
        )
        self._model = model

    def complete(
        self,
        segments: list[PromptSegment],
        **kwargs: Any,
    ) -> Completion:
        """Send segments to Anthropic and return a Completion.

        System and user/assistant segments are split: Anthropic takes
        system prompts as a top-level argument and user/assistant turns as
        messages. Stable segments emit `cache_control` markers so the
        prompt cache can serve repeated prefixes.
        """
        system_blocks: list[dict[str, Any]] = []
        messages: list[dict[str, Any]] = []

        for seg in segments:
            block: dict[str, Any] = {"type": "text", "text": seg.text}
            if seg.stable:
                # `ephemeral` is the standard 5-minute cache. Anthropic
                # also supports an extended 1-hour cache; we default to
                # ephemeral because most repair runs finish well within
                # that window and the extended cache costs more on write.
                block["cache_control"] = {"type": "ephemeral"}

            if seg.role == "system":
                system_blocks.append(block)
            else:
                messages.append({"role": seg.role, "content": [block]})

        if not messages:
            # Anthropic requires at least one message turn. Reject early
            # with a clear error rather than letting the SDK return one.
            raise ValueError(
                "AnthropicProvider received only system segments; the "
                "runner must include at least one user segment."
            )

        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": kwargs.get("max_tokens", 1024),
            "temperature": kwargs.get("temperature", 0.0),
            "messages": messages,
        }
        if system_blocks:
            request["system"] = system_blocks

        start = time.perf_counter()
        response = self._client.messages.create(**request)
        latency_ms = int((time.perf_counter() - start) * 1000)

        # Walk the content blocks. The default parser expects a single
        # text block; we concatenate all text blocks defensively. The SDK
        # may return non-text blocks (e.g. tool use); we ignore those.
        text_parts: list[str] = []
        for content_block in response.content:
            if getattr(content_block, "type", None) == "text":
                text_parts.append(content_block.text)
        text = "".join(text_parts)

        usage = response.usage
        # Cache hit accounting: `cache_read_input_tokens` is the count
        # served from cache, `cache_creation_input_tokens` is the count
        # written to cache for the first time. We report cache reads as
        # `cached_tokens`; cache writes are billable but uncached.
        cached = getattr(usage, "cache_read_input_tokens", 0) or 0

        return Completion(
            text=text,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=cached,
            latency_ms=latency_ms,
            model=self._model,
            provider="anthropic",
        )

    def supports_caching(self) -> bool:
        return True
