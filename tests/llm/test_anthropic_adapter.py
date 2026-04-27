"""Tests for the Anthropic provider adapter.

These tests use a fake `anthropic` client injected via monkeypatching, so
they run without an API key and without network access. The real-API
integration test is in CI, gated behind the `llm` marker.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from fhir_repair.core.models import PromptSegment


@pytest.fixture
def fake_anthropic(monkeypatch):
    """Inject a stub `anthropic` module into sys.modules.

    Captures the request payload so tests can assert that stable segments
    received `cache_control` markers and that system/user segments were
    routed to the right top-level argument.
    """
    captured: dict[str, Any] = {}

    class _Usage:
        input_tokens = 100
        output_tokens = 20
        cache_read_input_tokens = 80

    class _ContentBlock:
        type = "text"
        text = '{"value": "male"}'

    class _Response:
        usage = _Usage()

        def __init__(self):
            self.content = [_ContentBlock()]

    class _Messages:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return _Response()

    class _Anthropic:
        def __init__(self, **kwargs):
            captured["init"] = kwargs
            self.messages = _Messages()

    fake_module = types.ModuleType("anthropic")
    fake_module.Anthropic = _Anthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)

    return captured


def test_stable_segments_get_cache_control(fake_anthropic, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    from fhir_repair.llm.anthropic import AnthropicProvider

    provider = AnthropicProvider()
    provider.complete(
        [
            PromptSegment(role="system", text="stable system", stable=True),
            PromptSegment(role="user", text="volatile user", stable=False),
        ]
    )

    request = fake_anthropic["request"]
    system_blocks = request["system"]
    user_blocks = request["messages"][0]["content"]

    assert system_blocks[0]["cache_control"] == {"type": "ephemeral"}
    # Non-stable user segment must not have a cache_control marker.
    assert "cache_control" not in user_blocks[0]


def test_completion_reports_token_counts(fake_anthropic, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    from fhir_repair.llm.anthropic import AnthropicProvider

    provider = AnthropicProvider()
    completion = provider.complete([PromptSegment(role="user", text="hi", stable=False)])

    assert completion.input_tokens == 100
    assert completion.output_tokens == 20
    assert completion.cached_tokens == 80
    assert completion.provider == "anthropic"


def test_missing_user_segment_raises(fake_anthropic, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    from fhir_repair.llm.anthropic import AnthropicProvider

    provider = AnthropicProvider()
    with pytest.raises(ValueError, match="user segment"):
        provider.complete([PromptSegment(role="system", text="only system", stable=True)])


def test_missing_api_key_raises(fake_anthropic, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from fhir_repair.llm.anthropic import AnthropicProvider

    with pytest.raises(ValueError, match="API key"):
        AnthropicProvider()


def test_supports_caching_is_true(fake_anthropic, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    from fhir_repair.llm.anthropic import AnthropicProvider

    provider = AnthropicProvider()
    assert provider.supports_caching() is True
