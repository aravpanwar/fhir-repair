"""Strategy dispatcher.

Resolves an error to its strategy and runs the multi-error interaction
protocol described in docs/architecture.md:

  1. Leaf-first ordering: deepest FHIRPath first, deterministic before LLM
     at equal depth.
  2. Depth-batched application: same-depth deterministic fixes apply
     together, then we re-validate.
  3. Scope conflict serialisation: actions targeting overlapping paths
     serialise.
  4. Regression rollback: a fix that introduces a new error at a path it
     touched gets rolled back and retried sequentially.
  5. Termination: bounded by max_attempts and a stuck-detector.

The dispatcher knows nothing about specific strategies. It only knows how
to look one up by name in a registry and how to run it.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from fhir_repair.core.guard import HallucinationGuard
from fhir_repair.core.models import RepairAction, ValidationError

# A strategy plan entry is (strategy_id, error). The strategy_id is
# resolved against the registry when the plan is executed.
PlanEntry = tuple[str, ValidationError]


@dataclass
class DispatchPlan:
    """Ordered execution plan for a set of validation errors.

    `batches` is a list of equal-depth groups in execution order (deepest
    first). Within each batch, deterministic strategies precede LLM
    strategies. Errors with no mapped strategy go into `unmapped`.
    """

    batches: list[list[PlanEntry]]
    unmapped: list[ValidationError]


class StrategyResolver:
    """Resolves an error code to a strategy identifier from the dispatch table."""

    def __init__(self, table: dict[str, str]):
        self._table = table

    def resolve(self, error: ValidationError) -> str | None:
        """Return the strategy id for an error, or None if unmapped.

        Resolution order:
          1. Exact `error.code` match
          2. `unknown-error` catch-all
          3. None
        """
        if error.code in self._table:
            return self._table[error.code]
        return self._table.get("unknown-error")


def build_plan(
    errors: list[ValidationError],
    resolver: StrategyResolver,
) -> DispatchPlan:
    """Group errors into depth-batched, deterministic-first execution order."""
    mapped: list[tuple[int, str, ValidationError]] = []
    unmapped: list[ValidationError] = []

    for error in errors:
        strategy_id = resolver.resolve(error)
        if strategy_id is None or strategy_id == "refuse":
            unmapped.append(error)
            continue
        mapped.append((error.depth, strategy_id, error))

    # Sort by depth descending, then deterministic-before-llm by strategy
    # prefix. Stable sort preserves the input ordering inside each bucket,
    # which keeps audit logs deterministic.
    mapped.sort(key=lambda t: (-t[0], _strategy_priority(t[1])))

    # Group consecutive entries with the same depth into one batch.
    batches: list[list[PlanEntry]] = []
    current: list[PlanEntry] = []
    current_depth: int | None = None

    for depth, strategy_id, error in mapped:
        if current_depth is None or depth == current_depth:
            current.append((strategy_id, error))
            current_depth = depth
        else:
            batches.append(current)
            current = [(strategy_id, error)]
            current_depth = depth

    if current:
        batches.append(current)

    return DispatchPlan(batches=batches, unmapped=unmapped)


def _strategy_priority(strategy_id: str) -> int:
    """Lower priority runs first. Deterministic strategies come before LLM."""
    if strategy_id.startswith("deterministic."):
        return 0
    if strategy_id == "refuse":
        return 1
    if strategy_id.startswith("llm"):
        return 2
    return 3


def detect_regressions(
    before_errors: list[ValidationError],
    after_errors: list[ValidationError],
    touched_paths: set[str],
) -> list[ValidationError]:
    """Return errors that did not exist before the batch and that fall on a touched path.

    A regression is a *new* error at a path the batch wrote to. New errors
    on untouched paths are not regressions; they were merely uncovered by
    the fix.
    """
    before_set = {(e.code, e.location) for e in before_errors}
    return [
        e
        for e in after_errors
        if (e.code, e.location) not in before_set and e.location in touched_paths
    ]


def is_stuck(prev: list[ValidationError], curr: list[ValidationError]) -> bool:
    """True when two consecutive iterations produced the same error set.

    Triggers loop termination so we do not iterate forever on errors that
    cannot be fixed under the current configuration.
    """
    return _error_signature(prev) == _error_signature(curr)


def _error_signature(errors: list[ValidationError]) -> set[tuple[str, str]]:
    return {(e.code, e.location) for e in errors}


def check_permission(
    action: RepairAction | None,
    permission: str,
    guard: HallucinationGuard,
) -> bool:
    """Return True if the guard grants `permission`.

    Pulled out for testability. The dispatcher consults this before running
    a strategy that declares a `permission` requirement.
    """
    return guard.is_allowed(permission)


def snapshot(resource: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy a resource for rollback. The dispatcher takes a snapshot
    before each batch so a regression can be undone.
    """
    return copy.deepcopy(resource)
