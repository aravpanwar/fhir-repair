"""The orchestrator.

The `Repairer` class wires together the validator, the strategy registry,
the dispatcher, the hallucination guard, and the audit log. It exposes a
single `repair(resource)` method that returns a `RepairResult`.

This module contains no strategy logic. Strategies live under
`fhir_repair.strategies`; the orchestrator only resolves and invokes them.
"""

from __future__ import annotations

import copy
import re
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fhir_repair.core.audit import AuditWriter
from fhir_repair.core.config import RepairConfig
from fhir_repair.core.dispatcher import (
    DispatchPlan,
    StrategyResolver,
    build_plan,
    detect_regressions,
    is_stuck,
    snapshot,
)
from fhir_repair.core.fhirpath import delete_at_path, set_at_path
from fhir_repair.core.guard import HallucinationGuard
from fhir_repair.core.models import RepairAction, RepairResult, ValidationError
from fhir_repair.strategies.base import refused

if TYPE_CHECKING:
    from fhir_repair.llm.base import LLMProvider
    from fhir_repair.strategies.registry import StrategyRegistry
    from fhir_repair.validators.base import Validator


@dataclass
class _LLMBudget:
    """LLM strategy invocations left for the resource being repaired.

    Made fresh for each `repair()` call and passed down, rather than kept on
    the Repairer, because the HTTP service shares one Repairer across
    threads. An invocation counts once however many times the strategy
    retries a failed call inside it.
    """

    calls_left: int


