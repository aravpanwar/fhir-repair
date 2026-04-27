"""Normalize FHIR decimal wire format.

Handles common malformations seen in legacy ETL output:

  - Locale comma as decimal separator: "5,5" -> "5.5"
  - Surrounding whitespace: " 5.5 " -> "5.5"
  - Plus sign prefix: "+5.5" -> "5.5"

Refuses on:
  - Currency or unit prefixes/suffixes ("$5.5", "5.5 mg") because stripping
    them silently could discard clinical meaning.
  - Scientific notation, which FHIR R4 actually permits, so the validator
    should not have flagged it.
  - Multiple decimal separators or otherwise unparseable strings.

This is a deterministic strategy with no IO. It exercises the
`allow_reformat` permission, which is on by default.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fhir_repair.core.fhirpath import get_at_path, set_at_path
from fhir_repair.core.models import RepairAction, ValidationError
from fhir_repair.strategies.base import refused

NAME = "deterministic.normalize_decimal"
VERSION = "1.0.0"
PERMISSION = "allow_reformat"
RISK: Literal["low"] = "low"

# Matches a number that may have a leading sign, an integer part, and an
# optional decimal portion using either `.` or `,`. Refuses anything with
# letters or symbols beyond those.
_NUMERIC_RE = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")


def apply(resource: dict[str, Any], error: ValidationError) -> RepairAction:
    """Attempt to normalize the decimal at `error.location`."""
    before = get_at_path(resource, error.location)

    if isinstance(before, (int, float)):
        # Already a numeric type. The validator likely complained about
        # serialisation, not value; we cannot meaningfully reformat a number
        # that is not a string.
        return refused(
            error,
            NAME,
            VERSION,
            PERMISSION,
            before,
            "value at path is already numeric, not a malformed string",
        )

    if not isinstance(before, str):
        return refused(
            error,
            NAME,
            VERSION,
            PERMISSION,
            before,
            "value at path is not a string",
        )

    after = _normalize(before)
    if after is None:
        return refused(
            error,
            NAME,
            VERSION,
            PERMISSION,
            before,
            f"could not normalise {before!r} as a decimal",
        )

    set_at_path(resource, error.location, after)

    return RepairAction(
        error=error,
        strategy=NAME,
        strategy_version=VERSION,
        risk=RISK,
        permission_used=PERMISSION,
        before=before,
        after=after,
        explanation=f"Reformatted {before!r} to canonical decimal {after!r}.",
    )


def _normalize(value: str) -> str | None:
    """Return the canonical decimal form of `value`, or None if unparseable.

    "Canonical" here means: ASCII period as the decimal separator, no
    surrounding whitespace, no leading plus sign, no thousands separators.
    The numeric value is preserved bit-for-bit by going through `Decimal`.
    """
    s = value.strip()

    if not _NUMERIC_RE.match(s):
        return None

    # Replace a single comma with a period. We already rejected anything
    # that contains both via the regex (which only allows one separator).
    s = s.replace(",", ".")

    # Drop a leading plus sign; `Decimal` accepts it but FHIR's canonical
    # form omits it.
    if s.startswith("+"):
        s = s[1:]

    try:
        # Round-trip through Decimal to confirm the string parses cleanly.
        # We return the original string (sans plus and comma) rather than
        # str(Decimal(s)) because Decimal normalises trailing zeros, which
        # the FHIR spec preserves as significant precision.
        Decimal(s)
    except InvalidOperation:
        return None

    return s
