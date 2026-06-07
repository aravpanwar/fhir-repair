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
