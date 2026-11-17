"""Tests for the generic LLM strategy runner.

These use an in-memory stub provider, so they run without an API key or
network access.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fhir_repair.core.models import PromptSegment, ValidationError
from fhir_repair.llm.base import Completion
from fhir_repair.strategies.llm.runner import LLMStrategy

_PROMPT = (
    Path(__file__).parents[2]
    / "fhir_repair"
    / "strategies"
    / "llm"
    / "prompts"
    / "repair_unknown.v1.jinja"
)


class _StubProvider:
    """Returns a fixed response text for every call."""

    def __init__(self, text: str) -> None:
        self._text = text

    def complete(self, segments: list[PromptSegment], **kwargs: object) -> Completion:
        return Completion(
            text=self._text,
            input_tokens=1,
            output_tokens=1,
            model="stub",
            provider="stub",
        )

    def supports_caching(self) -> bool:
        return False


def _strategy(text: str) -> LLMStrategy:
    return LLMStrategy(
        name="llm",
        version="1.0.0",
        permission="allow_bind_required_valueset",
        risk="medium",
        prompt_path=_PROMPT,
        prompt_version="v1",
        provider=_StubProvider(text),
    )


def _error() -> ValidationError:
    return ValidationError(
        code="invalid-code-binding",
        severity="error",
        location="Patient.gender",
        message="",
    )


def test_applies_parsed_value():
    resource = {"resourceType": "Patient", "gender": "M"}
    action = _strategy('{"value": "male"}').apply(resource, _error())
    assert action.risk == "medium"
    assert action.after == "male"
    assert resource["gender"] == "male"


def test_null_value_is_refused_not_written():
    # The model signalling low confidence with {"value": null} must not
    # overwrite the resource with a literal null.
    resource = {"resourceType": "Patient", "gender": "M"}
    action = _strategy('{"value": null}').apply(resource, _error())
    assert action.risk == "refused"
    assert resource["gender"] == "M"


def test_unparseable_response_is_refused():
    resource = {"resourceType": "Patient", "gender": "M"}
    action = _strategy("not json").apply(resource, _error())
    assert action.risk == "refused"
    assert resource["gender"] == "M"


def test_unwritable_error_location_is_refused():
    """A whole-resource error has no field to assign to.

    Raising here used to abort an entire benchmark run partway through.
    """
    from fhir_repair.strategies.llm.runner import _PROMPTS_DIR

    class _Provider:
        def complete(self, segments, **kwargs):
            return Completion(
                text='{"value": "anything"}',
                input_tokens=1,
                output_tokens=1,
                cached_tokens=0,
                model="stub",
                provider="stub",
            )

        def supports_caching(self) -> bool:
            return False

    strategy = LLMStrategy(
        name="llm",
        version="1.0.0",
        permission="allow_reformat",
        risk="medium",
        prompt_path=_PROMPTS_DIR / "repair_unknown.v1.jinja",
        prompt_version="v1",
        provider=_Provider(),
    )
    resource = {"resourceType": "Encounter", "status": "finished"}
    error = ValidationError("processing", "error", "Encounter", "whole-resource constraint")

    action = strategy.apply(resource, error)

    assert action.risk == "refused"
    assert resource == {"resourceType": "Encounter", "status": "finished"}


class _FlakyProvider(_StubProvider):
    """Raises `failure` for the first `failures` calls, then answers."""

    def __init__(self, failures: int, failure: Exception) -> None:
        super().__init__('{"value": "male"}')
        self.calls = 0
        self._failures = failures
        self._failure = failure

    def complete(self, segments: list[PromptSegment], **kwargs: object) -> Completion:
        self.calls += 1
        if self.calls <= self._failures:
            raise self._failure
        return super().complete(segments, **kwargs)


def _flaky_strategy(provider: _FlakyProvider, waits: list[float], monkeypatch) -> LLMStrategy:
    monkeypatch.setattr("fhir_repair.strategies.llm.runner.time.sleep", waits.append)
    return LLMStrategy(
        name="llm",
        version="1.0.0",
        permission="allow_bind_required_valueset",
        risk="medium",
        prompt_path=_PROMPT,
        prompt_version="v1",
        provider=provider,
        max_retries=3,
        backoff_base_s=1.0,
        backoff_max_s=3.0,
    )


def test_transient_failures_are_retried_with_capped_backoff(monkeypatch):
    provider = _FlakyProvider(3, ConnectionError("reset"))
    waits: list[float] = []
    resource = {"resourceType": "Patient", "gender": "M"}

    action = _flaky_strategy(provider, waits, monkeypatch).apply(resource, _error())

    assert action.risk != "refused"
    assert resource["gender"] == "male"
    assert provider.calls == 4
    # Full jitter keeps each wait between half and all of 1, 2, then 4
    # capped to 3 seconds.
    for wait, ceiling in zip(waits, [1.0, 2.0, 3.0], strict=True):
        assert ceiling / 2 <= wait <= ceiling


def test_retries_give_up_with_a_refusal(monkeypatch):
    provider = _FlakyProvider(10, ConnectionError("reset"))
    resource = {"resourceType": "Patient", "gender": "M"}

    action = _flaky_strategy(provider, [], monkeypatch).apply(resource, _error())

    assert action.risk == "refused"
    assert resource["gender"] == "M"
    assert provider.calls == 4


def test_permanent_errors_are_not_retried(monkeypatch):
    provider = _FlakyProvider(1, ValueError("bad request"))
    waits: list[float] = []

    action = _flaky_strategy(provider, waits, monkeypatch).apply(
        {"resourceType": "Patient", "gender": "M"}, _error()
    )

    assert action.risk == "refused"
    assert provider.calls == 1
    assert waits == []


class _StatusError(Exception):
    """Shaped like the anthropic and openai SDK errors."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _BotocoreError(Exception):
    """Shaped like botocore's ClientError."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.response = {"ResponseMetadata": {"HTTPStatusCode": status_code}}


@pytest.mark.parametrize(
    "failure, retried",
    [
        (_StatusError(400), False),  # bad request: the same every time
        (_StatusError(401), False),  # bad key
        (_StatusError(404), False),  # unknown model
        (_StatusError(408), True),
        (_StatusError(429), True),
        (_StatusError(503), True),
        (_BotocoreError(403), False),
        (_BotocoreError(429), True),
        (TimeoutError("read timed out"), True),
        (TypeError("bad argument"), False),
    ],
)
def test_only_transient_errors_are_retried(monkeypatch, failure, retried):
    # 4xx responses used to be retried with backoff like a dropped
    # connection, although nothing about them changes between attempts.
    provider = _FlakyProvider(1, failure)

    action = _flaky_strategy(provider, [], monkeypatch).apply(
        {"resourceType": "Patient", "gender": "M"}, _error()
    )

    assert provider.calls == (2 if retried else 1)
    assert (action.risk != "refused") is retried
