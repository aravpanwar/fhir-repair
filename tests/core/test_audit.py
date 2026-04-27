"""Tests for the audit log writer and schema."""

from __future__ import annotations

import json
from pathlib import Path

from fhir_repair.core.audit import (
    SCHEMA_VERSION,
    AuditEntry,
    AuditWriter,
    hash_prompt,
)
from fhir_repair.core.models import RepairAction, ValidationError


def _sample_action() -> RepairAction:
    return RepairAction(
        error=ValidationError(
            code="invalid-date-format",
            severity="error",
            location="Patient.birthDate",
            message="bad date",
        ),
        strategy="deterministic.normalize_date",
        strategy_version="1.0.0",
        risk="low",
        permission_used="allow_reformat",
        before="1990-3-5",
        after="1990-03-05",
        explanation="Reformatted.",
    )


def test_writer_creates_destination_directory(tmp_path: Path):
    destination = tmp_path / "nested" / "audit.jsonl"
    with AuditWriter(
        destination=destination,
        resource_id="Patient/abc",
        resource_type="Patient",
        fhir_version="4.0.1",
        dispatch_version="1.0.0",
    ) as writer:
        writer.write_action(_sample_action())
        writer.write_summary(total_errors=1, fixed=1, unresolved=0, duration_ms=100)

    assert destination.exists()


def test_writer_emits_one_line_per_entry(tmp_path: Path):
    destination = tmp_path / "audit.jsonl"
    with AuditWriter(
        destination=destination,
        resource_id="Patient/abc",
        resource_type="Patient",
        fhir_version="4.0.1",
        dispatch_version="1.0.0",
    ) as writer:
        writer.write_action(_sample_action())
        writer.write_action(_sample_action())
        writer.write_summary(total_errors=2, fixed=2, unresolved=0, duration_ms=120)

    lines = destination.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 3
    for line in lines:
        # Every line must round-trip through the Pydantic schema.
        AuditEntry.model_validate_json(line)


def test_action_entry_carries_envelope_fields(tmp_path: Path):
    destination = tmp_path / "audit.jsonl"
    with AuditWriter(
        destination=destination,
        resource_id="Patient/abc",
        resource_type="Patient",
        fhir_version="4.0.1",
        dispatch_version="1.0.0",
    ) as writer:
        writer.write_action(_sample_action())
        writer.write_summary(total_errors=1, fixed=1, unresolved=0, duration_ms=10)

    first_line = destination.read_text(encoding="utf-8").splitlines()[0]
    entry = json.loads(first_line)
    assert entry["v"] == SCHEMA_VERSION
    assert entry["resource_id"] == "Patient/abc"
    assert entry["fhir_version"] == "4.0.1"
    assert entry["action"]["strategy"] == "deterministic.normalize_date"
    assert entry["llm"] is None


def test_hash_prompt_is_stable():
    assert hash_prompt("hello") == hash_prompt("hello")
    assert hash_prompt("hello") != hash_prompt("world")
    assert hash_prompt("hello").startswith("sha256:")
