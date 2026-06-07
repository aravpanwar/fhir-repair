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
