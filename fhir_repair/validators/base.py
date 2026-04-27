"""Validator Protocol.

A validator wraps a FHIR validation engine and returns a uniform
`list[ValidationError]`. Engine-specific quirks (HAPI's HTTP error shapes,
Firely's command-line conventions) stay inside the adapter.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from fhir_repair.core.models import ValidationError


@runtime_checkable
class Validator(Protocol):
    """Validates a FHIR resource and returns its issues."""

    def validate(
        self,
        resource: dict[str, Any],
        profile: str | None = None,
    ) -> list[ValidationError]:
        """Validate `resource`, optionally against `profile` (canonical URL).

        Returns an empty list when the resource is valid.
        """
        ...

    def close(self) -> None:
        """Release any held resources (HTTP clients, subprocess handles)."""
        ...
