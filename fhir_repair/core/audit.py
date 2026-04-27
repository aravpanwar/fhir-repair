"""Audit log writer and schema.

Every repair run writes one JSON Lines file per resource. Each repair
action becomes one line; a summary line closes the run. The schema is
sealed at v1 (see docs/audit-log-schema.md) and changes only additively.

The Pydantic model `AuditEntry` is exposed so consumers can validate logs
without parsing by hand.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fhir_repair.core.models import RepairAction, ValidationError

SCHEMA_VERSION: int = 1


class AuditError(BaseModel):
    """Validation error as recorded in the audit log."""

    model_config = ConfigDict(extra="ignore")

    code: str
    severity: str
    location: str
    message: str


class AuditAction(BaseModel):
    """The applied (or refused) repair, as written to the audit log."""

    model_config = ConfigDict(extra="ignore")

    strategy: str
    strategy_version: str
    risk: Literal["low", "medium", "high", "refused"]
    permission_used: str
    error: AuditError
    before: Any = None
    after: Any = None
    explanation: str


class AuditLLM(BaseModel):
    """LLM call provenance, present when the action came from an LLM strategy."""

    model_config = ConfigDict(extra="ignore")

    provider: str
    model: str
    prompt_version: str
    prompt_hash: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    latency_ms: int = 0


class AuditSummary(BaseModel):
    """Resource-level summary, written as the last line of an audit file."""

    model_config = ConfigDict(extra="ignore")

    total_errors: int
    fixed: int
    unresolved: int
    duration_ms: int


class AuditEntry(BaseModel):
    """One line of an audit log, action or summary.

    Exactly one of `action` or `summary` is present.
    """

    model_config = ConfigDict(extra="ignore")

    v: int = Field(SCHEMA_VERSION, description="Audit schema version.")
    ts: str
    resource_id: str
    resource_type: str | None = None
    fhir_version: str | None = None
    dispatch_version: str | None = None

    action: AuditAction | None = None
    llm: AuditLLM | None = None
    summary: AuditSummary | None = None


def now_iso() -> str:
    """ISO 8601 UTC timestamp with second precision, no fractional seconds."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_prompt(text: str) -> str:
    """Stable hash of a rendered prompt, recorded for reproducibility."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


class AuditWriter:
    """Append-only writer for a per-resource audit log file.

    Use as a context manager. Every call to `write_action` produces one line;
    `write_summary` produces one final line and the writer is closed.
    """

    def __init__(
        self,
        destination: Path,
        resource_id: str,
        resource_type: str,
        fhir_version: str,
        dispatch_version: str,
    ) -> None:
        self._destination = destination
        self._resource_id = resource_id
        self._resource_type = resource_type
        self._fhir_version = fhir_version
        self._dispatch_version = dispatch_version
        self._fh: Any = None

    def __enter__(self) -> AuditWriter:
        self._destination.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self._destination.open("a", encoding="utf-8")
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def write_action(self, action: RepairAction) -> None:
        """Write one action entry."""
        entry = self._envelope()
        entry["action"] = _action_to_dict(action)
        entry["llm"] = action.llm
        self._write(entry)

    def write_summary(
        self,
        total_errors: int,
        fixed: int,
        unresolved: int,
        duration_ms: int,
    ) -> None:
        """Write the closing summary entry."""
        entry = self._envelope()
        entry["summary"] = {
            "total_errors": total_errors,
            "fixed": fixed,
            "unresolved": unresolved,
            "duration_ms": duration_ms,
        }
        self._write(entry)

    def _envelope(self) -> dict[str, Any]:
        return {
            "v": SCHEMA_VERSION,
            "ts": now_iso(),
            "resource_id": self._resource_id,
            "resource_type": self._resource_type,
            "fhir_version": self._fhir_version,
            "dispatch_version": self._dispatch_version,
        }

    def _write(self, entry: dict[str, Any]) -> None:
        if self._fh is None:
            raise RuntimeError("AuditWriter used outside its context manager")
        json.dump(entry, self._fh, separators=(",", ":"), default=str)
        self._fh.write("\n")
        self._fh.flush()


def _action_to_dict(action: RepairAction) -> dict[str, Any]:
    """Convert a RepairAction to its audit log JSON shape."""
    return {
        "strategy": action.strategy,
        "strategy_version": action.strategy_version,
        "risk": action.risk,
        "permission_used": action.permission_used,
        "error": _error_to_dict(action.error),
        "before": action.before,
        "after": action.after,
        "explanation": action.explanation,
    }


def _error_to_dict(error: ValidationError) -> dict[str, Any]:
    return {
        "code": error.code,
        "severity": error.severity,
        "location": error.location,
        "message": error.message,
    }