class Repairer:
    """Orchestrates the repair loop for a single resource.

    The same Repairer instance can be reused across many resources. It
    holds no per-resource state.
    """

    def __init__(
        self,
        validator: Validator,
        registry: StrategyRegistry | None = None,
        config: RepairConfig | None = None,
        guard: HallucinationGuard | None = None,
        llm_provider: LLMProvider | None = None,
    ) -> None:
        # Imported lazily to keep module-level import graph small and to
        # avoid a circular import between core and strategies.
        from fhir_repair.strategies.registry import default_registry

        self._validator = validator
        self._registry = registry or default_registry()
        self._config = config or RepairConfig()
        self._guard = guard or self._config.hallucination_guard.to_guard()
        self._resolver = StrategyResolver(self._config.strategies)

        # If the dispatch table references LLM strategies, ensure they are
        # registered. The user can either pass a pre-built provider or let
        # us build one from config; either way we fail loudly at construction
        # time if the references can't be satisfied, rather than discovering
        # the mismatch mid-repair.
        if any(_is_llm(sid) for _, sid in self._dispatch_entries()):
            self._register_llm_strategies(llm_provider)

        # A misspelled id would otherwise surface as a KeyError partway
        # through a repair, after the audit file is open and other fixes
        # have been applied.
        known = set(self._registry.names())
        for code, sid in self._dispatch_entries():
            if sid != "refuse" and sid not in known:
                raise ValueError(f"unknown strategy {sid!r} in the dispatch entry for {code!r}")

    def _dispatch_entries(self) -> Iterator[tuple[str, str]]:
        """Every (error code, strategy id) pair in the dispatch table."""
        for code, value in self._config.strategies.items():
            for sid in [value] if isinstance(value, str) else value:
                yield code, sid

    def _register_llm_strategies(
        self,
        provider: LLMProvider | None,
    ) -> None:
        """Build (or accept) the LLM provider and register the LLM strategies.

        Skips registration of strategies that are already in the registry,
        so a caller who pre-registered custom LLM strategies on the registry
        keeps full control.
        """
        from fhir_repair.llm import build_llm_provider
        from fhir_repair.strategies.llm import register_default_llm_strategies

        resolved_provider = provider or build_llm_provider(self._config.llm)

        # `register_default_llm_strategies` adds the built-in LLM strategies.
        # If the user supplied a custom registry that already has them (or
        # replacements), we honour that and skip; otherwise we register
        # defaults.
        existing = set(self._registry.names())
        defaults = {"llm", "llm.suggest_terminology_match", "llm.resolve_invariant"}
        if defaults.issubset(existing):
            return
        register_default_llm_strategies(
            self._registry,
            resolved_provider,
            prompt_version=self._config.llm.prompt_version,
            max_retries=self._config.llm.max_retries,
            backoff_base_s=self._config.llm.backoff_base_s,
            backoff_max_s=self._config.llm.backoff_max_s,
            request_options=self._config.llm.options,
        )

    def repair(self, resource: dict[str, Any]) -> RepairResult:
        """Repair a resource, returning the fix and the audit log."""
        start = time.perf_counter()
        working = snapshot(resource)
        audit: list[RepairAction] = []

        # Validate once up front. If the resource is already valid, return
        # without touching the audit file.
        errors = self._validate(working)
        if not errors:
            return RepairResult(
                fixed_resource=working,
                audit=[],
                unresolved=[],
                duration_ms=_elapsed_ms(start),
                metadata=self._metadata(),
            )

        initial = errors
        budget = _LLMBudget(self._config.limits.max_llm_calls_per_resource)
        with self._open_audit(working) as audit_writer:
            for _ in range(self._config.limits.max_attempts):
                plan = build_plan(errors, self._resolver)
                if not plan.batches:
                    break

                actions, after = self._run_plan(working, plan, errors, audit_writer, budget)
                audit.extend(actions)

                # An attempt that leaves the error set as it found it would
                # make the same moves again, so stop rather than spend another
                # round of strategy and validator calls on it.
                stuck = is_stuck(errors, after)
                errors = after
                if not errors or stuck:
                    break

            # `errors` always describes the resource as returned, so it is
            # the unresolved list: an error that a later fix cleared up is
            # not reported, and one a fix uncovered is.
            unresolved = _dedupe_errors(errors)
            duration_ms = _elapsed_ms(start)
            audit_writer.write_summary(
                total_errors=len(initial),
                fixed=_count_fixed(initial, unresolved),
                unresolved=len(unresolved),
                duration_ms=duration_ms,
            )

        return RepairResult(
            fixed_resource=working,
            audit=audit,
            unresolved=unresolved,
            duration_ms=duration_ms,
            metadata=self._metadata(),
        )

    def _run_plan(
        self,
        resource: dict[str, Any],
        plan: DispatchPlan,
        errors: list[ValidationError],
        audit_writer: AuditWriter,
        budget: _LLMBudget,
    ) -> tuple[list[RepairAction], list[ValidationError]]:
        """Execute a plan against `resource`, mutating it in place.

        `errors` must describe `resource` as it is on entry. Each batch runs
        in declared order and is re-validated once; a regression rolls the
        batch back and replays it one action at a time. Returns the actions
        taken and the errors on the resource afterwards.
        """
        all_actions: list[RepairAction] = []

        for batch in plan.batches:
            # One corruption often raises two errors at different depths (a
            # comma decimal is both "not a valid decimal" and "must be a
            # number"), so fixing the deeper one clears the shallower one
            # queued in a later batch. Dispatching it anyway runs its chain on
            # a value that is already right: every deterministic strategy
            # refuses and an LLM strategy is called to "fix" it.
            current = set(errors)
            batch = [(chain, error) for chain, error in batch if error in current]
            if not batch:
                continue

            before_state = snapshot(resource)
            actions = [self._invoke_chain(chain, resource, error, budget) for chain, error in batch]
            touched = {action.written_path for action in actions if action.risk != "refused"}

            # Strategies only change the resource when they do not refuse,
            # so a batch that wrote nothing leaves `errors` accurate and
            # needs no validator call.
            if touched:
                after = self._validate(resource)
                if detect_regressions(errors, after, touched):
                    _restore(resource, before_state)
                    actions, after = self._replay_one_at_a_time(resource, actions, errors)
                errors = after

            for action in actions:
                audit_writer.write_action(action)
            all_actions.extend(actions)

        return all_actions, errors

    def _replay_one_at_a_time(
        self,
        resource: dict[str, Any],
        actions: list[RepairAction],
        errors: list[ValidationError],
    ) -> tuple[list[RepairAction], list[ValidationError]]:
        """Re-apply a rolled-back batch one action at a time.

        `errors` must describe `resource` as it is on entry; the errors
        after the replay are returned with the actions. Each action is
        replayed from its recorded result rather than by running the
        strategy again: an LLM strategy would spend a second call and could
        answer differently from what the audit log records. An action that
        still causes a regression on its own, or no longer applies, is undone
        and recorded as refused, and the replay carries on from the state
        before it.
        """
        replayed: list[RepairAction] = []
        for action in actions:
            if action.risk == "refused":
                replayed.append(action)
                continue

            checkpoint = snapshot(resource)
            try:
                _replay(resource, action)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                _restore(resource, checkpoint)
                replayed.append(_refuse_after_rollback(action, f"could not be replayed: {exc}"))
                continue

            after = self._validate(resource)
            if detect_regressions(errors, after, {action.written_path}):
                _restore(resource, checkpoint)
                replayed.append(
                    _refuse_after_rollback(action, f"caused a regression at {action.written_path}")
                )
            else:
                errors = after
                replayed.append(action)
        return replayed, errors

    def _validate(self, resource: dict[str, Any]) -> list[ValidationError]:
        return self._validator.validate(resource, self._config.target_profile)

    def _invoke_chain(
        self,
        chain: list[str],
        resource: dict[str, Any],
        error: ValidationError,
        budget: _LLMBudget,
    ) -> RepairAction:
        """Try each strategy in `chain` until one does not refuse.

        A chain lets a single FHIR issue code map to several specific
        strategies. Each strategy refuses cleanly when its preconditions
        do not match (e.g., normalize_date refuses on non-date values),
        and the dispatcher falls through to the next. Returns the first
        non-refused action, or the last refusal if every link refused.
        """
        last_action: RepairAction | None = None
        for strategy_id in chain:
            action = self._invoke_strategy(strategy_id, resource, error, budget)
            if action.risk != "refused":
                return action
            last_action = action

        # Every link refused. Return the final refusal so the audit log
        # at least records the last attempt.
        assert last_action is not None  # chain was non-empty by construction
        return last_action

    def _invoke_strategy(
        self,
        strategy_id: str,
        resource: dict[str, Any],
        error: ValidationError,
        budget: _LLMBudget,
    ) -> RepairAction:
        """Look up and run a single strategy, enforcing the guard and the LLM budget."""
        strategy = self._registry.get(strategy_id)

        def refuse(reason: str) -> RepairAction:
            return refused(
                error, strategy.name, strategy.version, strategy.permission, None, reason
            )

        if not self._guard.is_allowed(strategy.permission):
            return refuse(f"hallucination guard denies permission {strategy.permission!r}")

        if _is_llm(strategy_id):
            if budget.calls_left <= 0:
                return refuse("the LLM call limit for this resource is used up")
            budget.calls_left -= 1

        return strategy.apply(resource, error)

    def _open_audit(self, resource: dict[str, Any]) -> AuditWriter:
        """Build the AuditWriter for a single resource."""
        resource_type = resource.get("resourceType", "Unknown")
        resource_id = resource.get("id") or uuid.uuid4().hex[:8]
        full_id = f"{resource_type}/{resource_id}"

        # File name encodes resource type and id. The timestamp is only
        # second-resolution, so a short random suffix is what actually
        # prevents a collision when the same id is repaired twice within one
        # second.
        ts = time.strftime("%Y%m%dT%H%M%S")
        suffix = uuid.uuid4().hex[:6]
        name = f"{_file_safe(resource_type)}-{_file_safe(resource_id)}-{ts}-{suffix}"
        path = Path(self._config.logging.audit_destination) / f"{name}.audit.jsonl"

        return AuditWriter(
            destination=path,
            resource_id=full_id,
            resource_type=resource_type,
            fhir_version=self._config.fhir_version,
            dispatch_version=self._config.dispatch_version,
        )

    def _metadata(self) -> dict[str, Any]:
        """Reproducibility provenance attached to every RepairResult."""
        return {
            "fhir_version": self._config.fhir_version,
            "hapi_version": self._config.hapi_version,
            "dispatch_version": self._config.dispatch_version,
            "llm_provider": self._config.llm.provider,
            "llm_model": self._config.llm.model,
            "prompt_version": self._config.llm.prompt_version,
            "guard": self._guard.to_dict(),
        }


