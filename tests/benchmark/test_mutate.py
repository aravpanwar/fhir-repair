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


def test_identifier_system_finds_canonical_behind_other_identifiers():
    # Real Synthea patients put the generator id first and the canonical
    # us-ssn system third, so only checking identifier[0] found nothing.
    resource = {
        "resourceType": "Patient",
        "identifier": [
            {"system": "https://github.com/synthetichealth/synthea", "value": "abc"},
            {"system": "http://hospital.smarthealthit.org", "value": "def"},
            {"system": "http://hl7.org/fhir/sid/us-ssn", "value": "999-99-9999"},
        ],
    }
    result = mutate.mutate_identifier_system(resource, _RNG)
    assert result is not None
    assert result.resource["identifier"][2]["system"] == "SSN"
    assert result.location == "Patient.identifier[2].system"
    assert result.original_value == "http://hl7.org/fhir/sid/us-ssn"
    # Earlier identifiers are untouched.
    assert result.resource["identifier"][0]["system"] == (
        "https://github.com/synthetichealth/synthea"
    )


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


def test_mutate_corpus_skips_non_resource_json(tmp_path):
    # Corpus directories collect manifests and notes. A JSON array used to
    # crash the run partway through instead of being skipped.
    import json

    valid = tmp_path / "valid"
    valid.mkdir()
    (valid / "notes.json").write_text(json.dumps([{"file": "x"}]), encoding="utf-8")
    (valid / "Patient-001.json").write_text(
        json.dumps({"resourceType": "Patient", "gender": "male", "birthDate": "1980-04-11"}),
        encoding="utf-8",
    )

    manifests = mutate.mutate_corpus(valid, tmp_path / "mutated")

    assert manifests
    assert all("Patient-001" in m["valid_path"] for m in manifests)


# Interpretive mutation classes. Each corrupts a value that is readable but
# not mechanically reversible, and each was verified to raise a real HAPI
# error (unlike telecom_format, which base R4 accepts).


def test_unit_mismatch_spells_out_the_ucum_code():
    resource = {
        "resourceType": "Observation",
        "valueQuantity": {"value": 69.64, "unit": "mg/dL", "code": "mg/dL"},
    }
    result = mutate.mutate_unit_mismatch(resource, _RNG)
    assert result is not None
    assert result.resource["valueQuantity"]["code"] == "milligram per deciliter"
    assert result.original_value == "mg/dL"
    # The human-readable `unit` is untouched; only the coded form breaks.
    assert result.resource["valueQuantity"]["unit"] == "mg/dL"


def test_unit_mismatch_skips_unknown_unit():
    resource = {"resourceType": "Observation", "valueQuantity": {"value": 1, "code": "furlong"}}
    assert mutate.mutate_unit_mismatch(resource, _RNG) is None


def test_unit_mismatch_skips_without_quantity():
    assert mutate.mutate_unit_mismatch({"resourceType": "Patient"}, _RNG) is None


def test_date_precision_adds_a_time_component():
    resource = {"resourceType": "Patient", "birthDate": "1975-08-23"}
    result = mutate.mutate_date_precision(resource, _RNG)
    assert result is not None
    assert result.resource["birthDate"] == "1975-08-23T00:00:00Z"
    assert result.original_value == "1975-08-23"


def test_date_precision_skips_an_already_precise_date():
    resource = {"resourceType": "Patient", "birthDate": "1975-08-23T00:00:00Z"}
    assert mutate.mutate_date_precision(resource, _RNG) is None


def test_bad_comparator_adds_a_non_valueset_symbol():
    resource = {"resourceType": "Observation", "valueQuantity": {"value": 69.64}}
    result = mutate.mutate_bad_comparator(resource, _RNG)
    assert result is not None
    assert result.resource["valueQuantity"]["comparator"] == "~"
    assert result.original_value is None


def test_bad_comparator_skips_when_one_is_present():
    resource = {"resourceType": "Observation", "valueQuantity": {"value": 1, "comparator": "<"}}
    assert mutate.mutate_bad_comparator(resource, _RNG) is None


def test_bad_comparator_skips_without_a_numeric_value():
    resource = {"resourceType": "Observation", "valueQuantity": {"unit": "kg"}}
    assert mutate.mutate_bad_comparator(resource, _RNG) is None


def test_freetext_code_replaces_a_bound_code():
    resource = {"resourceType": "Patient", "gender": "male"}
    result = mutate.mutate_freetext_code(resource, _RNG)
    assert result is not None
    assert result.resource["gender"] == "Male (self-reported)"
    assert result.original_value == "male"
    assert result.location == "Patient.gender"


def test_freetext_code_handles_observation_status():
    resource = {"resourceType": "Observation", "status": "final"}
    result = mutate.mutate_freetext_code(resource, _RNG)
    assert result is not None
    assert result.resource["status"] == "Final result"


def test_freetext_code_skips_unmapped_value():
    resource = {"resourceType": "Patient", "gender": "other"}
    assert mutate.mutate_freetext_code(resource, _RNG) is None


def test_interpretive_mutations_are_registered():
    for name in ("unit_mismatch", "date_precision", "bad_comparator", "freetext_code"):
        assert name in mutate.MUTATIONS, name
