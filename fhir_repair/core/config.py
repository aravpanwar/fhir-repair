"""Configuration loading.

The repair config is YAML at the boundary, validated by Pydantic into a
typed object the rest of the system uses. Environment variables in the
form `${VAR}` are expanded during load; missing variables become empty
strings, which the LLM provider adapter is responsible for rejecting.
"""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from fhir_repair.core.guard import HallucinationGuard

ENV_VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

# The dispatch table used when a config does not set `strategies`. HAPI
# reports most malformed values with the coarse issue code `processing`, so
# every deterministic strategy is tried in turn and each refuses when the
# value is not its kind. LLM strategies are opt-in and never in the default.
DEFAULT_STRATEGIES: dict[str, str | list[str]] = {
    "processing": [
        "deterministic.normalize_date",
        "deterministic.normalize_decimal",
        "deterministic.unwrap_singleton",
        "deterministic.normalize_telecom",
        "deterministic.normalize_codeable_concept",
        "deterministic.canonicalize_identifier_system",
    ],
}


def _default_strategies() -> dict[str, str | list[str]]:
    """A fresh copy, so one config editing its table cannot change another's."""
    return copy.deepcopy(DEFAULT_STRATEGIES)


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # All fields are Optional because YAML expansion of an unset
    # environment variable (e.g. ${LLM_PROVIDER}) produces an empty
    # value that parses as None. Allowing None at the type level lets
    # the config load cleanly when no LLM is configured; downstream
    # code (build_llm_provider) handles missing values explicitly.
    provider: str | None = None
    model: str | None = None
    prompt_version: str = "v1"
    endpoint: str | None = None
    api_key: str | None = None

    # Transient error retry, the only retry layer: the provider SDKs' own
    # retries are turned off so the two do not multiply. A failed call
    # returned no answer, so retrying cannot apply a fix twice. Connection
    # errors, timeouts, 408, 409, 429 and 5xx are retried; other 4xx
    # responses and unparseable answers are not, since backoff will not
    # change them.
    max_retries: int = 3
    backoff_base_s: float = 1.0
    backoff_max_s: float = 30.0

    # Passed to the provider on every call. `max_tokens` and `temperature`
    # override the adapter's defaults (a null `temperature` sends none);
    # anything else goes into the request body as given, for vendor
    # settings such as DeepSeek's `reasoning_effort`.
    options: dict[str, Any] = Field(default_factory=dict)


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

    # `max_attempts` bounds the validate-fix-revalidate loop.
    # `max_llm_calls_per_resource` bounds how many times an LLM strategy is
    # invoked for one resource; once it is spent, further LLM strategies
    # refuse. Neither replaces spending limits at the LLM provider.
    max_llm_calls_per_resource: int = 5
    max_attempts: int = 5


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_destination: str = "./audit-logs/"


class RepairConfig(BaseModel):
    """Top-level config object loaded from `repair-config.yaml`."""

    model_config = ConfigDict(extra="forbid")

    target_profile: str | None = None
    fhir_version: str = "4.0.1"
    hapi_version: str = "7.4.0"
    dispatch_version: str = "1.0.0"

    # Each error code maps to either a single strategy id or an ordered list
    # of strategy ids to try in turn. The dispatcher tries each strategy in
    # order; the first one that does not refuse wins. This lets a single
    # FHIR issue code (e.g. HAPI's coarse `processing`) route to several
    # specific strategies, each of which refuses cleanly when its
    # preconditions do not match.
    strategies: dict[str, str | list[str]] = Field(default_factory=_default_strategies)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    hallucination_guard: HallucinationGuardConfig = Field(default_factory=HallucinationGuardConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @model_validator(mode="after")
    def _validate_consistency(self) -> RepairConfig:
        """Post-load sanity checks that go beyond per-field types.

        These run after all fields have been parsed. They give early,
        readable errors for configurations that type-check but are
        semantically broken (e.g., empty dispatch chains).
        """
        # Dispatch table: every entry must be non-empty.
        for code, strategy_ids in self.strategies.items():
            ids = [strategy_ids] if isinstance(strategy_ids, str) else list(strategy_ids)
            if not ids:
                raise ValueError(
                    f"dispatch table entry for {code!r} is empty; "
                    f"use a non-empty string or list of strings"
                )
            for sid in ids:
                if not isinstance(sid, str) or not sid.strip():
                    raise ValueError(f"empty strategy id in dispatch table entry for {code!r}")

        # Limits must be positive.
        if self.limits.max_attempts < 1:
            raise ValueError("limits.max_attempts must be >= 1")
        if self.limits.max_llm_calls_per_resource < 1:
            raise ValueError("limits.max_llm_calls_per_resource must be >= 1")

        return self


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
