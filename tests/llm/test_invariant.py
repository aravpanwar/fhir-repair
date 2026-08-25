"""Tests for the invariant repair strategy.

The strategy is scoped to removal: it may drop the flagged element and
nothing else. These tests cover the response contract, the removal itself,
and the refusal paths that keep it from writing clinical content.
"""

from __future__ import annotations

import pytest

from fhir_repair.core.models import ValidationError
from fhir_repair.llm.base import Completion
from fhir_repair.strategies.llm import register_default_llm_strategies
from fhir_repair.strategies.llm.invariant import (
    NAME,
    PERMISSION,
    parse_invariant_response,
)
from fhir_repair.strategies.llm.runner import DELETE
from fhir_repair.strategies.registry import StrategyRegistry


class _ScriptedProvider:
    """Returns a caller-supplied response body."""

    def __init__(self, text: str) -> None:
        self._text = text

    def complete(self, segments, **kwargs):
        return Completion(
            text=self._text,
            input_tokens=1,
            output_tokens=1,
            cached_tokens=0,
            model="stub",
            provider="stub",
        )

    def supports_caching(self) -> bool:
        return False


def _observation() -> dict:
    """An Observation violating obs-7: a value and a dataAbsentReason."""
    return {
        "resourceType": "Observation",
        "id": "obs-1",
        "status": "final",
        "code": {"coding": [{"system": "http://loinc.org", "code": "8867-4"}]},
        "valueQuantity": {"value": 72, "unit": "beats/min"},
        "dataAbsentReason": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/data-absent-reason",
                    "code": "unknown",
                }
            ]
        },
    }


def _error() -> ValidationError:
    return ValidationError(
        code="invariant-failed",
        severity="error",
        location="Observation.dataAbsentReason",
        message="obs-7: dataAbsentReason SHALL only be present if value[x] is not present",
    )


def _strategy(response: str):
    registry = StrategyRegistry()
    register_default_llm_strategies(registry, _ScriptedProvider(response))
    return registry.get(NAME)


def test_parser_accepts_remove():
    assert parse_invariant_response('{"action": "remove"}') is DELETE


def test_parser_accepts_none_as_decline():
    assert parse_invariant_response('{"action": "none"}') is None


def test_parser_rejects_replacement_value():
    """A replacement value is out of scope; the strategy only removes."""
    with pytest.raises(ValueError):
        parse_invariant_response('{"value": {"coding": []}}')


def test_parser_rejects_unknown_action():
    with pytest.raises(ValueError):
        parse_invariant_response('{"action": "rewrite"}')


def test_parser_rejects_non_object():
    with pytest.raises(ValueError):
        parse_invariant_response('"remove"')


def test_registered_with_clinical_value_permission():
    """Dropping submitted data is a clinical-value change, not a reformat."""
    strategy = _strategy('{"action": "remove"}')
    assert strategy.permission == PERMISSION
    assert strategy.permission == "allow_change_existing_clinical_value"


def test_remove_drops_the_flagged_element():
    strategy = _strategy('{"action": "remove"}')
    resource = _observation()

    action = strategy.apply(resource, _error())

    assert "dataAbsentReason" not in resource
    assert resource["valueQuantity"] == {"value": 72, "unit": "beats/min"}
    assert action.risk != "refused"
    assert action.removed is True
    assert action.after is None


def test_decline_leaves_resource_untouched():
    strategy = _strategy('{"action": "none"}')
    resource = _observation()

    action = strategy.apply(resource, _error())

    assert "dataAbsentReason" in resource
    assert action.risk == "refused"


def test_remove_of_absent_path_is_refused():
    """The model asking to drop something that is not there is not a fix."""
    strategy = _strategy('{"action": "remove"}')
    resource = _observation()
    del resource["dataAbsentReason"]

    action = strategy.apply(resource, _error())

    assert action.risk == "refused"


def test_unparseable_response_is_refused():
    strategy = _strategy("sure, remove it")
    resource = _observation()

    action = strategy.apply(resource, _error())

    assert action.risk == "refused"
    assert "dataAbsentReason" in resource
