"""LLM strategy for FHIR invariant violations.

An invariant is a constraint spanning more than one element, expressed in the
spec as a FHIRPath expression: Observation's obs-7 forbids `dataAbsentReason`
when a value is present, Patient's pat-1 constrains `contact`, and so on.
Unlike a malformed date, there is no single correct rewrite. The validator
says "these elements cannot coexist" and the repair is a judgement about
which one to drop.

That judgement is why this is an LLM strategy rather than a deterministic
one. The fix, however, is deliberately narrow: this strategy may only
*remove* an element, never write a new value. Removal cannot invent clinical
content, which keeps the highest-risk failure mode off the table entirely.
A model that answers with a replacement value is refused.
"""

from __future__ import annotations

import json
from typing import Any

from fhir_repair.strategies.llm.runner import DELETE

NAME = "llm.resolve_invariant"
VERSION = "1.0.0"

# Removing an element the submitter provided is a change to existing
# content, so it needs the clinical-value permission rather than the
# reformat one. It is off by default in the guard, which is the intended
# posture: dropping data should be an explicit opt-in.
PERMISSION = "allow_change_existing_clinical_value"

RISK = "high"


def parse_invariant_response(text: str) -> Any:
    """Parse the invariant strategy's response contract.

    Accepts exactly two shapes:

      {"action": "remove"}  -> drop the element at the error path
      {"action": "none"}    -> the model declined; becomes a refusal

    Anything else raises, which the runner turns into a refusal. In
    particular a replacement value is rejected: this strategy is scoped to
    removal so that it cannot introduce clinical content.
    """
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("expected a JSON object")

    action = obj.get("action")
    if action == "remove":
        return DELETE
    if action == "none":
        # None is the runner's "model declined" signal.
        return None

    raise ValueError(f"expected action 'remove' or 'none', got {action!r}")


SYSTEM_PROMPT = """\
You are a FHIR R4 repair assistant handling invariant violations. An
invariant is a constraint across several elements of a resource. You are
given the failing invariant and the element the validator flagged.

Your only available repair is to remove the flagged element. Decide whether
removing it resolves the invariant without destroying information the
resource is asserting.

Respond with a single JSON object and nothing else:

  {"action": "remove"}  if removing the flagged element is the correct fix
  {"action": "none"}    if it is not, or if you are not confident

Do not propose a replacement value. Do not add commentary or markdown.
Prefer {"action": "none"} whenever removal would discard a clinical
assertion rather than a redundant or contradictory field.
"""
