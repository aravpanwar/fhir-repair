"""Normalize a FHIR ContactPoint value to match its system.

Legacy systems often store telecom values with the scheme baked into the
string, so a phone number arrives as `tel:+1-555-0100` and an email as
`mailto:jane@example.com`. The FHIR wire format keeps the scheme in the
`system` field and the bare value in `value`, so the prefix is redundant
and the validator rejects it.

This strategy strips the redundant scheme prefix when it matches the
declared `system`, and trims surrounding whitespace.

Refuses on:
  - A value at the path that is not a ContactPoint object.
  - A ContactPoint with no string `value` or no `system`.
  - A scheme prefix that disagrees with `system` (e.g. system `phone` but
    value `mailto:...`). Guessing which field is wrong could change the
    contact method, so we leave it for review.
  - A value that needs no change.

This is a deterministic strategy with no IO. It exercises the
`allow_reformat` permission, which is on by default.
"""

from __future__ import annotations

from typing import Any, Literal

from fhir_repair.core.fhirpath import get_at_path, set_at_path
from fhir_repair.core.models import RepairAction, ValidationError
from fhir_repair.strategies.base import refused

NAME = "deterministic.normalize_telecom"
VERSION = "1.0.0"
PERMISSION = "allow_reformat"
RISK: Literal["low"] = "low"

# Scheme prefixes that are redundant with a given ContactPoint.system. Each
# system maps to the prefixes we will strip from its value.
_REDUNDANT_PREFIXES: dict[str, tuple[str, ...]] = {
    "phone": ("tel:",),
    "fax": ("fax:", "tel:"),
    "sms": ("sms:", "tel:"),
    "email": ("mailto:",),
}


def apply(resource: dict[str, Any], error: ValidationError) -> RepairAction:
    """Normalize the ContactPoint at `error.location`."""
    before = get_at_path(resource, error.location)

    if not isinstance(before, dict):
        return refused(
            error,
            NAME,
            VERSION,
            PERMISSION,
            before,
            "value at path is not a ContactPoint object",
        )

    system = before.get("system")
    value = before.get("value")

    if not isinstance(system, str) or not isinstance(value, str):
        return refused(
            error,
            NAME,
            VERSION,
            PERMISSION,
            before,
            "ContactPoint is missing a string system or value",
        )

    new_value = _normalize_value(system, value)
    if new_value is None:
        return refused(
            error,
            NAME,
            VERSION,
            PERMISSION,
            before,
            f"value {value!r} does not match a redundant prefix for system {system!r}",
        )

    if new_value == value:
        return refused(
            error,
            NAME,
            VERSION,
            PERMISSION,
            before,
            "ContactPoint value is already canonical",
        )

    after = dict(before)
    after["value"] = new_value
    set_at_path(resource, error.location, after)

    return RepairAction(
        error=error,
        strategy=NAME,
        strategy_version=VERSION,
        risk=RISK,
        permission_used=PERMISSION,
        before=before,
        after=after,
        explanation=f"Stripped redundant scheme and trimmed {value!r} to {new_value!r}.",
    )


def _normalize_value(system: str, value: str) -> str | None:
    """Return the cleaned telecom value, or None if the prefix conflicts.

    None means the value carries a scheme that disagrees with `system`; an
    unchanged string means there was nothing redundant to strip (the caller
    treats that as a no-op refusal).
    """
    trimmed = value.strip()
    lowered = trimmed.lower()

    prefixes = _REDUNDANT_PREFIXES.get(system, ())

    # If the value carries one of this system's own schemes, strip it.
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return trimmed[len(prefix) :].strip()

    # A scheme that belongs only to a different system signals the
    # system/value pair may be wrong. Refuse rather than guess which side
    # to trust. Schemes shared with the declared system (e.g. `tel:` across
    # phone/fax/sms) were already handled above.
    for other_prefix in _all_foreign_prefixes(system):
        if lowered.startswith(other_prefix):
            return None

    return trimmed


def _all_foreign_prefixes(system: str) -> set[str]:
    """Scheme prefixes that belong to some system other than `system`."""
    own = set(_REDUNDANT_PREFIXES.get(system, ()))
    foreign: set[str] = set()
    for other_system, other_prefixes in _REDUNDANT_PREFIXES.items():
        if other_system == system:
            continue
        foreign.update(other_prefixes)
    return foreign - own
