"""The benchmark arms must differ only where the study says they do."""

from __future__ import annotations

from pathlib import Path

import pytest

from fhir_repair.core.config import DEFAULT_STRATEGIES, load_config

CONFIGS = Path(__file__).parents[2] / "benchmark" / "configs"


@pytest.fixture(autouse=True)
def llm_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_MODEL", "deepseek-flash")
    monkeypatch.setenv("LLM_API_KEY", "test-key")


def _chain(name: str) -> list[str]:
    return list(load_config(CONFIGS / f"{name}.yaml").strategies["processing"])


def test_deterministic_arm_is_the_shipped_default():
    assert _chain("deterministic") == DEFAULT_STRATEGIES["processing"]


def test_hybrid_is_the_deterministic_arm_then_the_llm_only_arm():
    assert _chain("hybrid") == _chain("deterministic") + _chain("llm-only")


def test_llm_arms_share_the_guard():
    guards = {
        name: load_config(CONFIGS / f"{name}.yaml").hallucination_guard
        for name in ("hybrid", "llm-only", "published-0.2.0")
    }
    assert len({guard.model_dump_json() for guard in guards.values()}) == 1
