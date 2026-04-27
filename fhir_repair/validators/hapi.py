"""HAPI FHIR validator REST adapter.

Talks to a running HAPI server's `$validate` operation and normalizes the
returned `OperationOutcome` into our internal `ValidationError` type.

Run a HAPI server locally with:

    docker compose -f docker/docker-compose.yml up -d hapi

The HAPI version is pinned in docker-compose.yml; bumping it should be a
deliberate change paired with a benchmark re-run, since validator behaviour
changes within minor releases.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from fhir_repair.core.models import ValidationError

# Default URL points to the docker-compose service on a developer machine.
# Override via environment variable or the constructor for any other
# deployment.
DEFAULT_HAPI_URL = os.environ.get("HAPI_BASE_URL", "http://localhost:8080/fhir")

# Severity values that the FHIR spec actually uses, mapped to our internal
# severity literal. Anything else falls through to "error" because we do
# not want to silently drop unrecognised severities.
_SEVERITY_MAP: dict[str, str] = {
    "fatal": "error",
    "error": "error",
    "warning": "warning",
    "information": "information",
}


class HapiRestValidator:
    """Synchronous REST adapter for HAPI's `$validate` operation."""

    def __init__(
        self,
        base_url: str = DEFAULT_HAPI_URL,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ):
        self._base_url = base_url
        # Allowing the caller to inject a client makes testing easier
        # (respx can mock it) and lets advanced deployers configure TLS,
        # proxies, retries, etc.
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout)
        self._owns_client = client is None

    def validate(
        self,
        resource: dict[str, Any],
        profile: str | None = None,
    ) -> list[ValidationError]:
        """Validate `resource` and return its issues.

        Raises `httpx.HTTPError` if the server returns a non-2xx response
        for reasons other than validation failure. Validation failures
        themselves come back as 200 with an OperationOutcome body, which
        is normal HAPI behaviour.
        """
        resource_type = resource.get("resourceType")
        if not resource_type:
            raise ValueError("Resource has no 'resourceType' field")

        params = {"profile": profile} if profile else None
        response = self._client.post(
            f"/{resource_type}/$validate",
            json=resource,
            params=params,
        )

        # HAPI returns 200 even when validation fails; the outcome contains
        # the issues. A non-2xx status indicates a server problem, not a
        # validation problem, so let httpx raise.
        response.raise_for_status()

        outcome = response.json()
        return _parse_outcome(outcome)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _parse_outcome(outcome: dict[str, Any]) -> list[ValidationError]:
    """Convert an `OperationOutcome` resource to our internal error list.

    HAPI fields used:
      - `issue[].severity`
      - `issue[].code`
      - `issue[].diagnostics`  (human-readable message)
      - `issue[].expression[]` (FHIRPath locations; we collapse to first)
      - `issue[].location[]`   (legacy; only used as fallback)
    """
    errors: list[ValidationError] = []

    for issue in outcome.get("issue", []):
        # Skip purely informational issues; they do not represent fixable
        # errors. Warnings are kept because they often signal profile
        # conformance problems users want to address.
        severity = _SEVERITY_MAP.get(issue.get("severity", "error"), "error")
        if severity == "information":
            continue

        location = ""
        if issue.get("expression"):
            location = issue["expression"][0]
        elif issue.get("location"):
            # Older HAPI releases put the FHIRPath in `location` instead of
            # `expression`. Take the first entry as a fallback.
            location = issue["location"][0]

        errors.append(
            ValidationError(
                code=issue.get("code", "unknown"),
                severity=severity,  # type: ignore[arg-type]
                location=location,
                message=issue.get("diagnostics", ""),
            )
        )

    return errors
