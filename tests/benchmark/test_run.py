"""Tests for the benchmark aggregation and leaderboard helpers.

These cover the pure functions in benchmark.run. The full run_benchmark
path needs a HAPI server and is exercised by the validator-smoke workflow,
not here.
"""

from __future__ import annotations

import pytest

from benchmark import run


def _result(
    mutation: str,
    passed: bool,
    matched: bool,
    duration_ms: int = 10,
    detected: bool = True,
) -> dict:
    return {
        "mutation": mutation,
        "passed_validator": passed,
        "matches_ground_truth": matched,
        "duration_ms": duration_ms,
        "validator_detected": detected,
    }


def test_aggregate_empty_returns_zero_total():
    assert run._aggregate([], 0) == {"total": 0}


def test_aggregate_overall_rates():
    results = [
        _result("date_format", True, True),
        _result("date_format", True, False),
        _result("missing_required", False, False),
        _result("missing_required", False, False),
    ]
    summary = run._aggregate(results, total_duration_ms=100)
    assert summary["total"] == 4
    assert summary["validator_pass_rate"] == 0.5
    assert summary["ground_truth_match_rate"] == 0.25
    assert summary["mean_duration_ms"] == 10
    assert summary["total_duration_ms"] == 100


def test_aggregate_groups_by_mutation():
    results = [
        _result("date_format", True, True),
        _result("date_format", False, False),
        _result("telecom_format", True, True),
    ]
    by_mutation = run._aggregate(results, 0)["by_mutation"]
    assert by_mutation["date_format"]["total"] == 2
    assert by_mutation["date_format"]["validator_pass_rate"] == 0.5
    assert by_mutation["telecom_format"]["validator_pass_rate"] == 1.0
    assert by_mutation["telecom_format"]["ground_truth_match_rate"] == 1.0


def test_aggregate_counts_undetected_cases():
    """A mutation the validator accepts is not a repair the tool made."""
    results = [
        _result("date_format", True, True),
        _result("telecom_format", True, False, detected=False),
        _result("telecom_format", True, False, detected=False),
    ]
    summary = run._aggregate(results, 0)

    assert summary["undetected_by_validator"] == 2
    # Blended rate still counts everything, so it looks strong.
    assert summary["validator_pass_rate"] == 1.0
    # Restricted to cases that actually posed a problem, it is honest.
    assert summary["detected_total"] == 1
    assert summary["detected_ground_truth_match_rate"] == 1.0


def test_aggregate_flags_undetected_class_in_breakdown():
    results = [
        _result("telecom_format", True, False, detected=False),
        _result("date_format", True, True),
    ]
    by_mutation = run._aggregate(results, 0)["by_mutation"]

    assert by_mutation["telecom_format"]["undetected_by_validator"] == 1
    # Classes the validator does flag carry no such marker.
    assert "undetected_by_validator" not in by_mutation["date_format"]


def test_aggregate_omits_detected_rates_when_all_detected():
    """No second set of numbers when there is nothing to separate out."""
    summary = run._aggregate([_result("date_format", True, True)], 0)

    assert summary["undetected_by_validator"] == 0
    assert "detected_validator_pass_rate" not in summary


def _payload(model: str, pass_rate: float) -> dict:
    return {
        "summary": {
            "total": 10,
            "validator_pass_rate": pass_rate,
            "ground_truth_match_rate": pass_rate,
            "mean_duration_ms": 5,
            "by_mutation": {},
        },
        "metadata": {
            "llm_model": model,
            "prompt_version": "v1",
            "dispatch_version": "1.0.0",
        },
    }


def test_append_to_leaderboard_creates_file(tmp_path):
    path = tmp_path / "leaderboard.json"
    entries = run.append_to_leaderboard(_payload("model-a", 0.8), path, "model-a")
    assert path.exists()
    assert len(entries) == 1
    assert entries[0]["label"] == "model-a"
    assert entries[0]["validator_pass_rate"] == 0.8
    assert entries[0]["dispatch_version"] == "1.0.0"


def test_append_to_leaderboard_accumulates(tmp_path):
    path = tmp_path / "leaderboard.json"
    run.append_to_leaderboard(_payload("model-a", 0.8), path, "model-a")
    entries = run.append_to_leaderboard(_payload("model-b", 0.6), path, "model-b")
    assert len(entries) == 2
    assert [e["label"] for e in entries] == ["model-a", "model-b"]


