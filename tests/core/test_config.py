"""Tests for post-load config validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fhir_repair.core.config import LimitsConfig, RepairConfig


def test_valid_dispatch_table_and_limits():
    config = RepairConfig(
        strategies={
            "invalid-date-format": "deterministic.normalize_date",
            "invalid-code-binding": ["llm.suggest_terminology_match", "refuse"],
        },
    )
    assert config.strategies["invalid-date-format"] == "deterministic.normalize_date"


def test_empty_string_dispatch_entry_rejected():
    with pytest.raises(ValidationError, match="empty"):
        RepairConfig(strategies={"invalid-date-format": ""})


def test_empty_list_dispatch_entry_rejected():
    with pytest.raises(ValidationError, match="empty"):
        RepairConfig(strategies={"invalid-date-format": []})


def test_blank_strategy_id_in_list_rejected():
    with pytest.raises(ValidationError, match="empty strategy id"):
        RepairConfig(strategies={"invalid-date-format": ["deterministic.normalize_date", "  "]})


def test_zero_max_attempts_rejected():
    with pytest.raises(ValidationError, match="max_attempts"):
        RepairConfig(limits=LimitsConfig(max_attempts=0))


def test_zero_max_llm_calls_rejected():
    with pytest.raises(ValidationError, match="max_llm_calls_per_resource"):
        RepairConfig(limits=LimitsConfig(max_llm_calls_per_resource=0))


def test_llm_dispatch_without_provider_is_allowed():
    """A provider can be injected at runtime (see Repairer(llm_provider=...)),
    so the dispatch table may reference LLM strategies even when llm.provider
    is unset in config."""
    config = RepairConfig(strategies={"invalid-code-binding": "llm.suggest_terminology_match"})
    assert config.llm.provider is None
