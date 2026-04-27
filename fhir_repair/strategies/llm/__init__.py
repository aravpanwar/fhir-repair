"""LLM-backed strategies.

These strategies wrap an LLMProvider (and optionally a TerminologyService)
to handle errors that have no deterministic fix: terminology binding, free
text interpretation, complex invariant violations.

Use of these strategies is opt-in via the dispatch table. The hallucination
guard determines which permission each strategy exercises and whether the
guard grants it.
"""

from fhir_repair.strategies.llm.runner import LLMStrategy

__all__ = ["LLMStrategy"]
