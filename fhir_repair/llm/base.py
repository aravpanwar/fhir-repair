"""LLM provider Protocol and the `Completion` return type.

The runner sends a list of `PromptSegment` objects (defined in
`fhir_repair.core.models`) and receives a `Completion`. Caching is the
provider's responsibility: if `supports_caching()` returns True, the
adapter is expected to use the `stable` hints on incoming segments to
engage its native caching primitive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from fhir_repair.core.models import PromptSegment


@dataclass
class Completion:
    """Result of an LLM call.

    `cached_tokens` is the subset of `input_tokens` served from cache.
    Providers that do not report cache hits leave this at 0 even when
    caching was effective; consumers should not rely on it being accurate
    across providers.
    """

    text: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    latency_ms: int = 0
    model: str = ""
    provider: str = ""


@runtime_checkable
class LLMProvider(Protocol):
    """Synchronous provider interface."""

    def complete(
        self,
        segments: list[PromptSegment],
        **kwargs: Any,
    ) -> Completion:
        """Send the segments and return a Completion.

        Provider adapters interpret `kwargs` for provider-specific options
        (max tokens, temperature, top-p). Unrecognised kwargs should be
        ignored rather than raising, so the runner can pass uniform
        parameters across providers.
        """
        ...

    def supports_caching(self) -> bool:
        """True if the adapter applies a native caching primitive to stable segments."""
        ...
