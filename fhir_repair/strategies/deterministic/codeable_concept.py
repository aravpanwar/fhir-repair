"""Wrap a bare Coding where a CodeableConcept is expected.

A frequent conversion bug puts a Coding object directly on a field the spec
types as CodeableConcept:

    "code": {"system": "http://loinc.org", "code": "1234-5"}

The valid shape nests the Coding inside a `coding` array:

    "code": {"coding": [{"system": "http://loinc.org", "code": "1234-5"}]}

This strategy detects a bare Coding at the error location and nests it. Any
`text` on the bare object is lifted to the CodeableConcept level, where it
belongs.

Refuses on:
  - A value that is not an object.
  - An object that already has a `coding` array (it is already a
    CodeableConcept).
  - An object that does not carry a `code` (nothing to anchor a Coding on).
  - An object with keys outside the Coding/CodeableConcept shape, which
    signals some other structure we should not reshape.

This is a deterministic strategy with no IO. It exercises the
`allow_reformat` permission, which is on by default.
"""

from __future__ import annotations

from typing import Any, Literal

from fhir_repair.core.fhirpath import get_at_path, set_at_path
from fhir_repair.core.models import RepairAction, ValidationError
from fhir_repair.strategies.base import refused

NAME = "deterministic.normalize_codeable_concept"
VERSION = "1.0.0"
PERMISSION = "allow_reformat"
RISK: Literal["low"] = "low"

# Fields defined on a FHIR Coding.
_CODING_KEYS = frozenset({"system", "version", "code", "display", "userSelected"})


def apply(resource: dict[str, Any], error: ValidationError) -> RepairAction:
    """Wrap the bare Coding at `error.location` into a CodeableConcept."""
    before = get_at_path(resource, error.location)

    if not isinstance(before, dict):
        return refused(
            error,
            NAME,
            VERSION,
            PERMISSION,
            before,
            "value at path is not an object",
        )

    if "coding" in before:
        return refused(
            error,
            NAME,
            VERSION,
            PERMISSION,
            before,
            "value already has a coding array",
        )

    if "code" not in before:
        return refused(
            error,
            NAME,
            VERSION,
            PERMISSION,
            before,
            "value has no code to anchor a Coding",
        )

    unexpected = set(before) - _CODING_KEYS - {"text"}
    if unexpected:
        return refused(
            error,
            NAME,
            VERSION,
            PERMISSION,
            before,
            f"value has non-Coding keys {sorted(unexpected)}",
        )

    coding = {key: value for key, value in before.items() if key in _CODING_KEYS}
    after: dict[str, Any] = {"coding": [coding]}
    if "text" in before:
        after["text"] = before["text"]

    set_at_path(resource, error.location, after)

    return RepairAction(
        error=error,
        strategy=NAME,
        strategy_version=VERSION,
        risk=RISK,
        permission_used=PERMISSION,
        before=before,
        after=after,
        explanation="Nested bare Coding inside a CodeableConcept coding array.",
    )
