"""Tests for the FHIRPath helper."""

from __future__ import annotations

import pytest

from fhir_repair.core.fhirpath import (
    _parse_simple_path,
    get_at_path,
    set_at_path,
)


def test_parse_simple_attribute_path():
    assert _parse_simple_path("Patient.birthDate") == ["Patient", "birthDate"]


def test_parse_indexed_path():
    parsed = _parse_simple_path("Patient.contact[0].telecom[1].value")
    assert parsed == ["Patient", ("contact", 0), ("telecom", 1), "value"]


def test_parse_invalid_path():
    with pytest.raises(ValueError):
        _parse_simple_path("Patient.bad-segment!")


def test_get_at_simple_path(patient_invalid_date):
    assert get_at_path(patient_invalid_date, "Patient.birthDate") == "1990-3-5"


def test_get_at_missing_path_returns_none(patient_invalid_date):
    assert get_at_path(patient_invalid_date, "Patient.deceasedBoolean") is None


def test_set_at_simple_path(patient_invalid_date):
    set_at_path(patient_invalid_date, "Patient.birthDate", "1990-03-05")
    assert patient_invalid_date["birthDate"] == "1990-03-05"


def test_set_at_indexed_path(patient_valid):
    set_at_path(patient_valid, "Patient.telecom[0].value", "555-0199")
    assert patient_valid["telecom"][0]["value"] == "555-0199"


def test_set_at_path_drops_resource_type_segment():
    resource = {"resourceType": "Patient", "birthDate": "wrong"}
    set_at_path(resource, "Patient.birthDate", "1990-03-05")
    assert resource["birthDate"] == "1990-03-05"


def test_get_at_path_handles_choice_element_notation():
    resource = {
        "resourceType": "Condition",
        "onsetDateTime": "2020-06-22",
    }
    # HAPI emits FHIRPath ofType notation for choice elements; we should
    # canonicalise to the JSON property name and find the value.
    assert get_at_path(resource, "Condition.onset.ofType(dateTime)") == "2020-06-22"


def test_set_at_path_handles_choice_element_notation():
    resource = {
        "resourceType": "Observation",
        "valueQuantity": {"value": 70.5, "unit": "kg"},
    }
    set_at_path(resource, "Observation.value.ofType(Quantity).value", 71.0)
    assert resource["valueQuantity"]["value"] == 71.0
