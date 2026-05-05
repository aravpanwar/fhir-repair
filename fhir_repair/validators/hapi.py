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

        HAPI's $validate operation uses HTTP status to signal outcome:
          - 200: resource is valid (body may contain warnings/info)
          - 422: validation errors found (body contains the error list)

        Both responses carry an OperationOutcome we need to parse. Other
        non-2xx statuses (5xx, 4xx other than 422) indicate a server-side
        problem unrelated to the resource's validity, and propagate.
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

        # 422 is the spec-compliant "validation failed" status from $validate;
        # parse its body as an OperationOutcome rather than treating it as
        # a transport error.
        if response.status_code != 422:
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
        # Only `error` and `fatal` represent failed validation in the FHIR
        # sense (HAPI returns 200 with warnings still present). `warning`
        # and `information` are guidance, not failures, and cannot be
        # uniformly fixed; surfacing them as repair candidates produces
        # spurious unresolved errors and noisy audit logs.
        severity = _SEVERITY_MAP.get(issue.get("severity", "error"), "error")
        if severity in ("warning", "information"):
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
