"""Tests for the canonicalize_identifier_system deterministic strategy."""

from __future__ import annotations

import pytest

from fhir_repair.core.models import ValidationError
from fhir_repair.strategies.deterministic import identifier as identifier_strategy


def _error(location: str = "Patient.identifier[0].system") -> ValidationError:
    return ValidationError(
        code="processing",
        severity="error",
        location=location,
        message="",
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("SSN", "http://hl7.org/fhir/sid/us-ssn"),
        ("ssn", "http://hl7.org/fhir/sid/us-ssn"),
        ("Social Security Number", "http://hl7.org/fhir/sid/us-ssn"),
        ("NPI", "http://hl7.org/fhir/sid/us-npi"),
        ("MBI", "http://hl7.org/fhir/sid/us-mbi"),
        (" http://hl7.org/fhir/sid/us-ssn ", "http://hl7.org/fhir/sid/us-ssn"),
    ],
)
def test_canonicalizes_known_systems(raw, expected):
    resource = {
        "resourceType": "Patient",
        "identifier": [{"system": raw, "value": "123"}],
    }
    action = identifier_strategy.apply(resource, _error())
    assert action.risk == "low"
    assert resource["identifier"][0]["system"] == expected


def test_unknown_label_is_refused():
    # A local system label has no universal URI; we do not invent one.
    resource = {
        "resourceType": "Patient",
        "identifier": [{"system": "MRN", "value": "123"}],
    }
    action = identifier_strategy.apply(resource, _error())
    assert action.risk == "refused"
    assert resource["identifier"][0]["system"] == "MRN"


def test_already_canonical_is_refused():
    resource = {
        "resourceType": "Patient",
        "identifier": [{"system": "http://hl7.org/fhir/sid/us-ssn", "value": "123"}],
    }
    action = identifier_strategy.apply(resource, _error())
    assert action.risk == "refused"


def test_non_string_is_refused():
    resource = {
        "resourceType": "Patient",
        "identifier": [{"system": ["SSN"], "value": "123"}],
    }
    action = identifier_strategy.apply(resource, _error())
    assert action.risk == "refused"


def test_strategy_metadata_is_well_formed():
    assert identifier_strategy.NAME == "deterministic.canonicalize_identifier_system"
    assert identifier_strategy.PERMISSION == "allow_reformat"
    assert identifier_strategy.RISK == "low"
