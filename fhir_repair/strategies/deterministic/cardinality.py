"""Unwrap a singleton array where the schema expects a scalar.

A common conversion bug: `Patient.gender = ["male"]` when the spec defines
`gender` as a single value. The validator emits an error pointing at the
scalar-typed path; this strategy detects a length-1 list at that path and
unwraps it.

Refuses if the value is not a list, or if the list has more than one
element (which is a different problem this strategy is not equipped to
solve).
"""

from __future__ import annotations

from typing import Any, Literal

from fhir_repair.core.fhirpath import get_at_path, set_at_path
from fhir_repair.core.models import RepairAction, ValidationError
from fhir_repair.strategies.base import refused

NAME = "deterministic.unwrap_singleton"
VERSION = "1.0.0"
PERMISSION = "allow_reformat"
RISK: Literal["low"] = "low"


def apply(resource: dict[str, Any], error: ValidationError) -> RepairAction:
    """Unwrap a singleton list at `error.location`."""
    before = get_at_path(resource, error.location)

    if not isinstance(before, list):
        return refused(
            error,
            NAME,
            VERSION,
            PERMISSION,
            before,
            "value at path is not a list",
        )

    if len(before) != 1:
        return refused(
            error,
            NAME,
            VERSION,
            PERMISSION,
            before,
            f"list length is {len(before)}; only singleton arrays are unwrapped",
        )

    after = before[0]
    set_at_path(resource, error.location, after)

    return RepairAction(
        error=error,
        strategy=NAME,
        strategy_version=VERSION,
        risk=RISK,
        permission_used=PERMISSION,
        before=before,
        after=after,
        explanation="Unwrapped singleton array to scalar.",
    )
