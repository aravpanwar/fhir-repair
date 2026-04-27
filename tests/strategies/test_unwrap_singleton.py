"""Tests for the unwrap_singleton deterministic strategy."""

from __future__ import annotations

from fhir_repair.core.models import ValidationError
from fhir_repair.strategies.deterministic import cardinality


def _error(location: str = "Observation.status") -> ValidationError:
    return ValidationError(
        code="unexpected-array",
        severity="error",
        location=location,
        message="",
    )


def test_unwraps_singleton(observation_singleton_array):
    action = cardinality.apply(observation_singleton_array, _error())
    assert action.risk == "low"
    assert observation_singleton_array["status"] == "final"


def test_refuses_non_list():
    resource = {"resourceType": "Observation", "status": "final"}
    action = cardinality.apply(resource, _error())
    assert action.risk == "refused"


def test_refuses_multi_element_list():
    resource = {"resourceType": "Observation", "status": ["final", "amended"]}
    action = cardinality.apply(resource, _error())
    assert action.risk == "refused"
    assert resource["status"] == ["final", "amended"]


def test_refuses_empty_list():
    resource = {"resourceType": "Observation", "status": []}
    action = cardinality.apply(resource, _error())
    assert action.risk == "refused"
