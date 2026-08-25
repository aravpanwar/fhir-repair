"""Tests for the Bedrock provider adapter.

These tests inject a stub `boto3` module via monkeypatching, so they run
without AWS credentials and without network access.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from fhir_repair.core.models import PromptSegment

_RESPONSE = {
    "output": {"message": {"content": [{"text": '{"value": "male"}'}]}},
    "usage": {
        "inputTokens": 120,
        "outputTokens": 30,
        "cacheReadInputTokens": 64,
    },
}


@pytest.fixture
def fake_boto3(monkeypatch):
    """Inject a stub `boto3` module and capture the Converse request."""
    captured: dict[str, Any] = {}

    class _Client:
        def converse(self, **kwargs):
            captured["request"] = kwargs
            return _RESPONSE

    def _client(service, **kwargs):
        captured["service"] = service
        captured["init"] = kwargs
        return _Client()

    fake_module = types.ModuleType("boto3")
    fake_module.client = _client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "boto3", fake_module)
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    return captured


def test_builds_bedrock_runtime_client(fake_boto3):
    from fhir_repair.llm.bedrock import BedrockProvider

    BedrockProvider()

    assert fake_boto3["service"] == "bedrock-runtime"
    assert fake_boto3["init"]["region_name"] == "us-east-1"


def test_no_api_key_is_read(fake_boto3, monkeypatch):
    """Bedrock uses the AWS credential chain; a stray LLM_API_KEY is ignored."""
    monkeypatch.setenv("LLM_API_KEY", "should-not-be-used")

    from fhir_repair.llm.bedrock import BedrockProvider

    BedrockProvider()

    assert "should-not-be-used" not in str(fake_boto3["init"].values())


def test_segments_map_to_converse_shape(fake_boto3):
    from fhir_repair.llm.bedrock import BedrockProvider

    provider = BedrockProvider()
    provider.complete(
        [
            PromptSegment(role="system", text="system prompt", stable=True),
            PromptSegment(role="user", text="user prompt", stable=False),
        ]
    )

    request = fake_boto3["request"]
    # A stable system segment is followed by a cachePoint marker.
    assert request["system"] == [
        {"text": "system prompt"},
        {"cachePoint": {"type": "default"}},
    ]
    assert request["messages"] == [{"role": "user", "content": [{"text": "user prompt"}]}]


def test_volatile_segment_gets_no_cache_point(fake_boto3):
    from fhir_repair.llm.bedrock import BedrockProvider

    provider = BedrockProvider()
    provider.complete(
        [
            PromptSegment(role="system", text="volatile", stable=False),
            PromptSegment(role="user", text="hi", stable=False),
        ]
    )

    assert fake_boto3["request"]["system"] == [{"text": "volatile"}]


def test_completion_reports_token_counts(fake_boto3):
    from fhir_repair.llm.bedrock import BedrockProvider

    provider = BedrockProvider()
    completion = provider.complete([PromptSegment(role="user", text="hi", stable=False)])

    assert completion.text == '{"value": "male"}'
    assert completion.input_tokens == 120
    assert completion.output_tokens == 30
    assert completion.cached_tokens == 64
    assert completion.provider == "bedrock"


def test_missing_usage_fields_report_zero():
    """Regions without prompt caching omit the cache counters entirely."""
    from fhir_repair.llm import bedrock

    assert bedrock._usage_field({}, "inputTokens") == 0
    assert bedrock._usage_field({"usage": {}}, "cacheReadInputTokens") == 0
    assert bedrock._extract_text({}) == ""


def test_supports_caching_is_true(fake_boto3):
    from fhir_repair.llm.bedrock import BedrockProvider

    assert BedrockProvider().supports_caching() is True


def test_missing_user_segment_raises(fake_boto3):
    from fhir_repair.llm.bedrock import BedrockProvider

    provider = BedrockProvider()
    with pytest.raises(ValueError, match="user segment"):
        provider.complete([PromptSegment(role="system", text="only system", stable=True)])


def test_missing_region_raises(fake_boto3, monkeypatch):
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    from fhir_repair.llm.bedrock import BedrockProvider

    with pytest.raises(ValueError, match="region"):
        BedrockProvider()


def test_non_text_blocks_are_ignored(fake_boto3):
    from fhir_repair.llm import bedrock

    response = {
        "output": {
            "message": {
                "content": [
                    {"toolUse": {"name": "x"}},
                    {"text": "kept"},
                ]
            }
        }
    }
    assert bedrock._extract_text(response) == "kept"
