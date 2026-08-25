"""Generic LLM strategy runner.

Wraps a prompt template, an LLMProvider, and (optionally) a RAG retriever
into a `Strategy`. The runner handles:

  - Loading and rendering the prompt template
  - Calling the provider with a structured PromptSegment list, marking
    stable segments for caching
  - Parsing the provider's response into a fix
  - Applying the fix to the resource and producing a RepairAction with
    full LLM provenance recorded

Specific LLM strategies (e.g. terminology binding) are instances of
`LLMStrategy` configured with the right prompt template and parser.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jinja2 import Template

from fhir_repair.core.audit import hash_prompt
from fhir_repair.core.fhirpath import delete_at_path, get_at_path, set_at_path
from fhir_repair.core.models import (
    PromptSegment,
    RepairAction,
    ValidationError,
)
from fhir_repair.llm.base import Completion, LLMProvider
from fhir_repair.strategies.base import refused
from fhir_repair.strategies.llm.rag import SpecRetriever

logger = logging.getLogger(__name__)


class _Delete:
    """Sentinel meaning "remove the element at the error path".

    A distinct object rather than None because None already means "the model
    declined to answer" and is handled as a refusal. Some fixes really are a
    removal: an invariant forbidding two fields from coexisting is satisfied
    by dropping one, and writing null in its place leaves the resource
    invalid.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "DELETE"


DELETE = _Delete()

# Type for a parser that takes the LLM's raw text and returns the new value
# at the error location. May raise to signal "could not parse" or
# "untrustworthy output"; the runner converts that into a refusal. May also
# return the DELETE sentinel to request removal instead of replacement.
ResponseParser = Callable[[str], Any]


def _default_parser(text: str) -> Any:
    """Parse `{"value": <new_value>}` JSON from the LLM response.

    Strict by design: anything that is not exactly this shape raises, which
    becomes a refusal in the runner. Free-text LLM output should not be
    silently treated as a fix.
    """
    obj = json.loads(text)
    if not isinstance(obj, dict) or "value" not in obj:
        raise ValueError("expected JSON object with 'value' key")
    return obj["value"]


class LLMStrategy:
    """A `Strategy` implementation backed by a prompt template + LLM provider."""

    def __init__(
        self,
        name: str,
        version: str,
        permission: str,
        risk: str,
        prompt_path: Path,
        prompt_version: str,
        provider: LLMProvider,
        retriever: SpecRetriever | None = None,
        parser: ResponseParser = _default_parser,
        system_prompt: str | None = None,
        max_retries: int = 3,
        backoff_base_s: float = 1.0,
        backoff_max_s: float = 30.0,
    ) -> None:
        self.name = name
        self.version = version
        self.permission = permission
        self.risk = risk

        self._template = Template(prompt_path.read_text(encoding="utf-8"))
        self._prompt_version = prompt_version
        self._provider = provider
        self._retriever = retriever
        self._parser = parser
        self._system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._backoff_max_s = backoff_max_s

    def apply(
        self,
        resource: dict[str, Any],
        error: ValidationError,
    ) -> RepairAction:
        before = get_at_path(resource, error.location)

        spec_excerpt = ""
        if self._retriever is not None:
            spec_excerpt = self._retriever.retrieve(error)

        rendered = self._template.render(
            resource=json.dumps(resource, separators=(",", ":")),
            error=error,
            spec_excerpt=spec_excerpt,
            current_value=before,
        )

        # Stable segments (cacheable across calls) come first; volatile
        # segments (per-resource content) come last. Anthropic caches the
        # prefix up to and including the last stable segment; OpenAI auto-
        # caches whatever prefix happens to repeat.
        segments = [
            PromptSegment(role="system", text=self._system_prompt, stable=True),
        ]
        if spec_excerpt:
            segments.append(PromptSegment(role="system", text=spec_excerpt, stable=True))
        segments.append(PromptSegment(role="user", text=rendered, stable=False))

        completion = self._call_with_retry(segments)
        if completion is None:
            # retry loop exhausted; refusal already returned below
            return refused(
                error,
                self.name,
                self.version,
                self.permission,
                before,
                "LLM call failed after all retries.",
            )
        latency_ms = completion.latency_ms

        try:
            new_value = self._parser(completion.text)
        except Exception as exc:
            return refused(
                error,
                self.name,
                self.version,
                self.permission,
                before,
                f"could not parse LLM response: {exc}",
            )

        if new_value is None:
            # The system prompt asks the model to answer {"value": null} when
            # it cannot determine the fix with confidence. Honour that as a
            # refusal instead of writing a literal null over the resource.
            return refused(
                error,
                self.name,
                self.version,
                self.permission,
                before,
                "LLM returned null, signalling it could not determine the value",
            )

        if new_value is DELETE:
            if not delete_at_path(resource, error.location):
                return refused(
                    error,
                    self.name,
                    self.version,
                    self.permission,
                    before,
                    "LLM asked to remove an element that is not present",
                )
            after: Any = None
            removed = True
            explanation = f"LLM ({completion.model}) removed the element at the error path."
        else:
            try:
                set_at_path(resource, error.location, new_value)
            except ValueError as exc:
                # Some errors are reported against the resource itself rather
                # than a field (invariants, whole-resource constraints), so
                # there is no path to assign to. That is a refusal, not a
                # crash: raising here would abort a whole benchmark run.
                return refused(
                    error,
                    self.name,
                    self.version,
                    self.permission,
                    before,
                    f"cannot write to error location {error.location!r}: {exc}",
                )
            after = new_value
            removed = False
            explanation = f"LLM ({completion.model}) produced replacement value."

        action = RepairAction(
            error=error,
            strategy=self.name,
            strategy_version=self.version,
            risk=self.risk,  # type: ignore[arg-type]
            permission_used=self.permission,
            before=before,
            after=after,
            removed=removed,
            explanation=explanation,
            llm={
                "provider": completion.provider,
                "model": completion.model,
                "prompt_version": self._prompt_version,
                "prompt_hash": hash_prompt(rendered),
                "input_tokens": completion.input_tokens,
                "output_tokens": completion.output_tokens,
                "cached_tokens": completion.cached_tokens,
                "latency_ms": latency_ms,
            },
        )
        return action

    def _call_with_retry(
        self,
        segments: list[PromptSegment],
    ) -> Completion | None:
        """Call the provider with exponential backoff on transient failures.

        Retries on connection errors and rate limits. Permanent failures
        (import errors, bad credentials, parse failures) do not retry.
        Returns None only when all attempts have been exhausted; the caller
        converts that into a refusal.
        """
        deadline = self._backoff_max_s
        for attempt in range(self._max_retries + 1):
            call_start = time.perf_counter()
            try:
                result = self._provider.complete(segments)
                # Overwrite latency with our own measurement so it
                # reflects the wall-clock cost including any retries.
                result.latency_ms = int((time.perf_counter() - call_start) * 1000)
                return result
            except (ImportError, ValueError) as exc:
                # Permanent configuration or dependency errors. Do not
                # retry; the caller will write a refusal with the message.
                logger.warning("permanent LLM error: %s", exc)
                return None
            except Exception as exc:
                logger.warning(
                    "LLM call attempt %d/%d failed: %s",
                    attempt + 1,
                    self._max_retries + 1,
                    exc,
                )
                if attempt == self._max_retries:
                    return None
                # Exponential backoff with full jitter. Base grows as
                # base * 2^attempt, clamped to backoff_max_s, then
                # multiplied by a uniform random factor in [0.5, 1.0].
                base = self._backoff_base_s * (2**attempt)
                capped = min(base, deadline)
                wait = capped * (0.5 + random.random() * 0.5)
                logger.debug("retrying in %.1fs", wait)
                time.sleep(wait)
        return None