def _manifest(mutation: str, index: int) -> dict:
    return {"mutation": mutation, "location": f"Patient.f{index}"}


def test_stratified_sample_covers_every_class_and_repeats():
    manifests = [_manifest("big", i) for i in range(100)] + [
        _manifest(name, 0) for name in ("a", "b", "c")
    ]

    first = run.stratified_sample(manifests, 6)

    assert len(first) == 6
    assert {m["mutation"] for m in first} == {"a", "b", "c", "big"}
    assert first == run.stratified_sample(manifests, 6)


def test_llm_usage_sums_calls_and_tokens_including_refusals():
    from fhir_repair.core.models import RepairAction, ValidationError

    error = ValidationError("processing", "error", "Patient.gender", "")

    def action(risk: str, llm: dict | None) -> RepairAction:
        return RepairAction(error, "llm", "1", risk, "p", None, None, "", llm=llm)

    block = {"input_tokens": 100, "output_tokens": 20, "cached_tokens": 5, "model": "m-1"}
    usage = run.llm_usage([action("medium", block), action("refused", block), action("low", None)])

    assert usage == {
        "llm_calls": 2,
        "input_tokens": 200,
        "output_tokens": 40,
        "cached_tokens": 10,
        "llm_models": ["m-1"],
    }


VALID = {
    "resourceType": "Observation",
    "status": "final",
    "valueQuantity": {"value": 72, "unit": "beats/min"},
}


@pytest.mark.parametrize(
    "mutated, fixed, expected",
    [
        # A correct fix changes only what the mutation broke.
        ({**VALID, "status": ["final"]}, VALID, []),
        # A missed fix changes nothing.
        ({**VALID, "status": "F"}, {**VALID, "status": "F"}, []),
        # Removing the measurement instead of the added field is collateral.
        (
            {**VALID, "dataAbsentReason": {"text": "x"}},
            {"resourceType": "Observation", "status": "final", "dataAbsentReason": {"text": "x"}},
            ["valueQuantity.unit", "valueQuantity.value"],
        ),
        # Rewriting a value the mutation never touched is collateral.
        (
            {**VALID, "status": "F"},
            {**VALID, "status": "final", "valueQuantity": {"value": 7}},
            [
                "valueQuantity.unit",
                "valueQuantity.value",
            ],
        ),
    ],
)
def test_collateral_changes(mutated, fixed, expected):
    assert run.collateral_changes(mutated, fixed, VALID) == expected


def test_deleted_value_outcome():
    mutated = {"resourceType": "Observation", "valueQuantity": VALID["valueQuantity"]}
    location = "Observation.status"

    assert run.deleted_value_outcome(location, mutated, mutated, VALID) == {
        "deleted_value_written": False,
        "deleted_value_matches": False,
    }
    assert run.deleted_value_outcome(location, mutated, {**mutated, "status": "final"}, VALID) == {
        "deleted_value_written": True,
        "deleted_value_matches": True,
    }
    assert (
        run.deleted_value_outcome(location, mutated, {**mutated, "status": "amended"}, VALID)[
            "deleted_value_matches"
        ]
        is False
    )
    # Not a deletion: the field is still there in the mutated resource.
    assert run.deleted_value_outcome(location, VALID, VALID, VALID) == {
        "deleted_value_written": None,
        "deleted_value_matches": None,
    }


def test_aggregate_totals_cost_and_harm():
    results = [
        {
            **_result("a", True, True),
            "llm_calls": 2,
            "input_tokens": 10,
            "output_tokens": 3,
            "cached_tokens": 1,
            "collateral_changes": ["x"],
            "deleted_value_written": True,
        },
        {
            **_result("a", True, True),
            "llm_calls": 1,
            "input_tokens": 5,
            "output_tokens": 2,
            "cached_tokens": 0,
            "collateral_changes": [],
            "deleted_value_written": None,
        },
    ]

    summary = run._aggregate(results, 0)

    assert (summary["llm_calls"], summary["input_tokens"], summary["output_tokens"]) == (3, 15, 5)
    assert summary["cases_with_collateral_changes"] == 1
    assert summary["deleted_values_written"] == 1
