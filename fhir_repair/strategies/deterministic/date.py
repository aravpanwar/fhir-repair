"""Normalize FHIR date wire format to ISO 8601 (YYYY-MM-DD).

Handles common malformations seen in legacy ETL output:

  - Missing zero-padding: `1990-3-5` -> `1990-03-05`
  - Slash separators: `1990/03/05` -> `1990-03-05`
  - US-style ordering when unambiguous: `13/05/1990` -> `1990-05-13`

Refuses on ambiguous input. `01/02/2000` could be Jan 2 or Feb 1; we do not
guess. Also refuses a day the calendar does not have (`1990-2-31`) and a
value that is already ISO 8601, which this strategy cannot improve.

This is a deterministic strategy with no IO. It exercises the
`allow_reformat` permission, which is the lowest-risk permission and on by
default.
"""

from __future__ import annotations

import datetime
import re
from typing import Any, Literal

from fhir_repair.core.fhirpath import get_at_path, set_at_path
from fhir_repair.core.models import RepairAction, ValidationError
from fhir_repair.strategies.base import refused

NAME = "deterministic.normalize_date"
VERSION = "1.1.0"
PERMISSION = "allow_reformat"
RISK: Literal["low"] = "low"

# Patterns we recognise:
#   YYYY-M-D or YYYY/M/D (year-first, separator either)
_YEAR_FIRST_RE = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$")
#   M/D/YYYY or D-M-YYYY (year-last, separator either)
_YEAR_LAST_RE = re.compile(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$")


def apply(resource: dict[str, Any], error: ValidationError) -> RepairAction:
    """Attempt to normalize the date at `error.location`."""
    before = get_at_path(resource, error.location)

    if not isinstance(before, str):
        return _refuse(error, before, "value at path is not a string")

    after = _normalize(before)
    if after is None:
        return _refuse(error, before, f"no valid date in {before!r}")

    # Reporting an unchanged value as a fix would also stop the dispatch
    # chain here, so the strategies after this one never see the error.
    if after == before:
        return _refuse(error, before, f"{before!r} is already an ISO 8601 date")

    set_at_path(resource, error.location, after)

    return RepairAction(
        error=error,
        strategy=NAME,
        strategy_version=VERSION,
        risk=RISK,
        permission_used=PERMISSION,
        before=before,
        after=after,
        explanation=f"Reformatted {before!r} to ISO 8601 {after!r}.",
    )


def _normalize(value: str) -> str | None:
    """Return the ISO 8601 form of `value`, or None if it cannot be normalised."""
    s = value.strip()

    match = _YEAR_FIRST_RE.match(s)
    if match:
        year, month, day = match.groups()
        return _format(year, month, day)

    match = _YEAR_LAST_RE.match(s)
    if match:
        a, b, year = match.groups()
        ai, bi = int(a), int(b)
        # Disambiguate by checking which value cannot be a month.
        if ai > 12 and bi <= 12:
            # a is the day, b is the month: D-M-YYYY
            return _format(year, b, a)
        if bi > 12 and ai <= 12:
            # a is the month, b is the day: M-D-YYYY
            return _format(year, a, b)
        # Both <= 12 means we cannot tell M-D from D-M without a locale
        # signal. Refuse rather than guess.
        return None

    return None


def _format(year: str, month: str, day: str) -> str | None:
    """Return `YYYY-MM-DD` if the components name a real calendar day."""
    try:
        return datetime.date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def _refuse(error: ValidationError, before: Any, reason: str) -> RepairAction:
    return refused(error, NAME, VERSION, PERMISSION, before, reason)
