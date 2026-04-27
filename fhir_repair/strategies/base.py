"""Strategy Protocol and helpers.

A strategy takes a resource and a single ValidationError, returns a
`RepairAction` (applied or refused), and declares which hallucination_guard
permission it requires. Pure deterministic strategies have no IO; LLM
strategies inject an LLMProvider and possibly a TerminologyService.

The Protocol is `runtime_checkable` so the registry can validate
implementations at registration time.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from fhir_repair.core.models import RepairAction, ValidationError


@runtime_checkable
class Strategy(Protocol):
    """A repair strategy that addresses one validation error.

    Required attributes:
      - `name`: fully qualified id, e.g. `deterministic.normalize_date`.
      - `version`: SemVer of this implementation. Bumped when behaviour
        changes in a way a benchmark consumer would want to see.
      - `permission`: which `HallucinationGuard` permission this strategy
        exercises. The dispatcher checks this before invoking.
      - `risk`: default risk classification for the action this strategy
        produces. Individual actions may override (typically downward,
        e.g. a refusal).
    """

    name: str
    version: str
    permission: str
    risk: str

    def apply(
        self,
        resource: dict[str, Any],
        error: ValidationError,
    ) -> RepairAction:
        """Attempt the fix. Mutates `resource` on success.

        Always returns a `RepairAction`. On failure (precondition not met,
        ambiguous input, LLM refused), returns one with `risk="refused"`
        and a reason in `explanation`.
        """
        ...


def refused(
    error: ValidationError,
    name: str,
    version: str,
    permission: str,
    before: Any,
    reason: str,
) -> RepairAction:
    """Build a `risk=refused` RepairAction.

    Helper to keep refusal sites uniform across strategy modules.
    """
    return RepairAction(
        error=error,
        strategy=name,
        strategy_version=version,
        risk="refused",
        permission_used=permission,
        before=before,
        after=before,
        explanation=f"Refused: {reason}.",
    )
