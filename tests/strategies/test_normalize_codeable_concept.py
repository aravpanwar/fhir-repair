"""Tests for the normalize_codeable_concept deterministic strategy."""

from __future__ import annotations

from fhir_repair.core.models import ValidationError
from fhir_repair.strategies.deterministic import codeable_concept as cc_strategy


def _error(location: str = "Observation.code") -> ValidationError:
    return ValidationError(
        code="processing",
        severity="error",
        location=location,
        message="",
    )


def test_wraps_bare_coding():
    resource = {
        "resourceType": "Observation",
        "code": {"system": "http://loinc.org", "code": "1234-5", "display": "Test"},
    }
    action = cc_strategy.apply(resource, _error())
    assert action.risk == "low"
    assert resource["code"] == {
        "coding": [{"system": "http://loinc.org", "code": "1234-5", "display": "Test"}]
    }


def test_lifts_text_to_codeable_concept_level():
    resource = {
        "resourceType": "Observation",
        "code": {"system": "http://loinc.org", "code": "1234-5", "text": "Glucose"},
    }
    action = cc_strategy.apply(resource, _error())
    assert action.risk == "low"
    assert resource["code"] == {
        "coding": [{"system": "http://loinc.org", "code": "1234-5"}],
        "text": "Glucose",
    }


def test_already_codeable_concept_is_refused():
    resource = {
        "resourceType": "Observation",
        "code": {"coding": [{"system": "http://loinc.org", "code": "1234-5"}]},
    }
    action = cc_strategy.apply(resource, _error())
    assert action.risk == "refused"


def test_missing_code_is_refused():
    resource = {
        "resourceType": "Observation",
        "code": {"system": "http://loinc.org", "display": "Test"},
    }
    action = cc_strategy.apply(resource, _error())
    assert action.risk == "refused"


def test_non_coding_keys_are_refused():
    # An object that is not a Coding should not be reshaped.
    resource = {
        "resourceType": "Observation",
        "code": {"code": "1234-5", "reference": "Observation/2"},
    }
    action = cc_strategy.apply(resource, _error())
    assert action.risk == "refused"


def test_non_object_is_refused():
    resource = {
        "resourceType": "Observation",
        "code": "1234-5",
    }
    action = cc_strategy.apply(resource, _error())
    assert action.risk == "refused"


def test_strategy_metadata_is_well_formed():
    assert cc_strategy.NAME == "deterministic.normalize_codeable_concept"
    assert cc_strategy.PERMISSION == "allow_reformat"
    assert cc_strategy.RISK == "low"