def _restore(resource: dict[str, Any], state: dict[str, Any]) -> None:
    """Put `resource` back to `state` in place.

    Copies rather than adopting `state`'s nested objects: a later in-place
    write would otherwise change the saved state too, and a second restore
    to it would bring the write back.
    """
    resource.clear()
    resource.update(snapshot(state))


def _replay(resource: dict[str, Any], action: RepairAction) -> None:
    """Re-apply a recorded action at the path it originally wrote.

    A removal is replayed as a deletion: writing its recorded `after`
    would put a literal null where the element was.
    """
    if action.removed:
        if not delete_at_path(resource, action.written_path):
            raise ValueError(f"nothing left to remove at {action.written_path}")
        return
    # Copied so the resource and the audit record do not share an object
    # that a later fix could change in place.
    set_at_path(resource, action.written_path, copy.deepcopy(action.after))


def _refuse_after_rollback(action: RepairAction, reason: str) -> RepairAction:
    return replace(
        action,
        risk="refused",
        after=action.before,
        removed=False,
        explanation=f"Refused after rollback: {reason}.",
    )


# A valid FHIR id only uses these characters, but the id comes from the
# input (over the network, for the HTTP service), so it cannot be trusted
# not to carry a path separator or a name Windows rejects.
_UNSAFE_IN_FILENAME = re.compile(r"[^A-Za-z0-9.-]")


def _file_safe(value: str) -> str:
    return _UNSAFE_IN_FILENAME.sub("_", value)[:64]


def _is_llm(strategy_id: str) -> bool:
    return strategy_id == "llm" or strategy_id.startswith("llm.")


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _count_fixed(initial: list[ValidationError], remaining: list[ValidationError]) -> int:
    """How many of the input's errors are gone from the output.

    Counted per error rather than as `initial - remaining`, which goes
    negative when a fix uncovers more errors than it cleared.
    """
    left = {(e.code, e.location) for e in remaining}
    return len({(e.code, e.location) for e in initial} - left)


def _dedupe_errors(errors: list[ValidationError]) -> list[ValidationError]:
    """Remove duplicate errors while preserving order."""
    seen: set[tuple[str, str]] = set()
    out: list[ValidationError] = []
    for error in errors:
        key = (error.code, error.location)
        if key not in seen:
            seen.add(key)
            out.append(error)
    return out