# A short system prompt tuned for fix-the-broken-FHIR tasks. Sets the JSON
# response contract enforced by `_default_parser`.
_DEFAULT_SYSTEM_PROMPT = """\
You are a FHIR R4 repair assistant. Given a resource with a single
validation error and (optionally) the relevant spec excerpt, produce the
correct value for the path indicated by the error.

Respond with a single JSON object: {"value": <correct_value>}. Do not add
commentary, markdown formatting, or explanation. If you cannot determine
the correct value with high confidence, respond with
{"value": null}; the system will mark the case unresolved.

Never invent clinical content not present in the input. Reformat and
constrained-set selection are the only acceptable transformations.
"""


# Path to the prompt templates shipped with the package. Strategies built by
# `register_default_llm_strategies` use this directory; users can supply
# their own prompts by constructing `LLMStrategy` directly with a different
# `prompt_path`.
_PROMPTS_DIR = Path(__file__).parent / "prompts"


def register_default_llm_strategies(
    registry: Any,
    provider: LLMProvider,
    prompt_version: str = "v1",
    retriever: SpecRetriever | None = None,
    max_retries: int = 3,
    backoff_base_s: float = 1.0,
    backoff_max_s: float = 30.0,
) -> None:
    """Register the built-in LLM strategies on `registry`.

    Currently registers three:

      - `llm.suggest_terminology_match`: pick a code from a bound ValueSet
        when the user-provided value is interpretable (e.g., "M" maps to
        "male" under AdministrativeGender).
      - `llm.resolve_invariant`: drop an element that violates a
        cross-element invariant. Removal only; it cannot write a value.
      - `llm`: generic catch-all using a less specific prompt template.
        Use sparingly and pair with conservative hallucination_guard
        permissions.

    The terminology and generic strategies require
    `allow_bind_required_valueset`; the invariant strategy requires
    `allow_change_existing_clinical_value`, which is denied by default.
    Adjust the config's permissions to enable or disable.
    """
    # Imported here rather than at module scope to keep the import graph
    # between the runner and the individual strategies one-directional.
    from fhir_repair.strategies.llm import invariant as invariant_mod

    registry.register(
        LLMStrategy(
            name="llm.suggest_terminology_match",
            version="1.0.0",
            permission="allow_bind_required_valueset",
            risk="medium",
            prompt_path=_PROMPTS_DIR / "repair_terminology.v1.jinja",
            prompt_version=prompt_version,
            provider=provider,
            retriever=retriever,
            max_retries=max_retries,
            backoff_base_s=backoff_base_s,
            backoff_max_s=backoff_max_s,
        )
    )
    # Not an LLMStrategy: an invariant failure is reported against the
    # resource, so the element to remove has to come from the model's answer
    # rather than the error location. See invariant.py.
    registry.register(
        invariant_mod.InvariantStrategy(
            provider=provider,
            prompt_version=prompt_version,
        )
    )
    registry.register(
        LLMStrategy(
            name="llm",
            version="1.0.0",
            permission="allow_bind_required_valueset",
            risk="medium",
            prompt_path=_PROMPTS_DIR / "repair_unknown.v1.jinja",
            prompt_version=prompt_version,
            provider=provider,
            retriever=retriever,
            max_retries=max_retries,
            backoff_base_s=backoff_base_s,
            backoff_max_s=backoff_max_s,
        )
    )
