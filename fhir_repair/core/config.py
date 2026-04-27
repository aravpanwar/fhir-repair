"""Configuration loading.

The repair config is YAML at the boundary, validated by Pydantic into a
typed object the rest of the system uses. Environment variables in the
form `${VAR}` are expanded during load; missing variables become empty
strings, which the LLM provider adapter is responsible for rejecting.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from fhir_repair.core.guard import HallucinationGuard

ENV_VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    temperature: float = 0.0
    prompt_version: str = "v1"
    endpoint: str | None = None
    api_key: str | None = None


class TerminologyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: Literal["none", "tx-fhir-org", "hapi-local", "custom"] = "none"
    endpoint: str | None = None
    cache_dir: str | None = None


class HallucinationGuardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_reformat: bool = True
    allow_bind_required_valueset: bool = True
    allow_bind_extensible_valueset: bool = False
    allow_add_missing_required_field: bool = False
    allow_change_existing_clinical_value: bool = False

    def to_guard(self) -> HallucinationGuard:
        return HallucinationGuard(
            allow_reformat=self.allow_reformat,
            allow_bind_required_valueset=self.allow_bind_required_valueset,
            allow_bind_extensible_valueset=self.allow_bind_extensible_valueset,
            allow_add_missing_required_field=self.allow_add_missing_required_field,
            allow_change_existing_clinical_value=self.allow_change_existing_clinical_value,
        )


class LimitsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Bounds the validate-fix-revalidate loop. This is a correctness limit
    # (preventing infinite loops), not a cost limit. Cost is the deployer's
    # responsibility at the LLM provider level.
    max_llm_calls_per_resource: int = 5
    max_attempts: int = 5


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_destination: str = "./audit-logs/"
    log_level: Literal["debug", "info", "warning", "error"] = "info"


class RepairConfig(BaseModel):
    """Top-level config object loaded from `repair-config.yaml`."""

    model_config = ConfigDict(extra="forbid")

    target_profile: str | None = None
    fhir_version: str = "4.0.1"
    hapi_version: str = "7.4.0"
    dispatch_version: str = "1.0.0"

    strategies: dict[str, str] = Field(default_factory=dict)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    terminology: TerminologyConfig = Field(default_factory=TerminologyConfig)
    hallucination_guard: HallucinationGuardConfig = Field(default_factory=HallucinationGuardConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def load_config(path: str | Path) -> RepairConfig:
    """Load and validate a repair config file."""
    raw = Path(path).read_text(encoding="utf-8")
    expanded = _expand_env(raw)
    data: dict[str, Any] = yaml.safe_load(expanded) or {}
    return RepairConfig.model_validate(data)


def _expand_env(text: str) -> str:
    """Replace `${VAR}` placeholders with environment variable values.

    Missing variables become empty strings. This lets the file load even
    when secrets are not yet set; downstream code (LLM provider) rejects
    empty credentials with a clear error.
    """

    def repl(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), "")

    return ENV_VAR_RE.sub(repl, text)
