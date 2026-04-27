"""Tests for LLM provider construction and strategy registration.

Covers:

  - `build_llm_provider` switches on `provider`, raises sensibly for
    unknown / unimplemented providers.
  - `register_default_llm_strategies` adds the expected strategy names.
  - `Repairer` auto-registers LLM strategies when the dispatch table
    references them, and skips registration otherwise.
"""

from __future__ import annotations

import sys
import types

import pytest

from fhir_repair.core.config import LLMConfig, RepairConfig
from fhir_repair.llm import build_llm_provider
from fhir_repair.llm.base import Completion
from fhir_repair.strategies.llm import register_default_llm_strategies
from fhir_repair.strategies.registry import StrategyRegistry


class _StubProvider:
    """An in-memory LLM provider that returns a fixed response."""

    def complete(self, segments, **kwargs):
        return Completion(
            text='{"value": "stubbed"}',
            input_tokens=1,
            output_tokens=1,
            cached_tokens=0,
            model="stub",
            provider="stub",
        )

    def supports_caching(self) -> bool:
        return False


def test_build_llm_provider_unknown_raises():
    config = LLMConfig(provider="madeupcorp", model="x", api_key="dummy")
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        build_llm_provider(config)


def test_build_llm_provider_unimplemented_raises():
    config = LLMConfig(provider="openai", model="gpt-x", api_key="dummy")
    with pytest.raises(NotImplementedError, match="not yet implemented"):
        build_llm_provider(config)


def test_build_llm_provider_anthropic(monkeypatch):
    """Anthropic builds when the SDK is importable and an API key is present."""
    captured: dict = {}

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs
            self.messages = None

    fake_module = types.ModuleType("anthropic")
    fake_module.Anthropic = _FakeAnthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)

    config = LLMConfig(
        provider="anthropic",
        model="claude-test",
        api_key="sk-ant-test",
        endpoint="https://example.test",
    )
    provider = build_llm_provider(config)

    assert provider.supports_caching() is True
    assert captured["kwargs"]["api_key"] == "sk-ant-test"
    assert captured["kwargs"]["base_url"] == "https://example.test"


def test_register_default_llm_strategies_adds_expected_names():
    registry = StrategyRegistry()
    register_default_llm_strategies(registry, _StubProvider())
    names = registry.names()
    assert "llm" in names
    assert "llm.suggest_terminology_match" in names


def test_register_default_llm_strategies_metadata():
    registry = StrategyRegistry()
    register_default_llm_strategies(registry, _StubProvider())

    terminology = registry.get("llm.suggest_terminology_match")
    assert terminology.permission == "allow_bind_required_valueset"
    assert terminology.risk == "medium"

    generic = registry.get("llm")
    assert generic.permission == "allow_bind_required_valueset"
    assert generic.risk == "medium"


def test_repairer_skips_llm_registration_when_unused():
    """Without LLM strategies in the dispatch table, Repairer never builds a provider."""
    from fhir_repair.core.repairer import Repairer

    config = RepairConfig(
        strategies={"invalid-date-format": "deterministic.normalize_date"},
    )

    class _FakeValidator:
        def validate(self, resource, profile=None):
            return []

        def close(self):
            pass

    # Even with no LLM provider injected and no API key in env, this must
    # not raise: the dispatch table doesn't reference any LLM strategies.
    Repairer(validator=_FakeValidator(), config=config)


def test_repairer_registers_llm_when_dispatch_uses_llm():
    from fhir_repair.core.repairer import Repairer

    config = RepairConfig(
        strategies={
            "invalid-code-binding": "llm.suggest_terminology_match",
            "unknown-error": "llm",
        },
    )

    class _FakeValidator:
        def validate(self, resource, profile=None):
            return []

        def close(self):
            pass

    repairer = Repairer(
        validator=_FakeValidator(),
        config=config,
        llm_provider=_StubProvider(),
    )

    assert "llm" in repairer._registry.names()
    assert "llm.suggest_terminology_match" in repairer._registry.names()


def test_repairer_raises_when_llm_referenced_without_credentials(monkeypatch):
    """Dispatch table references LLM, no provider supplied, no API key."""
    from fhir_repair.core.repairer import Repairer

    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    # Inject a fake anthropic module so the import succeeds; the API key
    # check inside the adapter is what we want to trigger.
    class _FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = None

    fake_module = types.ModuleType("anthropic")
    fake_module.Anthropic = _FakeAnthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)

    config = RepairConfig(
        strategies={"unknown-error": "llm"},
        llm=LLMConfig(provider="anthropic", model="x"),
    )

    class _FakeValidator:
        def validate(self, resource, profile=None):
            return []

        def close(self):
            pass

    with pytest.raises(ValueError, match="API key"):
        Repairer(validator=_FakeValidator(), config=config)
