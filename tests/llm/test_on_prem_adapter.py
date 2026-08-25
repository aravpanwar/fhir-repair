"""Tests for the on-prem provider adapter.

These tests inject a stub `openai` module via monkeypatching, so they run
without a server and without network access. The behaviour that matters
here is the guard against defaulting to a public endpoint.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from fhir_repair.core.models import PromptSegment


@pytest.fixture
def fake_openai(monkeypatch):
    """Inject a stub `openai` module and capture init plus request."""
    captured: dict[str, Any] = {}

    class _Usage:
        prompt_tokens = 90
        completion_tokens = 12

    class _Message:
        content = '{"value": "male"}'

    class _Choice:
        message = _Message()

    class _Response:
        usage = _Usage()

        def __init__(self):
            self.choices = [_Choice()]

    class _Completions:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return _Response()

    class _Chat:
        def __init__(self):
            self.completions = _Completions()

    class _OpenAI:
        def __init__(self, **kwargs):
            captured["init"] = kwargs
            self.chat = _Chat()

    fake_module = types.ModuleType("openai")
    fake_module.OpenAI = _OpenAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    return captured


def test_endpoint_is_required(fake_openai, monkeypatch):
    """Defaulting would ship clinical data to api.openai.com."""
    monkeypatch.delenv("LLM_ENDPOINT", raising=False)

    from fhir_repair.llm.on_prem import OnPremProvider

    with pytest.raises(ValueError, match="endpoint"):
        OnPremProvider(model="llama-3.1-8b")


def test_model_is_required(fake_openai):
    from fhir_repair.llm.on_prem import OnPremProvider

    with pytest.raises(ValueError, match="model"):
        OnPremProvider(endpoint="http://localhost:8000/v1")


def test_endpoint_from_environment(fake_openai, monkeypatch):
    monkeypatch.setenv("LLM_ENDPOINT", "http://vllm.internal:8000/v1")

    from fhir_repair.llm.on_prem import OnPremProvider

    OnPremProvider(model="llama-3.1-8b")

    assert fake_openai["init"]["base_url"] == "http://vllm.internal:8000/v1"


def test_runs_without_an_api_key(fake_openai, monkeypatch):
    """Unauthenticated local servers are the common case."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    from fhir_repair.llm.on_prem import OnPremProvider

    OnPremProvider(endpoint="http://localhost:8000/v1", model="llama-3.1-8b")

    # The SDK requires a non-empty key; a placeholder is sent.
    assert fake_openai["init"]["api_key"] == "not-used"


def test_api_key_is_used_when_supplied(fake_openai):
    from fhir_repair.llm.on_prem import OnPremProvider

    OnPremProvider(
        endpoint="http://localhost:8000/v1",
        model="llama-3.1-8b",
        api_key="gateway-token",
    )

    assert fake_openai["init"]["api_key"] == "gateway-token"


def test_segments_map_to_messages(fake_openai):
    from fhir_repair.llm.on_prem import OnPremProvider

    provider = OnPremProvider(endpoint="http://localhost:8000/v1", model="llama-3.1-8b")
    provider.complete(
        [
            PromptSegment(role="system", text="system prompt", stable=True),
            PromptSegment(role="user", text="user prompt", stable=False),
        ]
    )

    assert fake_openai["request"]["messages"] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "user prompt"},
    ]
    assert fake_openai["request"]["model"] == "llama-3.1-8b"


def test_completion_reports_token_counts(fake_openai):
    from fhir_repair.llm.on_prem import OnPremProvider

    provider = OnPremProvider(endpoint="http://localhost:8000/v1", model="llama-3.1-8b")
    completion = provider.complete([PromptSegment(role="user", text="hi", stable=False)])

    assert completion.input_tokens == 90
    assert completion.output_tokens == 12
    assert completion.provider == "on-prem"
    assert completion.model == "llama-3.1-8b"


def test_missing_user_segment_raises(fake_openai):
    from fhir_repair.llm.on_prem import OnPremProvider

    provider = OnPremProvider(endpoint="http://localhost:8000/v1", model="llama-3.1-8b")
    with pytest.raises(ValueError, match="user segment"):
        provider.complete([PromptSegment(role="system", text="only system", stable=True)])


def test_supports_caching_is_false(fake_openai):
    from fhir_repair.llm.on_prem import OnPremProvider

    provider = OnPremProvider(endpoint="http://localhost:8000/v1", model="llama-3.1-8b")
    assert provider.supports_caching() is False
