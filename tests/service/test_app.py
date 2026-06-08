"""Tests for the optional FastAPI service.

These inject a fake repairer, so they exercise the HTTP envelope and error
mapping without a HAPI server. They are skipped when FastAPI is not
installed (the service extra is optional).
"""

from __future__ import annotations

import httpx
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from fhir_repair.core.models import (
    RepairAction,
    RepairResult,
    ValidationError,
)
from fhir_repair.service.app import create_app


def _sample_result() -> RepairResult:
    error = ValidationError(
        code="processing",
        severity="error",
        location="Patient.birthDate",
        message="bad date",
    )
    action = RepairAction(
        error=error,
        strategy="deterministic.normalize_date",
        strategy_version="1.0.0",
        risk="low",
        permission_used="allow_reformat",
        before="1990-3-5",
        after="1990-03-05",
        explanation="reformatted",
    )
    return RepairResult(
        fixed_resource={"resourceType": "Patient", "birthDate": "1990-03-05"},
        audit=[action],
        unresolved=[],
        duration_ms=5,
        metadata={"fhir_version": "4.0.1"},
    )


class _FakeRepairer:
    def __init__(self, result: RepairResult | None = None, error: Exception | None = None):
        self._result = result
        self._error = error
        self.seen: dict | None = None

    def repair(self, resource: dict) -> RepairResult:
        self.seen = resource
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _client(repairer: _FakeRepairer) -> TestClient:
    return TestClient(create_app(repairer=repairer))


def test_health():
    with _client(_FakeRepairer(_sample_result())) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_repair_returns_fixed_resource():
    repairer = _FakeRepairer(_sample_result())
    with _client(repairer) as client:
        response = client.post(
            "/repair",
            json={"resource": {"resourceType": "Patient", "birthDate": "1990-3-5"}},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["fixed_resource"]["birthDate"] == "1990-03-05"
    assert body["audit"][0]["strategy"] == "deterministic.normalize_date"
    assert body["audit"][0]["error"]["location"] == "Patient.birthDate"
    assert body["unresolved"] == []
    assert body["duration_ms"] == 5
    # The resource reached the repairer unchanged.
    assert repairer.seen == {"resourceType": "Patient", "birthDate": "1990-3-5"}


def test_repair_missing_resource_type_returns_422():
    with _client(_FakeRepairer(_sample_result())) as client:
        response = client.post("/repair", json={"resource": {"birthDate": "1990-3-5"}})
    assert response.status_code == 422
    assert "resourceType" in response.json()["detail"]


def test_repair_missing_envelope_returns_422():
    # No `resource` key at all: pydantic rejects the body.
    with _client(_FakeRepairer(_sample_result())) as client:
        response = client.post("/repair", json={"birthDate": "1990-3-5"})
    assert response.status_code == 422


def test_repair_validator_unreachable_returns_502():
    repairer = _FakeRepairer(error=httpx.ConnectError("connection refused"))
    with _client(repairer) as client:
        response = client.post(
            "/repair",
            json={"resource": {"resourceType": "Patient"}},
        )
    assert response.status_code == 502
    assert "validator request failed" in response.json()["detail"]
