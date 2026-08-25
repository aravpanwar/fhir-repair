"""Tests for the FHIRPath helper."""

from __future__ import annotations

import pytest

from fhir_repair.core.fhirpath import (
    _parse_simple_path,
    delete_at_path,
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


def test_delete_at_path_removes_top_level_field():
    resource = {"resourceType": "Observation", "status": "final", "dataAbsentReason": {}}
    assert delete_at_path(resource, "Observation.dataAbsentReason") is True
    assert "dataAbsentReason" not in resource
    assert resource["status"] == "final"


def test_delete_at_path_removes_nested_field():
    resource = {
        "resourceType": "Patient",
        "contact": [{"name": {"text": "A"}, "gender": "female"}],
    }
    assert delete_at_path(resource, "Patient.contact[0].gender") is True
    assert resource["contact"][0] == {"name": {"text": "A"}}


def test_delete_at_path_removes_list_entry_and_shifts():
    """A list with a hole is not representable, so entries shift down."""
    resource = {
        "resourceType": "Patient",
        "telecom": [
            {"system": "phone", "value": "1"},
            {"system": "email", "value": "2"},
            {"system": "fax", "value": "3"},
        ],
    }
    assert delete_at_path(resource, "Patient.telecom[1]") is True
    assert [t["value"] for t in resource["telecom"]] == ["1", "3"]


def test_delete_at_path_absent_field_returns_false():
    resource = {"resourceType": "Observation", "status": "final"}
    assert delete_at_path(resource, "Observation.dataAbsentReason") is False
    assert resource == {"resourceType": "Observation", "status": "final"}


def test_delete_at_path_absent_parent_returns_false():
    resource = {"resourceType": "Observation", "status": "final"}
    assert delete_at_path(resource, "Observation.code.coding[0]") is False


def test_delete_at_path_out_of_range_index_returns_false():
    resource = {"resourceType": "Patient", "telecom": [{"value": "1"}]}
    assert delete_at_path(resource, "Patient.telecom[3]") is False
    assert len(resource["telecom"]) == 1


def test_delete_at_path_handles_choice_element_notation():
    resource = {
        "resourceType": "Observation",
        "valueQuantity": {"value": 70.5, "unit": "kg"},
    }
    assert delete_at_path(resource, "Observation.value.ofType(Quantity)") is True
    assert "valueQuantity" not in resource


def test_delete_at_path_rejects_bare_resource_type():
    resource = {"resourceType": "Observation"}
    with pytest.raises(ValueError):
        delete_at_path(resource, "Observation")
