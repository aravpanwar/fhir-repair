"""LLM strategy for FHIR invariant violations.

An invariant is a constraint spanning more than one element, expressed in the
spec as a FHIRPath expression: Observation's obs-6 forbids `dataAbsentReason`
when a value is present, and so on. Unlike a malformed date there is no
single correct rewrite. The validator says "these elements cannot coexist"
and the repair is a judgement about which one to drop.

That judgement is why this is an LLM strategy rather than a deterministic
one. The fix is deliberately narrow: it may only *remove* an element, never
write a new value. Removal cannot invent clinical content, which keeps the
highest-risk failure mode off the table entirely.

Why this needs its own `apply` rather than the generic runner: HAPI reports
an invariant failure against the resource, not against the offending field.
`Observation.dataAbsentReason` and `Observation.value[x]` are both implicated
and the error location is just `Observation`. A strategy that could only act
on the error location would be asking whether to delete the whole resource,
so the model has to name which element to remove, and the name has to be
checked against the resource before anything is deleted.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from jinja2 import Template

from fhir_repair.core.audit import hash_prompt
from fhir_repair.core.fhirpath import delete_at_path, get_at_path
from fhir_repair.core.models import PromptSegment, RepairAction, ValidationError
from fhir_repair.llm.base import LLMProvider
from fhir_repair.strategies.base import refused

logger = logging.getLogger(__name__)

NAME = "llm.resolve_invariant"
VERSION = "2.0.0"

# Removing an element the submitter provided is a change to existing
# content, so it needs the clinical-value permission rather than the
# reformat one. It is off by default in the guard, which is the intended
# posture: dropping data should be an explicit opt-in.
PERMISSION = "allow_change_existing_clinical_value"

RISK = "high"

_PROMPT_PATH = Path(__file__).parent / "prompts" / "repair_invariant.v1.jinja"


def parse_invariant_response(text: str) -> str | None:
    """Parse the response contract, returning the element to remove.

    Accepts exactly two shapes:

      {"remove": "<element name>"}  -> drop that element
      {"remove": null}              -> the model declined

    Anything else raises, which the caller turns into a refusal. A
    replacement value is rejected outright: this strategy is scoped to
    removal so that it cannot introduce clinical content.
    """
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("expected a JSON object")
    if "remove" not in obj:
        raise ValueError("expected a 'remove' key")

    target = obj["remove"]
    if target is None:
        return None
    if not isinstance(target, str) or not target.strip():
        raise ValueError(f"expected an element name or null, got {target!r}")
    return target.strip()


SYSTEM_PROMPT = """\
You are a FHIR R4 repair assistant handling invariant violations. An
invariant is a constraint across several elements of a resource, so the
validator reports it against the resource rather than one field.

Your only available repair is to remove one element. Pick the element whose
removal resolves the invariant while preserving what the resource is
actually asserting. Prefer dropping a redundant or contradictory field over
one carrying a clinical measurement.

Respond with a single JSON object and nothing else:

  {"remove": "<element name>"}  the top-level element to delete
  {"remove": null}              if no removal is safe, or you are unsure

Use the element name as it appears in the JSON, for example
"dataAbsentReason". Do not propose a replacement value, do not return a
path with dots, and do not add commentary or markdown.
"""


class InvariantStrategy:
    """Resolve an invariant violation by removing one conflicting element."""

    name = NAME
    version = VERSION
    permission = PERMISSION
    risk = RISK

    def __init__(
        self,
        provider: LLMProvider,
        prompt_version: str = "v1",
        prompt_path: Path | None = None,
    ) -> None:
        self._provider = provider
        self._prompt_version = prompt_version
        self._template = Template((prompt_path or _PROMPT_PATH).read_text(encoding="utf-8"))

    def apply(
        self,
        resource: dict[str, Any],
        error: ValidationError,
    ) -> RepairAction:
        # Only act on an actual invariant failure. This strategy is usually
        # last in a chain, so without this check it becomes a catch-all that
        # deletes clinical content to silence any error the earlier
        # strategies refused: an unparseable unit, a bad comparator, a
        # malformed code. Removing an Observation's value does make the
        # validator happy, and it is exactly the behaviour the hallucination
        # guard exists to prevent.
        if not _is_invariant_failure(error):
            return self._refuse(
                error,
                None,
                "error is not an invariant failure",
            )

        rendered = self._template.render(
            resource=json.dumps(resource, separators=(",", ":")),
            error=error,
            candidates=sorted(_candidate_elements(resource)),
        )
        segments = [
            PromptSegment(role="system", text=SYSTEM_PROMPT, stable=True),
            PromptSegment(role="user", text=rendered, stable=False),
        ]

        start = time.perf_counter()
        try:
            completion = self._provider.complete(segments)
        except Exception as exc:
            logger.warning("invariant LLM call failed: %s", exc)
            return self._refuse(error, None, f"LLM call failed: {exc}")
        latency_ms = int((time.perf_counter() - start) * 1000)

        try:
            target = parse_invariant_response(completion.text)
        except Exception as exc:
            return self._refuse(error, None, f"could not parse LLM response: {exc}")

        if target is None:
            return self._refuse(error, None, "LLM declined to name an element to remove")

        # Never delete on an unchecked name. The model could return a field
        # that is absent, or one that is structural rather than clinical.
        if target not in resource:
            return self._refuse(error, None, f"element {target!r} is not present on the resource")
        if target in _PROTECTED_ELEMENTS:
            return self._refuse(error, None, f"refusing to remove structural element {target!r}")

        location = f"{resource.get('resourceType', '')}.{target}".lstrip(".")
        before = get_at_path(resource, location)
        if not delete_at_path(resource, location):
            return self._refuse(error, before, f"could not remove {location}")

        return RepairAction(
            error=error,
            strategy=self.name,
            strategy_version=self.version,
            risk=self.risk,  # type: ignore[arg-type]
            permission_used=self.permission,
            before=before,
            after=None,
            removed=True,
            explanation=(
                f"LLM ({completion.model}) resolved the invariant by removing {location}."
            ),
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

    def _refuse(self, error: ValidationError, before: Any, reason: str) -> RepairAction:
        return refused(error, self.name, self.version, self.permission, before, reason)


# Elements that carry the resource's identity rather than clinical content.
# Removing one of these would never be the right way to satisfy an
# invariant, whatever the model says.
_PROTECTED_ELEMENTS = frozenset({"resourceType", "id", "meta", "implicitRules"})


def _is_invariant_failure(error: ValidationError) -> bool:
    """True if the validator reported a failed invariant.

    HAPI phrases these as "Constraint failed: <key>: '<expression>'". The
    code is the generic `processing`, so the message is the only signal.
    Matching on it is unavoidably validator-specific; a different validator
    adapter would need its own rule.
    """
    message = error.message.lower()
    return "constraint failed" in message or "invariant" in message


def _candidate_elements(resource: dict[str, Any]) -> set[str]:
    """Top-level element names the strategy is willing to remove.

    Passed to the prompt so the model chooses from what is actually there
    instead of guessing at the resource shape.
    """
    return {key for key in resource if key not in _PROTECTED_ELEMENTS}
