"""Tests for the Repairer orchestration loop.

These use fake validators and stub strategies, so they exercise the loop's
bookkeeping (rollback, unresolved accounting) without a HAPI server.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fhir_repair.core.config import LoggingConfig, RepairConfig
from fhir_repair.core.fhirpath import delete_at_path, get_at_path, set_at_path
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


def _repairer(tmp_path, validator, strategies: dict[str, str], *stubs: Any) -> Repairer:
    registry = StrategyRegistry()
    registry.register_module(identifier)
    for stub in stubs:
        registry.register(stub)
    config = RepairConfig(
        strategies=strategies,
        logging=LoggingConfig(audit_destination=str(tmp_path)),
    )
    return Repairer(validator=validator, registry=registry, config=config)


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
    assert {action.risk for action in result.audit} == {"refused"}
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


def test_audit_filenames_do_not_collide_within_one_second(tmp_path):
    # Same resource id repaired twice in the same second must still get
    # distinct audit files; the second-resolution timestamp alone cannot
    # guarantee that, so a random suffix does.
    repairer = _repairer(tmp_path, _ConstantValidator([]), {})
    first = repairer._open_audit({"resourceType": "Patient", "id": "same"})
    second = repairer._open_audit({"resourceType": "Patient", "id": "same"})
    assert first._destination != second._destination


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
