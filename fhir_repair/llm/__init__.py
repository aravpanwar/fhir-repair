"""LLM provider adapters.

The provider interface lives in `base`. Specific adapters (Anthropic,
OpenAI, Bedrock, on-prem Llama) live in their own modules. v0.1 ships only
the Anthropic adapter; others can be added without touching core code.

Provider adapters are responsible for:

  - Translating `PromptSegment.stable=True` into the provider's native
    caching primitive (or ignoring the hint if the provider caches
    automatically).
  - Reading credentials from environment variables, never from a config
    file.
  - Returning a `Completion` with token counts populated.
"""

from fhir_repair.llm.base import Completion, LLMProvider

__all__ = ["Completion", "LLMProvider"]
