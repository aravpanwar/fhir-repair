"""Tests for the normalize_date deterministic strategy."""

from __future__ import annotations

import pytest

from fhir_repair.core.models import ValidationError
from fhir_repair.strategies.deterministic import date as date_strategy


def _error(location: str = "Patient.birthDate") -> ValidationError:
    return ValidationError(
        code="invalid-date-format",
        severity="error",
        location=location,
        message="",
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1990-3-5", "1990-03-05"),
        ("1990/03/05", "1990-03-05"),
        ("1990/3/5", "1990-03-05"),
        ("1990-03-05", "1990-03-05"),
    ],
)
def test_year_first_padding(raw, expected):
    resource = {"resourceType": "Patient", "birthDate": raw}
    action = date_strategy.apply(resource, _error())
    assert action.risk == "low"
    assert action.after == expected
    assert resource["birthDate"] == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("13/05/1990", "1990-05-13"),  # day > 12, unambiguously D-M-Y
        ("05/13/1990", "1990-05-13"),  # day > 12, unambiguously M-D-Y
    ],
)
def test_year_last_disambiguation(raw, expected):
    resource = {"resourceType": "Patient", "birthDate": raw}
    action = date_strategy.apply(resource, _error())
    assert action.after == expected


def test_ambiguous_year_last_is_refused():
    # Both components are <= 12, so we cannot tell M-D from D-M.
    resource = {"resourceType": "Patient", "birthDate": "01/02/2000"}
    action = date_strategy.apply(resource, _error())
    assert action.risk == "refused"
    # Resource is left untouched on refusal.
    assert resource["birthDate"] == "01/02/2000"


def test_non_string_value_is_refused():
    resource = {"resourceType": "Patient", "birthDate": 19900305}
    action = date_strategy.apply(resource, _error())
    assert action.risk == "refused"


def test_garbage_value_is_refused():
    resource = {"resourceType": "Patient", "birthDate": "not a date"}
    action = date_strategy.apply(resource, _error())
    assert action.risk == "refused"


def test_strategy_metadata_is_well_formed():
    assert date_strategy.NAME == "deterministic.normalize_date"
    assert date_strategy.PERMISSION == "allow_reformat"
    assert date_strategy.RISK == "low"
