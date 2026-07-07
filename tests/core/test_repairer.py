"""Tests for the Repairer orchestration loop.

These use a fake validator that returns a fixed error set, so they exercise
the loop's bookkeeping (unresolved accounting, rollback) without a HAPI
server.
"""

from __future__ import annotations

from fhir_repair.core.config import LoggingConfig, RepairConfig
from fhir_repair.core.models import RepairAction, ValidationError
from fhir_repair.core.repairer import Repairer
from fhir_repair.strategies.base import refused
from fhir_repair.strategies.registry import StrategyRegistry


class _ConstantValidator:
    """Returns the same error list on every call, forcing the stuck detector."""

    def __init__(self, errors: list[ValidationError]) -> None:
        self._errors = errors

    def validate(self, resource: dict, profile: str | None = None) -> list[ValidationError]:
        return list(self._errors)

    def close(self) -> None:
        pass


class _RefusingStrategy:
    name = "stub.refuse"
    version = "1.0.0"
    permission = "allow_reformat"
    risk = "low"

    def apply(self, resource: dict, error: ValidationError) -> RepairAction:
        return refused(error, self.name, self.version, self.permission, None, "always refuses")


def _config(tmp_path) -> RepairConfig:
    return RepairConfig(
        strategies={"mapped-code": "stub.refuse"},
        logging=LoggingConfig(audit_destination=str(tmp_path)),
    )


class _CountingStrategy:
    name = "stub.count"
    version = "1.0.0"
    permission = "allow_reformat"
    risk = "low"

    def __init__(self) -> None:
        self.calls = 0

    def apply(self, resource: dict, error: ValidationError) -> RepairAction:
        self.calls += 1
        return refused(error, self.name, self.version, self.permission, None, "counting")


def test_reapply_writes_recorded_value_without_reinvoking_strategy(tmp_path):
    # On rollback retry, the recorded `after` is written directly. Re-invoking
    # the strategy would risk a divergent (and, for an LLM, billable) result.
    counting = _CountingStrategy()
    registry = StrategyRegistry()
    registry.register(counting)

    repairer = Repairer(
        validator=_ConstantValidator([]),
        registry=registry,
        config=_config(tmp_path),
    )

    resource = {"resourceType": "Patient", "gender": "M"}
    action = RepairAction(
        error=ValidationError("c", "error", "Patient.gender", ""),
        strategy="stub.count",
        strategy_version="1.0.0",
        risk="low",
        permission_used="allow_reformat",
        before="M",
        after="male",
        explanation="",
    )

    repairer._reapply_action(resource, action)

    assert resource["gender"] == "male"
    assert counting.calls == 0


def test_mapped_but_unfixed_errors_are_reported_unresolved(tmp_path):
    # A mapped error that never gets fixed and an unmapped error together.
    # The unmapped error populates `unresolved` early; the mapped one must
    # still be reported when the loop exits via the stuck detector.
    mapped = ValidationError("mapped-code", "error", "Patient.a", "")
    unmapped = ValidationError("other-code", "error", "Patient.b", "")

    registry = StrategyRegistry()
    registry.register(_RefusingStrategy())

    repairer = Repairer(
        validator=_ConstantValidator([mapped, unmapped]),
        registry=registry,
        config=_config(tmp_path),
    )
    result = repairer.repair({"resourceType": "Patient", "id": "x"})

    codes = {e.code for e in result.unresolved}
    assert "mapped-code" in codes
    assert "other-code" in codes
