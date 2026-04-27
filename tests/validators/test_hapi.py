"""Tests for the HAPI REST validator adapter.

Unit tests use respx to mock HAPI's HTTP responses. Integration tests
hit a real HAPI server and are gated behind the `integration` marker.
"""

from __future__ import annotations

import os

import httpx
import pytest
import respx

from fhir_repair.validators.hapi import HapiRestValidator


def _outcome(*issues: dict) -> dict:
    return {"resourceType": "OperationOutcome", "issue": list(issues)}


@respx.mock
def test_validate_returns_empty_list_for_valid_resource():
    respx.post("http://example/fhir/Patient/$validate").mock(
        return_value=httpx.Response(200, json=_outcome())
    )
    validator = HapiRestValidator(base_url="http://example/fhir")
    try:
        errors = validator.validate({"resourceType": "Patient"})
    finally:
        validator.close()

    assert errors == []


@respx.mock
def test_validate_normalizes_issues():
    respx.post("http://example/fhir/Patient/$validate").mock(
        return_value=httpx.Response(
            200,
            json=_outcome(
                {
                    "severity": "error",
                    "code": "invalid-date-format",
                    "diagnostics": "bad date",
                    "expression": ["Patient.birthDate"],
                }
            ),
        )
    )
    validator = HapiRestValidator(base_url="http://example/fhir")
    try:
        errors = validator.validate({"resourceType": "Patient"})
    finally:
        validator.close()

    assert len(errors) == 1
    assert errors[0].code == "invalid-date-format"
    assert errors[0].severity == "error"
    assert errors[0].location == "Patient.birthDate"


@respx.mock
def test_validate_drops_information_severity():
    respx.post("http://example/fhir/Patient/$validate").mock(
        return_value=httpx.Response(
            200,
            json=_outcome(
                {
                    "severity": "information",
                    "code": "informational",
                    "diagnostics": "FYI",
                    "expression": ["Patient"],
                },
                {
                    "severity": "error",
                    "code": "real-error",
                    "diagnostics": "x",
                    "expression": ["Patient.x"],
                },
            ),
        )
    )
    validator = HapiRestValidator(base_url="http://example/fhir")
    try:
        errors = validator.validate({"resourceType": "Patient"})
    finally:
        validator.close()

    assert len(errors) == 1
    assert errors[0].code == "real-error"


def test_validate_rejects_resource_without_resourceType():
    validator = HapiRestValidator(base_url="http://example/fhir")
    try:
        with pytest.raises(ValueError, match="resourceType"):
            validator.validate({})
    finally:
        validator.close()


@pytest.mark.integration
def test_validate_against_running_hapi():
    """Smoke test against a real HAPI server. Requires HAPI_BASE_URL env var."""
    base_url = os.environ.get("HAPI_BASE_URL")
    if not base_url:
        pytest.skip("HAPI_BASE_URL not set")

    validator = HapiRestValidator(base_url=base_url)
    try:
        # Minimal valid Patient. Some HAPI configurations may emit
        # warnings; we only assert the call succeeds.
        validator.validate({"resourceType": "Patient", "id": "smoke"})
    finally:
        validator.close()
