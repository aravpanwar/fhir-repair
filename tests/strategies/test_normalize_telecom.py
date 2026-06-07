"""Tests for the normalize_telecom deterministic strategy."""

from __future__ import annotations

import pytest

from fhir_repair.core.models import ValidationError
from fhir_repair.strategies.deterministic import telecom as telecom_strategy


def _error(location: str = "Patient.telecom[0]") -> ValidationError:
    return ValidationError(
        code="processing",
        severity="error",
        location=location,
        message="",
    )


@pytest.mark.parametrize(
    "system, raw, expected",
    [
        ("phone", "tel:+1-555-0100", "+1-555-0100"),
        ("phone", " +1-555-0100 ", "+1-555-0100"),
        ("email", "mailto:jane@example.com", "jane@example.com"),
        ("email", "MAILTO:jane@example.com", "jane@example.com"),
        ("fax", "fax:+1-555-0199", "+1-555-0199"),
        ("sms", "sms:+1-555-0123", "+1-555-0123"),
    ],
)
def test_strips_redundant_scheme(system, raw, expected):
    resource = {
        "resourceType": "Patient",
        "telecom": [{"system": system, "value": raw}],
    }
    action = telecom_strategy.apply(resource, _error())
    assert action.risk == "low"
    assert resource["telecom"][0]["value"] == expected
    # Other ContactPoint fields are preserved.
    assert resource["telecom"][0]["system"] == system


def test_preserves_sibling_fields():
    resource = {
        "resourceType": "Patient",
        "telecom": [{"system": "phone", "value": "tel:+1-555-0100", "use": "home"}],
    }
    action = telecom_strategy.apply(resource, _error())
    assert action.risk == "low"
    assert resource["telecom"][0]["use"] == "home"


def test_already_canonical_is_refused():
    resource = {
        "resourceType": "Patient",
        "telecom": [{"system": "phone", "value": "+1-555-0100"}],
    }
    action = telecom_strategy.apply(resource, _error())
    assert action.risk == "refused"
    assert resource["telecom"][0]["value"] == "+1-555-0100"


def test_conflicting_scheme_is_refused():
    # system says phone but the value carries an email scheme. We do not
    # guess which side is correct.
    resource = {
        "resourceType": "Patient",
        "telecom": [{"system": "phone", "value": "mailto:jane@example.com"}],
    }
    action = telecom_strategy.apply(resource, _error())
    assert action.risk == "refused"
    assert resource["telecom"][0]["value"] == "mailto:jane@example.com"


def test_missing_system_is_refused():
    resource = {
        "resourceType": "Patient",
        "telecom": [{"value": "tel:+1-555-0100"}],
    }
    action = telecom_strategy.apply(resource, _error())
    assert action.risk == "refused"


def test_non_object_is_refused():
    resource = {
        "resourceType": "Patient",
        "telecom": ["tel:+1-555-0100"],
    }
    action = telecom_strategy.apply(resource, _error("Patient.telecom[0]"))
    assert action.risk == "refused"


def test_strategy_metadata_is_well_formed():
    assert telecom_strategy.NAME == "deterministic.normalize_telecom"
    assert telecom_strategy.PERMISSION == "allow_reformat"
    assert telecom_strategy.RISK == "low"
