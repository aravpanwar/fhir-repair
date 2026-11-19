"""Tests for the Repairer orchestration loop.

These use fake validators and stub strategies, so they exercise the loop's
bookkeeping (rollback, unresolved accounting) without a HAPI server.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from fhir_repair.core.config import LimitsConfig, LoggingConfig, RepairConfig
from fhir_repair.core.fhirpath import delete_at_path, get_at_path, set_at_path
from fhir_repair.core.guard import HallucinationGuard
from fhir_repair.core.models import RepairAction, ValidationError
from fhir_repair.core.repairer import Repairer
from fhir_repair.strategies.base import refused
from fhir_repair.strategies.deterministic import identifier
from fhir_repair.strategies.registry import StrategyRegistry

Rule = Callable[[dict[str, Any]], list[ValidationError]]


class _RuleValidator:
    """Derives the error list from the resource, and counts its calls."""

    def __init__(self, rule: Rule) -> None:
        self._rule = rule
        self.calls = 0

    def validate(self, resource: dict, profile: str | None = None) -> list[ValidationError]:
        self.calls += 1
        return self._rule(resource)

    def close(self) -> None:
        pass


class _ConstantValidator(_RuleValidator):
    """Returns the same error list on every call, forcing the stuck detector."""

    def __init__(self, errors: list[ValidationError]) -> None:
        super().__init__(lambda _: list(errors))


class _Stub:
    """A strategy that writes `value` at `path`, or removes the element there."""

    version = "1.0.0"
    permission = "allow_reformat"
    risk = "low"

    def __init__(self, name: str, path: str, value: Any = None, remove: bool = False) -> None:
        self.name = name
        self._path = path
        self._value = value
        self._remove = remove

    def apply(self, resource: dict, error: ValidationError) -> RepairAction:
        before = get_at_path(resource, self._path)
        if self._remove:
            delete_at_path(resource, self._path)
        else:
            set_at_path(resource, self._path, self._value)
        return RepairAction(
            error=error,
            strategy=self.name,
            strategy_version=self.version,
            risk="low",
            permission_used=self.permission,
            before=before,
            after=None if self._remove else self._value,
            explanation="stub",
            removed=self._remove,
            path=self._path,
        )


class _RefusingStrategy:
    name = "stub.refuse"
    version = "1.0.0"
    permission = "allow_reformat"
    risk = "low"

    def apply(self, resource: dict, error: ValidationError) -> RepairAction:
        return refused(error, self.name, self.version, self.permission, None, "always refuses")


def _error(code: str, location: str, message: str = "") -> ValidationError:
    return ValidationError(code, "error", location, message or code)


def _repairer(
    tmp_path,
    validator,
    strategies: dict[str, Any],
    *stubs: Any,
    guard: HallucinationGuard | None = None,
) -> Repairer:
    registry = StrategyRegistry()
    registry.register_module(identifier)
    for stub in stubs:
        registry.register(stub)
    config = RepairConfig(
        strategies=strategies,
        logging=LoggingConfig(audit_destination=str(tmp_path)),
    )
    return Repairer(validator=validator, registry=registry, config=config, guard=guard)


def _gustav() -> dict[str, Any]:
    return {
        "resourceType": "Patient",
        "id": "37-4407",
        "name": [{"family": "victor", "given": ["Gustav"]}],
        "gender": "M",
        "birthDate": "1937-04-27",
        "deceasedBoolean": False,
        "identifier": [{"system": "SSN", "value": "37-4407"}],
    }


def _gender_regresses(resource: dict[str, Any]) -> list[ValidationError]:
    if resource.get("gender") == "REGRESS":
        return [_error("value", "Patient.gender", "a new problem")]
    return []


def test_nested_fix_that_regresses_is_undone(tmp_path):
    # The rollback restored the resource by adopting the snapshot's nested
    # objects, so replaying a nested write changed the snapshot too and
    # the second restore brought the bad value back.
    def rule(resource: dict[str, Any]) -> list[ValidationError]:
        family = resource["name"][0]["family"]
        if family == "victor":
            return [_error("family", "Patient.name[0].family")]
        if family == "REGRESS":
            return [_error("family", "Patient.name[0].family", "a different problem")]
        return []

    stub = _Stub("stub.regress", "Patient.name[0].family", "REGRESS")
    repairer = _repairer(tmp_path, _RuleValidator(rule), {"family": "stub.regress"}, stub)

    result = repairer.repair(_gustav())

    assert result.fixed_resource["name"][0]["family"] == "victor"
    assert [action.risk for action in result.audit] == ["refused"]
    assert "regression at Patient.name[0].family" in result.audit[0].explanation


def test_rolled_back_identifier_fix_is_replayed_at_its_system(tmp_path):
    # HAPI reports the identifier error on the Identifier, but the strategy
    # writes to Identifier.system. Replaying at the error location replaced
    # the whole Identifier with the system string.
    def rule(resource: dict[str, Any]) -> list[ValidationError]:
        errors = _gender_regresses(resource)
        if resource["identifier"][0].get("system") == "SSN":
            errors.append(_error("ident", "Patient.identifier[0]"))
        if resource.get("gender") == "M":
            errors.append(_error("gender", "Patient.gender"))
        return errors

    stub = _Stub("stub.regress", "Patient.gender", "REGRESS")
    strategies = {
        "ident": "deterministic.canonicalize_identifier_system",
        "gender": "stub.regress",
    }
    repairer = _repairer(tmp_path, _RuleValidator(rule), strategies, stub)

    result = repairer.repair(_gustav())

    assert result.fixed_resource["identifier"] == [
        {"system": "http://hl7.org/fhir/sid/us-ssn", "value": "37-4407"}
    ]
    assert result.fixed_resource["gender"] == "M"
    risks = {action.strategy: action.risk for action in result.audit}
    assert risks == {
        "deterministic.canonicalize_identifier_system": "low",
        "stub.regress": "refused",
    }


def test_rolled_back_removal_is_replayed_as_a_deletion(tmp_path):
    # An invariant removal is reported against the whole resource. Replaying
    # it at the error location tried to delete the resource and raised out
    # of repair().
    # Both errors are reported at the resource level, so they share a batch.
    def rule(resource: dict[str, Any]) -> list[ValidationError]:
        errors = _gender_regresses(resource)
        if "deceasedBoolean" in resource:
            errors.append(_error("invariant", "Patient"))
        if resource.get("gender") == "M":
            errors.append(_error("gender", "Patient"))
        return errors

    remove = _Stub("stub.remove", "Patient.deceasedBoolean", remove=True)
    regress = _Stub("stub.regress", "Patient.gender", "REGRESS")
    strategies = {"invariant": "stub.remove", "gender": "stub.regress"}
    repairer = _repairer(tmp_path, _RuleValidator(rule), strategies, remove, regress)

    result = repairer.repair(_gustav())

    assert "deceasedBoolean" not in result.fixed_resource
    assert result.fixed_resource["gender"] == "M"
    risks = {action.strategy: action.risk for action in result.audit}
    assert risks == {"stub.remove": "low", "stub.regress": "refused"}


def test_replay_that_no_longer_applies_becomes_a_refusal(tmp_path):
    # Two fixes in one batch remove the same element. Once the first is
    # replayed there is nothing left for the second, which is refused
    # rather than raising. A refusal already in the batch passes through.
    def rule(resource: dict[str, Any]) -> list[ValidationError]:
        errors = _gender_regresses(resource)
        if "deceasedBoolean" in resource:
            errors += [_error("first", "Patient"), _error("second", "Patient")]
        if resource.get("gender") == "M":
            errors += [_error("gender", "Patient"), _error("other", "Patient")]
        return errors

    stubs = [
        _Stub("stub.first", "Patient.deceasedBoolean", remove=True),
        _Stub("stub.second", "Patient.deceasedBoolean", remove=True),
        _Stub("stub.regress", "Patient.gender", "REGRESS"),
        _RefusingStrategy(),
    ]
    strategies = {
        "first": "stub.first",
        "second": "stub.second",
        "gender": "stub.regress",
        "other": "stub.refuse",
    }
    repairer = _repairer(tmp_path, _RuleValidator(rule), strategies, *stubs)

    result = repairer.repair(_gustav())

    assert "deceasedBoolean" not in result.fixed_resource
    actions = {action.strategy: action for action in result.audit}
    assert actions["stub.first"].risk == "low"
    assert actions["stub.second"].risk == "refused"
    assert "could not be replayed" in actions["stub.second"].explanation
    assert actions["stub.refuse"].explanation == "Refused: always refuses."


def test_guard_refusal_does_not_run_the_strategy(tmp_path):
    stub = _Stub("stub.fix", "Patient.gender", "male")
    validator = _ConstantValidator([_error("gender", "Patient.gender")])
    repairer = _repairer(
        tmp_path, validator, {"gender": "stub.fix"}, stub, guard=HallucinationGuard.strict()
    )

    result = repairer.repair(_gustav())

    assert result.fixed_resource["gender"] == "M"
    assert "hallucination guard denies permission 'allow_reformat'" in result.audit[0].explanation


def test_one_fix_costs_two_validator_calls(tmp_path):
    # One to find the error, one to confirm the fix. Re-validating state
    # that was just validated doubled the HAPI round trips.
    def rule(resource: dict[str, Any]) -> list[ValidationError]:
        return [_error("gender", "Patient.gender")] if resource["gender"] == "M" else []

    validator = _RuleValidator(rule)
    stub = _Stub("stub.fix", "Patient.gender", "male")
    result = _repairer(tmp_path, validator, {"gender": "stub.fix"}, stub).repair(_gustav())

    assert result.fixed_resource["gender"] == "male"
    assert result.unresolved == []
    assert validator.calls == 2


def test_unfixable_error_is_attempted_once(tmp_path):
    # The stuck detector only compared attempts after the first, so an
    # error nothing could fix ran its whole strategy chain twice, which
    # for an LLM strategy is a second paid call.
    validator = _ConstantValidator([_error("mapped-code", "Patient.a")])
    repairer = _repairer(tmp_path, validator, {"mapped-code": "stub.refuse"}, _RefusingStrategy())

    result = repairer.repair(_gustav())

    assert len(result.audit) == 1
    assert validator.calls == 1


def test_error_cleared_by_another_fix_is_not_unresolved(tmp_path):
    # The unmapped error is a knock-on effect of the gender error and goes
    # away with it, so the resource comes back valid.
    def rule(resource: dict[str, Any]) -> list[ValidationError]:
        if resource["gender"] != "M":
            return []
        return [_error("gender", "Patient.gender"), _error("knock-on", "Patient")]

    stub = _Stub("stub.fix", "Patient.gender", "male")
    result = _repairer(tmp_path, _RuleValidator(rule), {"gender": "stub.fix"}, stub).repair(
        _gustav()
    )

    assert result.unresolved == []


def test_summary_counts_fixed_errors_not_a_difference(tmp_path):
    # Fixing one error uncovers two more. `fixed` used to be computed as
    # total minus unresolved, which came out as -1.
    def rule(resource: dict[str, Any]) -> list[ValidationError]:
        if resource["gender"] == "M":
            return [_error("gender", "Patient.gender")]
        return [_error("a", "Patient.name"), _error("b", "Patient.birthDate")]

    stub = _Stub("stub.fix", "Patient.gender", "male")
    result = _repairer(tmp_path, _RuleValidator(rule), {"gender": "stub.fix"}, stub).repair(
        _gustav()
    )

    (log,) = tmp_path.glob("*.audit.jsonl")
    summary = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])["summary"]
    assert summary == {
        "total_errors": 1,
        "fixed": 1,
        "unresolved": 2,
        "duration_ms": summary["duration_ms"],
    }
    assert len(result.unresolved) == 2


class _CountingLLMStrategy(_RefusingStrategy):
    name = "llm.counting"

    def __init__(self) -> None:
        self.calls = 0

    def apply(self, resource: dict, error: ValidationError) -> RepairAction:
        self.calls += 1
        return super().apply(resource, error)


def test_llm_strategies_stop_at_the_per_resource_limit(tmp_path):
    # max_llm_calls_per_resource was documented but never read.
    llm = _CountingLLMStrategy()
    registry = StrategyRegistry()
    registry.register(llm)
    config = RepairConfig(
        strategies={"code": "llm.counting"},
        limits=LimitsConfig(max_llm_calls_per_resource=2),
        logging=LoggingConfig(audit_destination=str(tmp_path)),
    )
    errors = [_error("code", f"Patient.field{i}") for i in range(3)]
    repairer = Repairer(
        validator=_ConstantValidator(errors),
        registry=registry,
        config=config,
        llm_provider=object(),  # type: ignore[arg-type]
    )

    result = repairer.repair(_gustav())

    assert llm.calls == 2
    assert "LLM call limit" in result.audit[-1].explanation


def test_unknown_strategy_id_fails_at_construction(tmp_path):
    with pytest.raises(ValueError, match=r"unknown strategy 'deterministic\.normalise_date'"):
        _repairer(
            tmp_path,
            _ConstantValidator([]),
            {"processing": ["refuse", "deterministic.normalise_date"]},
        )


def test_audit_filenames_do_not_collide_within_one_second(tmp_path):
    # Same resource id repaired twice in the same second must still get
    # distinct audit files; the second-resolution timestamp alone cannot
    # guarantee that, so a random suffix does.
    repairer = _repairer(tmp_path, _ConstantValidator([]), {})
    first = repairer._open_audit({"resourceType": "Patient", "id": "same"})
    second = repairer._open_audit({"resourceType": "Patient", "id": "same"})
    assert first._destination != second._destination


@pytest.mark.parametrize("resource_id", ["../../../escaped", "a/b", "c:\\d?e"])
def test_audit_file_stays_in_the_audit_directory(tmp_path, resource_id):
    audit_dir = tmp_path / "audit"
    repairer = _repairer(audit_dir, _ConstantValidator([_error("x", "Patient")]), {})

    repairer.repair({"resourceType": "Patient", "id": resource_id})

    written = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert len(written) == 1
    assert written[0].parent == audit_dir


def test_mapped_but_unfixed_errors_are_reported_unresolved(tmp_path):
    # A mapped error that never gets fixed and an unmapped error together.
    # The unmapped error populates `unresolved` early; the mapped one must
    # still be reported when the loop exits via the stuck detector.
    mapped = _error("mapped-code", "Patient.a")
    unmapped = _error("other-code", "Patient.b")

    repairer = _repairer(
        tmp_path,
        _ConstantValidator([mapped, unmapped]),
        {"mapped-code": "stub.refuse"},
        _RefusingStrategy(),
    )
    result = repairer.repair({"resourceType": "Patient", "id": "x"})

    codes = {e.code for e in result.unresolved}
    assert "mapped-code" in codes
    assert "other-code" in codes


def test_repairer_without_config_fixes_processing_errors(tmp_path, monkeypatch):
    # HAPI's code for a malformed date. The default table must route it.
    monkeypatch.chdir(tmp_path)

    def rule(resource: dict[str, Any]) -> list[ValidationError]:
        if resource["birthDate"] == "1937-4-27":
            return [_error("processing", "Patient.birthDate")]
        return []

    result = Repairer(validator=_RuleValidator(rule)).repair(
        {"resourceType": "Patient", "birthDate": "1937-4-27"}
    )

    assert result.fixed_resource["birthDate"] == "1937-04-27"
    assert result.unresolved == []


def test_an_error_cleared_by_an_earlier_batch_is_not_dispatched(tmp_path):
    # A comma decimal raises two errors at different depths. Fixing the
    # deeper one clears the other, which used to be dispatched anyway and
    # fall through to whatever ends the chain, an LLM in the hybrid arm.
    def rule(resource: dict[str, Any]) -> list[ValidationError]:
        if resource["valueQuantity"]["value"] != "15,31":
            return []
        return [
            _error("processing", "Observation.valueQuantity.value", "not a valid decimal"),
            _error("processing", "Observation.valueQuantity", "must be a number"),
        ]

    class _LastResort(_CountingLLMStrategy):
        name = "stub.last_resort"

    fix = _Stub("stub.fix", "Observation.valueQuantity.value", 15.31)
    last_resort = _LastResort()
    registry_chain = {"processing": ["stub.fix", "stub.last_resort"]}
    repairer = _repairer(tmp_path, _RuleValidator(rule), registry_chain, fix, last_resort)

    result = repairer.repair(
        {"resourceType": "Observation", "valueQuantity": {"value": "15,31", "unit": "mg"}}
    )

    assert result.fixed_resource["valueQuantity"]["value"] == 15.31
    assert last_resort.calls == 0
    assert [action.strategy for action in result.audit] == ["stub.fix"]
