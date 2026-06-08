"""LLM provider adapters.

The provider interface lives in `base`. Specific adapters (Anthropic,
OpenAI, Bedrock, on-prem Llama) live in their own modules. Anthropic and
OpenAI ship today; others can be added without touching core code.

Provider adapters are responsible for:

  - Translating `PromptSegment.stable=True` into the provider's native
    caching primitive (or ignoring the hint if the provider caches
    automatically).
  - Reading credentials from environment variables, never from a config
    file.
  - Returning a `Completion` with token counts populated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fhir_repair.llm.base import Completion, LLMProvider

if TYPE_CHECKING:
    from fhir_repair.core.config import LLMConfig


def build_llm_provider(config: LLMConfig) -> LLMProvider:
    """Construct the provider adapter named in `config.provider`.

    Raises ValueError for unknown or unimplemented providers. Lets the
    underlying adapter raise (typically ImportError or ValueError) when
    optional dependencies or credentials are missing, so the error
    message is provider-specific.
    """
    provider = (config.provider or "").lower()

    if provider == "anthropic":
        from fhir_repair.llm.anthropic import AnthropicProvider

        # Provide a default model when none is configured. The config
        # type allows None so YAML expansion of an unset env var doesn't
        # fail validation; the build function is the right place to apply
        # provider-specific defaults.
        return AnthropicProvider(
            api_key=config.api_key or None,
            model=config.model or "claude-sonnet-4-6",
            endpoint=config.endpoint or None,
        )

    if provider == "openai":
        from fhir_repair.llm.openai import OpenAIProvider

        return OpenAIProvider(
            api_key=config.api_key or None,
            model=config.model or "gpt-4o-mini",
            endpoint=config.endpoint or None,
        )

    if provider in ("bedrock", "on-prem", "azure", "vertex"):
        # Adapters planned but not shipped yet. Raise a clear error rather
        # than silently picking a default.
        raise NotImplementedError(
            f"LLM provider {provider!r} is recognised but not yet implemented. "
            "Implement an adapter in fhir_repair/llm/ that satisfies the "
            "LLMProvider Protocol, and add a branch here."
        )

    raise ValueError(
        f"Unknown LLM provider: {config.provider!r}. "
        "Set llm.provider in repair-config.yaml to one of: anthropic, openai."
    )


__all__ = ["Completion", "LLMProvider", "build_llm_provider"]
