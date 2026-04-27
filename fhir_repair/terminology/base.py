"""TerminologyService Protocol.

Used by code-binding strategies to validate and look up codes. Two
operations are exposed:

  - `validate_code(system, code)`: is this code a member of this CodeSystem?
  - `lookup_in_value_set(value_set_url, term)`: search a ValueSet for a code
    matching `term` (free text or an exact code).

Adapters wrap a real terminology source (a HAPI server, tx.fhir.org, a
commercial service, or a local index file) behind this interface. The
default `LocalTerminology` adapter handles the small, stable FHIR-internal
enumerations without any network call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class ValidateCodeResult:
    """Result of a `validate_code` call."""

    valid: bool
    display: str | None = None
    message: str | None = None


@dataclass
class CodeMatch:
    """A candidate code returned by a ValueSet lookup."""

    system: str
    code: str
    display: str
    confidence: float = 1.0


@runtime_checkable
class TerminologyService(Protocol):
    """Validates codes and searches ValueSets."""

    def validate_code(
        self,
        system: str,
        code: str,
    ) -> ValidateCodeResult:
        """Check whether `code` is in the given CodeSystem."""
        ...

    def lookup_in_value_set(
        self,
        value_set_url: str,
        term: str,
    ) -> list[CodeMatch]:
        """Search a ValueSet for codes matching the term.

        `term` may be a free-text string (e.g., user input "M") or an
        exact code (e.g., "male"). Adapters return zero or more candidate
        matches ordered by confidence descending.
        """
        ...
