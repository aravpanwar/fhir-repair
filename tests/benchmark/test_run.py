"""Tests for the benchmark aggregation and leaderboard helpers.

These cover the pure functions in benchmark.run. The full run_benchmark
path needs a HAPI server and is exercised by the validator-smoke workflow,
not here.
"""

from __future__ import annotations

from benchmark import run


def _result(mutation: str, passed: bool, matched: bool, duration_ms: int = 10) -> dict:
    return {
        "mutation": mutation,
        "passed_validator": passed,
        "matches_ground_truth": matched,
        "duration_ms": duration_ms,
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
