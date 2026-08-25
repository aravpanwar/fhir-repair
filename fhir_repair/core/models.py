"""Core data structures for fhir-repair.

These types form the boundary between the orchestrator, the strategies, the
validator adapter, and the audit log. Keeping them simple dataclasses (and
not Pydantic models) keeps the hot path allocation-light. Pydantic appears
only at trust boundaries (config parsing, audit log validation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["error", "warning", "information"]
Risk = Literal["low", "medium", "high", "refused"]
Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class ValidationError:
    """A single validator-reported issue against a FHIR resource.

    `location` is a FHIRPath expression as emitted by HAPI, e.g.
    `Patient.contact[0].telecom[0].value`. It is used both for sorting
    (depth-first) and for assignment back to the resource.
    """

    code: str
    severity: Severity
    location: str
    message: str

    @property
    def depth(self) -> int:
        """Depth used for leaf-first ordering. Counts dot-separated segments."""
        # The leading resourceType segment counts; we want deeper paths to
        # sort first so segments-minus-one is not necessary.
        return self.location.count(".") if self.location else 0


@dataclass
class RepairAction:
    """A single applied (or refused) repair, written to the audit log.

    Pure-function strategies create one of these per error they handle.
    LLM strategies create one per LLM invocation that addressed an error.
    """

    error: ValidationError
    strategy: str
    strategy_version: str
    risk: Risk
    permission_used: str
    before: Any
    after: Any
    explanation: str

    # Set by the LLM runner when this action came from an LLM call. Stays
    # None for deterministic strategies.
    llm: dict[str, Any] | None = None

    # True when the repair removed the element rather than replacing it.
    # `after` is None in that case, which is indistinguishable from a fix
    # that legitimately wrote null, so rollback-retry needs this flag to
    # replay the action as a deletion instead of writing a literal null.
    # Not part of the sealed v1 audit schema; the writer ignores it.
    removed: bool = False


@dataclass
class RepairResult:
    """Full result of a Repairer.repair() call.

    `metadata` carries reproducibility provenance: model id, prompt version,
    dispatch table version, FHIR version, validator version. Anyone re-running
    a benchmark with the same metadata should see the same numbers.
    """

    fixed_resource: dict[str, Any]
    audit: list[RepairAction]
    unresolved: list[ValidationError]
    duration_ms: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PromptSegment:
    """A piece of a prompt sent to an LLM provider.

    The `stable` flag is a hint to the provider adapter: when True, this
    segment repeats across many calls (system prompt, retrieved spec
    excerpt). Provider adapters that have a native caching primitive
    (Anthropic `cache_control`, Bedrock equivalents) will apply it. Adapters
    where caching is automatic (OpenAI prefix caching, vLLM) ignore the hint.

    Marking a segment stable when it is in fact unique is harmless except for
    a small first-write cache cost on providers that bill for cache writes.
    Marking a segment unstable when it is in fact stable misses the
    optimisation but produces correct output.
    """

    role: Role
    text: str
    stable: bool = False
