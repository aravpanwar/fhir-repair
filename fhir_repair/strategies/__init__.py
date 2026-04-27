"""Repair strategies.

Two flavours:

  - Deterministic: pure functions, one per fix pattern. Live under
    `fhir_repair.strategies.deterministic`.
  - LLM-backed: invoke an LLM with a prompt template and a retrieved
    spec excerpt. Live under `fhir_repair.strategies.llm`.

Strategies are registered in the `StrategyRegistry`; the dispatcher looks
them up by id (e.g., `deterministic.normalize_date`).
"""

from fhir_repair.strategies.base import Strategy
from fhir_repair.strategies.registry import StrategyRegistry, default_registry

__all__ = ["Strategy", "StrategyRegistry", "default_registry"]
