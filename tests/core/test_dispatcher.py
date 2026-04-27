"""Tests for the dispatcher: ordering, regression detection, stuck-detection."""

from __future__ import annotations

from fhir_repair.core.dispatcher import (
    StrategyResolver,
    build_plan,
    detect_regressions,
    is_stuck,
)
from fhir_repair.core.models import ValidationError


def _err(code: str, location: str) -> ValidationError:
    return ValidationError(code=code, severity="error", location=location, message="")


def test_resolver_falls_back_to_unknown_error():
    resolver = StrategyResolver({"unknown-error": "llm"})
    error = _err("never-seen-before", "Patient.birthDate")
    assert resolver.resolve(error) == "llm"


def test_resolver_returns_none_when_no_match_and_no_fallback():
    resolver = StrategyResolver({})
    assert resolver.resolve(_err("x", "Patient")) is None


def test_plan_orders_deepest_first():
    resolver = StrategyResolver(
        {"shallow": "deterministic.normalize_date", "deep": "deterministic.normalize_date"}
    )
    errors = [
        _err("shallow", "Patient.birthDate"),
        _err("deep", "Patient.contact[0].telecom[0].value"),
    ]
    plan = build_plan(errors, resolver)
    assert plan.batches[0][0][1].location == "Patient.contact[0].telecom[0].value"
    assert plan.batches[1][0][1].location == "Patient.birthDate"


def test_plan_separates_depth_into_batches():
    resolver = StrategyResolver(
        {
            "a": "deterministic.normalize_date",
            "b": "deterministic.normalize_date",
            "c": "deterministic.normalize_date",
        }
    )
    errors = [
        _err("a", "Patient.x"),
        _err("b", "Patient.y.z"),
        _err("c", "Patient.q"),
    ]
    plan = build_plan(errors, resolver)
    # Two distinct depths means two batches.
    assert len(plan.batches) == 2


def test_plan_puts_deterministic_before_llm_at_same_depth():
    resolver = StrategyResolver({"a": "deterministic.normalize_date", "b": "llm"})
    errors = [
        _err("b", "Patient.x"),
        _err("a", "Patient.y"),
    ]
    plan = build_plan(errors, resolver)
    first_batch = plan.batches[0]
    # First entry should be the deterministic strategy.
    assert first_batch[0][0].startswith("deterministic.")


def test_unmapped_errors_collected_separately():
    resolver = StrategyResolver({"foo": "deterministic.normalize_date"})
    errors = [_err("nope", "Patient.x")]
    plan = build_plan(errors, resolver)
    assert plan.batches == []
    assert plan.unmapped == errors


def test_refuse_strategy_goes_to_unmapped():
    resolver = StrategyResolver({"foo": "refuse"})
    errors = [_err("foo", "Patient.x")]
    plan = build_plan(errors, resolver)
    assert plan.unmapped == errors


def test_detect_regressions_flags_new_errors_on_touched_paths():
    before = [_err("a", "Patient.x")]
    after = [_err("a", "Patient.x"), _err("b", "Patient.y")]
    regressions = detect_regressions(before, after, {"Patient.y"})
    assert len(regressions) == 1
    assert regressions[0].code == "b"


def test_detect_regressions_ignores_new_errors_on_untouched_paths():
    before = [_err("a", "Patient.x")]
    after = [_err("a", "Patient.x"), _err("b", "Patient.y")]
    regressions = detect_regressions(before, after, {"Patient.x"})
    assert regressions == []


def test_is_stuck_detects_identical_error_sets():
    a = [_err("x", "Patient.foo"), _err("y", "Patient.bar")]
    b = [_err("y", "Patient.bar"), _err("x", "Patient.foo")]
    assert is_stuck(a, b)


def test_is_stuck_returns_false_when_set_changes():
    a = [_err("x", "Patient.foo")]
    b = [_err("y", "Patient.bar")]
    assert not is_stuck(a, b)
