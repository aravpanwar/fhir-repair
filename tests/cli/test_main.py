"""Tests for the command-line interface.

The HAPI validator is replaced with a fake, so these run without a server.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from fhir_repair.cli import main as cli
from fhir_repair.core.models import ValidationError

_DATE_ERROR = ValidationError("processing", "error", "Patient.birthDate", "bad date")
_CONFIG = "strategies:\n  processing: deterministic.normalize_date\n"

runner = CliRunner()


class _FakeValidator:
    """Reports a date error until the birthDate is ISO 8601."""

    failure: Exception | None = None

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def validate(self, resource: dict[str, Any], profile: str | None = None) -> list:
        if self.failure is not None:
            raise self.failure
        return [] if resource.get("birthDate") == "1937-04-27" else [_DATE_ERROR]

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def fake_validator(monkeypatch):
    _FakeValidator.failure = None
    monkeypatch.setattr(cli, "HapiRestValidator", _FakeValidator)
    return _FakeValidator


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(_CONFIG, encoding="utf-8")
    patient = {"resourceType": "Patient", "id": "37-4407", "birthDate": "1937-4-27"}
    (tmp_path / "patient.json").write_text(json.dumps(patient), encoding="utf-8")
    return tmp_path


def test_fix_writes_the_repaired_resource(files):
    result = runner.invoke(
        cli.app, ["fix", "patient.json", "-c", "config.yaml", "-o", "fixed.json"]
    )

    assert result.exit_code == 0, result.output
    fixed = json.loads((files / "fixed.json").read_text(encoding="utf-8"))
    assert fixed["birthDate"] == "1937-04-27"
    # Actions and errors used to be subtracted from each other here.
    assert "1 fixes applied, 0 refused; 0 errors unresolved" in result.output


def test_fix_exits_1_when_errors_remain(files):
    (files / "config.yaml").write_text("strategies: {}\n", encoding="utf-8")

    result = runner.invoke(cli.app, ["fix", "patient.json", "-c", "config.yaml"])

    assert result.exit_code == 1
    assert "0 fixes applied, 0 refused; 1 errors unresolved" in result.output


@pytest.mark.parametrize(
    "setup, message",
    [
        (lambda f: (f / "patient.json").write_text("{not json"), "expected JSON in patient.json"),
        (lambda f: (f / "patient.json").write_text("[]"), "expected a FHIR resource"),
        (lambda f: (f / "patient.json").write_text('{"id": "x"}'), "expected a FHIR resource"),
        (lambda f: (f / "config.yaml").write_text("limits: [1"), "couldn't load config"),
        (lambda f: (f / "config.yaml").write_text("unknown_key: 1"), "couldn't load config"),
        (
            lambda f: (f / "config.yaml").write_text("strategies: {processing: nope}"),
            "couldn't set up the repair: unknown strategy 'nope'",
        ),
        (
            lambda _: setattr(_FakeValidator, "failure", httpx.ConnectError("refused")),
            "validator request to http://hapi.test/fhir failed",
        ),
    ],
)
def test_fix_reports_what_stopped_it(files, setup, message):
    # Each of these used to end in a raw traceback.
    setup(files)

    result = runner.invoke(
        cli.app,
        ["fix", "patient.json", "-c", "config.yaml", "--hapi-url", "http://hapi.test/fhir"],
    )

    assert result.exit_code == 2
    assert message in result.output
    assert "Traceback" not in result.output


def test_validate_lists_errors(files):
    result = runner.invoke(cli.app, ["validate", "patient.json"])

    assert result.exit_code == 1
    assert "Patient.birthDate" in result.output


def test_validate_reports_a_valid_resource(files):
    (files / "patient.json").write_text(
        json.dumps({"resourceType": "Patient", "birthDate": "1937-04-27"}), encoding="utf-8"
    )

    result = runner.invoke(cli.app, ["validate", "patient.json"])

    assert result.exit_code == 0
    assert "Resource is valid." in result.output


def test_validate_reports_an_unreachable_validator(files, fake_validator):
    fake_validator.failure = httpx.ConnectError("refused")

    result = runner.invoke(cli.app, ["validate", "patient.json", "--hapi-url", "http://hapi:1"])

    assert result.exit_code == 2
    assert "validator request to http://hapi:1 failed" in result.output
