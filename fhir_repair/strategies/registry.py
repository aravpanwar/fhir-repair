"""Strategy registry.

Maps strategy ids (`deterministic.normalize_date`, `llm.suggest_terminology_match`,
etc.) to their implementations. The default registry is populated with the
strategies shipped in v0.1.

Users can build a custom registry by instantiating `StrategyRegistry` and
calling `register()`, then passing it to `Repairer(registry=...)`.
"""

from __future__ import annotations

from typing import Any

from fhir_repair.core.models import RepairAction, ValidationError
from fhir_repair.strategies.base import Strategy


class _ModuleStrategy:
    """Adapt a module-level `apply()` plus constants into a Strategy object.

    The deterministic strategies are written as modules with `NAME`,
    `VERSION`, `PERMISSION`, `RISK` constants and an `apply()` function.
    This adapter wraps them so they satisfy the `Strategy` Protocol without
    each module needing to define a class.
    """

    def __init__(self, module: Any) -> None:
        self.name: str = module.NAME
        self.version: str = module.VERSION
        self.permission: str = module.PERMISSION
        self.risk: str = module.RISK
        self._apply = module.apply

    def apply(
        self,
        resource: dict[str, Any],
        error: ValidationError,
    ) -> RepairAction:
        result: RepairAction = self._apply(resource, error)
        return result


class StrategyRegistry:
    """A name -> Strategy map.

    Methods:
      - `register(strategy)`: add a Strategy.
      - `register_module(module)`: add a strategy defined as a module.
      - `get(name)`: look up by name. Raises KeyError on miss.
    """

    def __init__(self) -> None:
        self._strategies: dict[str, Strategy] = {}

    def register(self, strategy: Strategy) -> None:
        self._strategies[strategy.name] = strategy

    def register_module(self, module: Any) -> None:
        self.register(_ModuleStrategy(module))

    def get(self, name: str) -> Strategy:
        if name not in self._strategies:
            raise KeyError(f"No strategy registered with name {name!r}")
        return self._strategies[name]

    def names(self) -> list[str]:
        return sorted(self._strategies.keys())


def default_registry() -> StrategyRegistry:
    """Registry pre-populated with v0.1 deterministic strategies.

    LLM strategies are registered separately by callers that have an
    LLMProvider configured. This keeps `default_registry()` import-safe in
    environments without the optional `anthropic` dependency installed.
    """
    # Imports are local so the registry module itself stays import-light.
    from fhir_repair.strategies.deterministic import cardinality, date

    registry = StrategyRegistry()
    registry.register_module(date)
    registry.register_module(cardinality)
    return registry
