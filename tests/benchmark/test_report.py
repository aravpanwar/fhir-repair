"""Tests for the benchmark report: grouping repeats, cost, and old runs."""

from __future__ import annotations

import json

import pytest

from benchmark import report

PRICES = {"flash": {"input": 0.30, "cached_input": 0.006, "output": 1.20}}


def _payload(label: str, truth: float, **summary: float) -> dict:
    return {
        "metadata": {"label": label, "llm_model": "flash"},
        "summary": {
            "total": 10,
            "validator_pass_rate": truth,
            "ground_truth_match_rate": truth,
            "mean_duration_ms": 100,
            "by_mutation": {"date_format": {"ground_truth_match_rate": truth}},
            **summary,
        },
    }


def _write(tmp_path, name: str, payload: dict):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_repeats_of_an_arm_are_grouped_with_their_spread(tmp_path):
    paths = [
        _write(tmp_path, "a.json", _payload("hybrid", 0.8)),
        _write(tmp_path, "b.json", _payload("hybrid", 0.6)),
        _write(tmp_path, "c.json", _payload("deterministic", 0.5)),
    ]

    runs = report.load_runs(paths)
    rows = {row["label"]: row for row in report.headline_rows(runs, PRICES)}

    assert rows["hybrid"]["runs"] == 2
    assert rows["hybrid"]["truth"][0] == pytest.approx(0.7)
    assert rows["hybrid"]["truth"][1] is not None
    assert rows["deterministic"]["truth"] == (0.5, None)


def test_cost_separates_cached_input():
    payload = _payload(
        "x", 1.0, llm_calls=4, input_tokens=1_000_000, cached_tokens=500_000, output_tokens=100_000
    )

    # 500k uncached at 0.30, 500k cached at 0.006, 100k output at 1.20.
    assert report.run_cost(payload, PRICES) == pytest.approx(0.15 + 0.003 + 0.12)


def test_runs_recorded_before_cost_tracking_read_as_not_available(tmp_path):
    old = _write(tmp_path, "old.json", _payload("old", 0.5))

    text = report.render(report.load_runs([old]), PRICES, by_class=True)

    assert "| old | 1 | 10 | 50.0% | 50.0% | 50.0% | n/a | n/a | n/a | n/a | 100 |" in text
    assert "| date_format | 50.0% |" in text
