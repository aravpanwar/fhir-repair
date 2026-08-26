"""Tests for the invariant repair strategy.

The strategy is scoped to removal: it may drop one element and nothing else.
HAPI reports an invariant failure against the resource rather than the
offending field, so the model names the element and the strategy checks that
name against the resource before deleting anything. These tests cover the
response contract, the removal, and the refusal paths.
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


class _ExplodingProvider:
    def complete(self, segments, **kwargs):
        raise RuntimeError("upstream is down")

    def supports_caching(self) -> bool:
        return False


def _observation() -> dict:
    """An Observation violating obs-6: a value and a dataAbsentReason."""
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
    """HAPI reports the constraint against the resource, not the field."""
    return ValidationError(
        code="processing",
        severity="error",
        location="Observation",
        message=(
            "Constraint failed: obs-6: 'dataAbsentReason SHALL only be "
            "present if Observation.value[x] is not present'"
        ),
    )


def _strategy(response: str):
    registry = StrategyRegistry()
    register_default_llm_strategies(registry, _ScriptedProvider(response))
    return registry.get(NAME)


def test_parser_accepts_an_element_name():
    assert parse_invariant_response('{"remove": "dataAbsentReason"}') == "dataAbsentReason"


def test_parser_trims_whitespace():
    assert parse_invariant_response('{"remove": " dataAbsentReason "}') == "dataAbsentReason"


def test_parser_accepts_null_as_decline():
    assert parse_invariant_response('{"remove": null}') is None


def test_parser_rejects_replacement_value():
    """A replacement value is out of scope; the strategy only removes."""
    with pytest.raises(ValueError):
        parse_invariant_response('{"value": {"coding": []}}')


def test_parser_rejects_missing_key():
    with pytest.raises(ValueError):
        parse_invariant_response('{"action": "remove"}')


def test_parser_rejects_non_object():
    with pytest.raises(ValueError):
        parse_invariant_response('"dataAbsentReason"')


def test_parser_rejects_empty_name():
    with pytest.raises(ValueError):
        parse_invariant_response('{"remove": "  "}')


def test_registered_with_clinical_value_permission():
    """Dropping submitted data is a clinical-value change, not a reformat."""
    strategy = _strategy('{"remove": "dataAbsentReason"}')
    assert strategy.permission == PERMISSION
    assert strategy.permission == "allow_change_existing_clinical_value"


def test_removes_the_named_element():
    strategy = _strategy('{"remove": "dataAbsentReason"}')
    resource = _observation()

    action = strategy.apply(resource, _error())

    assert "dataAbsentReason" not in resource
    # The clinical measurement is preserved.
    assert resource["valueQuantity"] == {"value": 72, "unit": "beats/min"}
    assert action.risk != "refused"
    assert action.removed is True
    assert action.after is None
    assert action.llm["provider"] == "stub"


def test_decline_leaves_resource_untouched():
    strategy = _strategy('{"remove": null}')
    resource = _observation()

    action = strategy.apply(resource, _error())

    assert "dataAbsentReason" in resource
    assert action.risk == "refused"


def test_absent_element_is_refused():
    """Never delete on an unchecked name."""
    strategy = _strategy('{"remove": "explanation"}')
    resource = _observation()

    action = strategy.apply(resource, _error())

    assert action.risk == "refused"
    assert len(resource) == 6


@pytest.mark.parametrize("protected", ["resourceType", "id", "meta"])
def test_structural_elements_are_refused(protected):
    """A model naming resourceType must not be able to gut the resource."""
    resource = _observation()
    resource["meta"] = {"versionId": "1"}
    strategy = _strategy(f'{{"remove": "{protected}"}}')

    action = strategy.apply(resource, _error())

    assert action.risk == "refused"
    assert protected in resource


def test_unparseable_response_is_refused():
    strategy = _strategy("sure, drop dataAbsentReason")
    resource = _observation()

    action = strategy.apply(resource, _error())

    assert action.risk == "refused"
    assert "dataAbsentReason" in resource


def test_provider_failure_is_refused():
    registry = StrategyRegistry()
    register_default_llm_strategies(registry, _ExplodingProvider())
    resource = _observation()

    action = registry.get(NAME).apply(resource, _error())

    assert action.risk == "refused"
    assert "dataAbsentReason" in resource


def test_candidates_exclude_structural_elements():
    from fhir_repair.strategies.llm.invariant import _candidate_elements

    candidates = _candidate_elements(_observation())

    assert "dataAbsentReason" in candidates
    assert "valueQuantity" in candidates
    assert "resourceType" not in candidates
    assert "id" not in candidates
