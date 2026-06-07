"""Tests for the benchmark mutation functions.

Each mutation pairs with a deterministic repair strategy: the mutation
corrupts a valid resource and the matching strategy restores it. These
tests check the corruption side, plus that the recorded ground truth
(original value) is what a repair would need to reproduce.
"""

from __future__ import annotations

import random

from benchmark import mutate

_RNG = random.Random(0)


def test_decimal_format_uses_locale_comma():
    resource = {
        "resourceType": "Observation",
        "valueQuantity": {"value": 70.5, "unit": "kg"},
    }
    result = mutate.mutate_decimal_format(resource, _RNG)
    assert result is not None
    assert result.resource["valueQuantity"]["value"] == "70,5"
    assert result.original_value == 70.5
    # Other fields are left intact.
    assert result.resource["valueQuantity"]["unit"] == "kg"
    # The source resource is not mutated in place.
    assert resource["valueQuantity"]["value"] == 70.5


def test_decimal_format_skips_integer_value():
    resource = {
        "resourceType": "Observation",
        "valueQuantity": {"value": 70},
    }
    assert mutate.mutate_decimal_format(resource, _RNG) is None


def test_decimal_format_skips_without_quantity():
    resource = {"resourceType": "Patient", "gender": "male"}
    assert mutate.mutate_decimal_format(resource, _RNG) is None


def test_invalid_code_binding_corrupts_gender():
    resource = {"resourceType": "Patient", "gender": "male"}
    result = mutate.mutate_invalid_code_binding(resource, _RNG)
    assert result is not None
    assert result.resource["gender"] == "M"
    assert result.original_value == "male"
    assert result.location == "Patient.gender"


def test_invalid_code_binding_corrupts_status():
    resource = {"resourceType": "Observation", "status": "final"}
    result = mutate.mutate_invalid_code_binding(resource, _RNG)
    assert result is not None
    assert result.resource["status"] == "F"
    assert result.original_value == "final"


def test_invalid_code_binding_skips_unknown_field():
    resource = {"resourceType": "Patient", "active": True}
    assert mutate.mutate_invalid_code_binding(resource, _RNG) is None


def test_invariant_violation_adds_data_absent_reason():
    resource = {
        "resourceType": "Observation",
        "status": "final",
        "valueQuantity": {"value": 70.5},
    }
    result = mutate.mutate_invariant_violation(resource, _RNG)
    assert result is not None
    assert "dataAbsentReason" in result.resource
    assert "valueQuantity" in result.resource
    assert result.original_value is None


def test_invariant_violation_skips_without_value():
    resource = {"resourceType": "Observation", "status": "final"}
    assert mutate.mutate_invariant_violation(resource, _RNG) is None


def test_invariant_violation_skips_non_observation():
    resource = {"resourceType": "Patient", "valueQuantity": {"value": 1.0}}
    assert mutate.mutate_invariant_violation(resource, _RNG) is None


def test_telecom_format_prepends_scheme():
    resource = {
        "resourceType": "Patient",
        "telecom": [{"system": "phone", "value": "555-0100"}],
    }
    result = mutate.mutate_telecom_format(resource, _RNG)
    assert result is not None
    assert result.resource["telecom"][0]["value"] == "tel:555-0100"
    assert result.original_value == "555-0100"


def test_telecom_format_skips_when_already_prefixed():
    resource = {
        "resourceType": "Patient",
        "telecom": [{"system": "phone", "value": "tel:555-0100"}],
    }
    assert mutate.mutate_telecom_format(resource, _RNG) is None


def test_telecom_format_skips_without_telecom():
    resource = {"resourceType": "Patient", "gender": "male"}
    assert mutate.mutate_telecom_format(resource, _RNG) is None


def test_identifier_system_replaces_canonical_with_label():
    resource = {
        "resourceType": "Patient",
        "identifier": [{"system": "http://hl7.org/fhir/sid/us-ssn", "value": "999-99-9999"}],
    }
    result = mutate.mutate_identifier_system(resource, _RNG)
    assert result is not None
    assert result.resource["identifier"][0]["system"] == "SSN"
    assert result.original_value == "http://hl7.org/fhir/sid/us-ssn"


def test_identifier_system_skips_unknown_system():
    resource = {
        "resourceType": "Patient",
        "identifier": [{"system": "http://hospital.example/mrn", "value": "1"}],
    }
    assert mutate.mutate_identifier_system(resource, _RNG) is None


def test_identifier_system_skips_without_identifier():
    resource = {"resourceType": "Patient", "gender": "male"}
    assert mutate.mutate_identifier_system(resource, _RNG) is None


def test_every_registered_mutation_is_callable():
    # Guards against a registry entry that points at a missing function.
    for name, fn in mutate.MUTATIONS.items():
        assert callable(fn), name
