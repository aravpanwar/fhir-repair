"""Tests for the OpenAI provider adapter.

These tests inject a stub `openai` module via monkeypatching, so they run
without an API key and without network access. The real-API integration
test is in CI, gated behind the `llm` marker.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from fhir_repair.core.models import PromptSegment


@pytest.fixture
def fake_openai(monkeypatch):
    """Inject a stub `openai` module into sys.modules.

    Captures the request payload so tests can assert how segments were
    mapped onto the Chat Completions messages list.
    """
    captured: dict[str, Any] = {}

    class _PromptTokensDetails:
        cached_tokens = 64

    class _Usage:
        prompt_tokens = 120
        completion_tokens = 30
        prompt_tokens_details = _PromptTokensDetails()

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


def test_segments_map_to_messages(fake_openai, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    from fhir_repair.llm.openai import OpenAIProvider

    provider = OpenAIProvider()
    provider.complete(
        [
            PromptSegment(role="system", text="system prompt", stable=True),
            PromptSegment(role="user", text="user prompt", stable=False),
        ]
    )

    messages = fake_openai["request"]["messages"]
    assert messages == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "user prompt"},
    ]


def test_completion_reports_token_counts(fake_openai, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    from fhir_repair.llm.openai import OpenAIProvider

    provider = OpenAIProvider()
    completion = provider.complete([PromptSegment(role="user", text="hi", stable=False)])

    assert completion.input_tokens == 120
    assert completion.output_tokens == 30
    assert completion.cached_tokens == 64
    assert completion.provider == "openai"


def test_missing_user_segment_raises(fake_openai, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    from fhir_repair.llm.openai import OpenAIProvider

    provider = OpenAIProvider()
    with pytest.raises(ValueError, match="user segment"):
        provider.complete([PromptSegment(role="system", text="only system", stable=True)])


def test_missing_api_key_raises(fake_openai, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from fhir_repair.llm.openai import OpenAIProvider

    with pytest.raises(ValueError, match="API key"):
        OpenAIProvider()


def test_supports_caching_is_false(fake_openai, monkeypatch):
    # OpenAI caches prefixes automatically; the adapter applies no primitive.
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    from fhir_repair.llm.openai import OpenAIProvider

    provider = OpenAIProvider()
    assert provider.supports_caching() is False


def test_sdk_retries_are_off(fake_openai, monkeypatch):
    # The strategies retry transient failures; the SDK retrying as well
    # turned three retries into up to twelve requests.
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    from fhir_repair.llm.openai import OpenAIProvider

    OpenAIProvider()

    assert fake_openai["init"]["max_retries"] == 0


def test_options_reach_the_request(fake_openai, monkeypatch):
    # Vendor settings such as DeepSeek's reasoning_effort have no SDK
    # keyword, so they travel in the body; a null temperature sends none.
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    from fhir_repair.llm.openai import OpenAIProvider

    OpenAIProvider().complete(
        [PromptSegment(role="user", text="hi", stable=False)],
        max_tokens=4000,
        temperature=None,
        reasoning_effort="none",
    )

    request = fake_openai["request"]
    assert request["max_tokens"] == 4000
    assert "temperature" not in request
    assert request["extra_body"] == {"reasoning_effort": "none"}


def test_completion_records_the_model_the_server_reports(fake_openai, monkeypatch):
    # DeepSeek serves a retired id from its successor; the audit should say
    # which model actually answered.
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    from fhir_repair.llm.openai import OpenAIProvider

    provider = OpenAIProvider(model="requested-alias")
    original_create = provider._client.chat.completions.create

    def create(**kwargs):
        response = original_create(**kwargs)
        response.model = "served-model-1017"
        return response

    provider._client.chat.completions.create = create
    completion = provider.complete([PromptSegment(role="user", text="hi", stable=False)])

    assert completion.model == "served-model-1017"
