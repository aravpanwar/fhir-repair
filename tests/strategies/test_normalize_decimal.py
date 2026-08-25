"""Tests for the normalize_decimal deterministic strategy."""

from __future__ import annotations

import pytest

from fhir_repair.core.models import ValidationError
from fhir_repair.strategies.deterministic import decimal as decimal_strategy


def _error(location: str = "Observation.valueQuantity.value") -> ValidationError:
    return ValidationError(
        code="invalid-decimal-format",
        severity="error",
        location=location,
        message="",
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("5,5", 5.5),  # locale comma
        (" 5.5 ", 5.5),  # surrounding whitespace
        ("+5.5", 5.5),  # plus sign prefix
        ("5.5", 5.5),  # string that only needed retyping
        ("0", 0),  # integer
        ("100", 100),  # integer, multi-digit
        ("0.1", 0.1),  # leading zero
        ("-3.14", -3.14),  # negative
        ("12.50", 12.5),  # trailing zero lost; see _normalize docstring
    ],
)
def test_normalises_to_a_json_number(raw, expected):
    """FHIR decimal is a JSON number; a quoted value stays invalid."""
    resource = {
        "resourceType": "Observation",
        "valueQuantity": {"value": raw},
    }
    action = decimal_strategy.apply(resource, _error())
    assert action.risk == "low"
    assert action.after == expected
    assert resource["valueQuantity"]["value"] == expected
    # The type matters, not just the value: "5.5" == 5.5 is False in JSON.
    assert isinstance(resource["valueQuantity"]["value"], (int, float))
    assert not isinstance(resource["valueQuantity"]["value"], str)


@pytest.mark.parametrize("raw, expected", [("0", 0), ("100", 100), ("-7", -7)])
def test_integral_values_stay_integers(raw, expected):
    """Avoids serialising a whole number as "100.0"."""
    resource = {"resourceType": "Observation", "valueQuantity": {"value": raw}}
    decimal_strategy.apply(resource, _error())
    assert resource["valueQuantity"]["value"] == expected
    assert isinstance(resource["valueQuantity"]["value"], int)


def test_result_serialises_as_a_json_number():
    import json

    resource = {"resourceType": "Observation", "valueQuantity": {"value": "69,64"}}
    decimal_strategy.apply(resource, _error())
    assert '"value": 69.64' in json.dumps(resource)


def test_already_numeric_is_refused():
    # Validator complaint about a numeric value is not something we can fix
    # by reformatting the string.
    resource = {
        "resourceType": "Observation",
        "valueQuantity": {"value": 5.5},
    }
    action = decimal_strategy.apply(resource, _error())
    assert action.risk == "refused"
    assert resource["valueQuantity"]["value"] == 5.5


def test_currency_prefix_is_refused():
    # Stripping "$" silently could lose meaning.
    resource = {
        "resourceType": "Observation",
        "valueQuantity": {"value": "$5.5"},
    }
    action = decimal_strategy.apply(resource, _error())
    assert action.risk == "refused"


def test_unit_suffix_is_refused():
    resource = {
        "resourceType": "Observation",
        "valueQuantity": {"value": "5.5 mg"},
    }
    action = decimal_strategy.apply(resource, _error())
    assert action.risk == "refused"


def test_scientific_notation_is_refused():
    # FHIR R4 permits scientific notation; if the validator flagged it,
    # the issue is upstream of this strategy.
    resource = {
        "resourceType": "Observation",
        "valueQuantity": {"value": "5e1"},
    }
    action = decimal_strategy.apply(resource, _error())
    assert action.risk == "refused"


def test_garbage_is_refused():
    resource = {
        "resourceType": "Observation",
        "valueQuantity": {"value": "not a number"},
    }
    action = decimal_strategy.apply(resource, _error())
    assert action.risk == "refused"


def test_double_separator_is_refused():
    # "1.234,56" is European thousands+decimal style. We do not attempt
    # locale-aware parsing because it requires more context than is in
    # a single value.
    resource = {
        "resourceType": "Observation",
        "valueQuantity": {"value": "1.234,56"},
    }
    action = decimal_strategy.apply(resource, _error())
    assert action.risk == "refused"


def test_strategy_metadata_is_well_formed():
    assert decimal_strategy.NAME == "deterministic.normalize_decimal"
    assert decimal_strategy.PERMISSION == "allow_reformat"
    assert decimal_strategy.RISK == "low"
