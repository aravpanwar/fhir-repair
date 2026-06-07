"""Canonicalize a well-known Identifier.system to its FHIR URI.

Source systems frequently record an identifier system as a short label
(`SSN`, `NPI`) or a non-canonical URN instead of the canonical FHIR system
URI. The validator rejects the label because the bound system is a URI.

This strategy maps a small set of universally defined US identifier systems
to their canonical URIs, and trims surrounding whitespace from a value that
is otherwise already a URI.

The synonym table is intentionally small. It only contains systems with a
single, nationally defined canonical URI, where the mapping is a fact rather
than a guess. Local systems (MRN, account number) have no universal URI and
are never mapped; those route to review.

Refuses on:
  - A value that is not a string.
  - A label that is not in the synonym table and needs no whitespace fix.

This is a deterministic strategy with no IO. It exercises the
`allow_reformat` permission, which is on by default.
"""

from __future__ import annotations

from typing import Any, Literal

from fhir_repair.core.fhirpath import get_at_path, set_at_path
from fhir_repair.core.models import RepairAction, ValidationError
from fhir_repair.strategies.base import refused

NAME = "deterministic.canonicalize_identifier_system"
VERSION = "1.0.0"
PERMISSION = "allow_reformat"
RISK: Literal["low"] = "low"

# Lowercased label -> canonical FHIR system URI. Only nationally defined
# systems with a single canonical URI belong here.
_SYNONYMS: dict[str, str] = {
    "ssn": "http://hl7.org/fhir/sid/us-ssn",
    "social security number": "http://hl7.org/fhir/sid/us-ssn",
    "urn:ssn": "http://hl7.org/fhir/sid/us-ssn",
    "npi": "http://hl7.org/fhir/sid/us-npi",
    "national provider identifier": "http://hl7.org/fhir/sid/us-npi",
    "mbi": "http://hl7.org/fhir/sid/us-mbi",
    "medicare beneficiary identifier": "http://hl7.org/fhir/sid/us-mbi",
}


def apply(resource: dict[str, Any], error: ValidationError) -> RepairAction:
    """Canonicalize the identifier system at `error.location`."""
    before = get_at_path(resource, error.location)

    if not isinstance(before, str):
        return refused(
            error,
            NAME,
            VERSION,
            PERMISSION,
            before,
            "value at path is not a string",
        )

    after = _canonicalize(before)
    if after is None or after == before:
        return refused(
            error,
            NAME,
            VERSION,
            PERMISSION,
            before,
            f"no canonical system known for {before!r}",
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
        explanation=f"Canonicalized identifier system {before!r} to {after!r}.",
    )


def _canonicalize(value: str) -> str | None:
    """Return the canonical system URI, the trimmed value, or None.

    None means there was nothing to do and no known mapping; the caller
    treats that as a refusal.
    """
    trimmed = value.strip()

    mapped = _SYNONYMS.get(trimmed.lower())
    if mapped is not None:
        return mapped

    # An already-URI value that only differs by surrounding whitespace.
    if trimmed != value:
        return trimmed

    return None
