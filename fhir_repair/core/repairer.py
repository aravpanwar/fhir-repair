"""The orchestrator.

The `Repairer` class wires together the validator, the strategy registry,
the dispatcher, the hallucination guard, and the audit log. It exposes a
single `repair(resource)` method that returns a `RepairResult`.

This module contains no strategy logic. Strategies live under
`fhir_repair.strategies`; the orchestrator only resolves and invokes them.
"""

from __future__ import annotations

import time
import uuid
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
from fhir_repair.core.guard import HallucinationGuard
from fhir_repair.core.models import RepairAction, RepairResult, ValidationError

if TYPE_CHECKING:
    from fhir_repair.llm.base import LLMProvider
    from fhir_repair.strategies.registry import StrategyRegistry
    from fhir_repair.validators.base import Validator


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
        if self._dispatch_uses_llm():
            self._register_llm_strategies(llm_provider)

    def _dispatch_uses_llm(self) -> bool:
        """True if any dispatch table value names an LLM strategy.

        Handles both single-string entries and chain (list-of-string)
        entries.
        """
        for value in self._config.strategies.values():
            ids = [value] if isinstance(value, str) else list(value)
            if any(sid == "llm" or sid.startswith("llm.") for sid in ids):
                return True
        return False

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

        # `register_default_llm_strategies` adds two strategies. If the user
        # supplied a custom registry that already has them (or replacements),
        # we honour that and skip; otherwise we register defaults.
        existing = set(self._registry.names())
        defaults = {"llm", "llm.suggest_terminology_match"}
        if defaults.issubset(existing):
            return
        register_default_llm_strategies(
            self._registry,
            resolved_provider,
            prompt_version=self._config.llm.prompt_version,
            max_retries=self._config.llm.max_retries,
            backoff_base_s=self._config.llm.backoff_base_s,
            backoff_max_s=self._config.llm.backoff_max_s,
        )

    def repair(self, resource: dict[str, Any]) -> RepairResult:
        """Repair a resource, returning the fix and the audit log."""
        start = time.perf_counter()
        working = snapshot(resource)
        audit: list[RepairAction] = []
        unresolved: list[ValidationError] = []

        # Validate once up front. If the resource is already valid, return
        # without touching the audit file.
        errors = self._validator.validate(working, self._config.target_profile)
        if not errors:
            duration_ms = int((time.perf_counter() - start) * 1000)
            return RepairResult(
                fixed_resource=working,
                audit=[],
                unresolved=[],
                duration_ms=duration_ms,
                metadata=self._metadata(),
            )

        total_errors = len(errors)
        attempts = self._config.limits.max_attempts
        prev_errors: list[ValidationError] = []

        with self._open_audit(working) as audit_writer:
            for attempt in range(attempts):
                plan = build_plan(errors, self._resolver)

                if not plan.batches and not plan.unmapped:
                    # Nothing left to do.
                    break

                actions = self._run_plan(working, plan, audit_writer)
                audit.extend(actions)

                # Anything the resolver could not map is unresolved unless
                # a later attempt picks it up (it will not, but the loop
                # structure makes that explicit).
                unresolved.extend(plan.unmapped)

                # Re-validate. If clean, we are done.
                errors = self._validator.validate(working, self._config.target_profile)
                if not errors:
                    break

                # Stuck detector: if the error set has not changed, give up.
                if attempt > 0 and is_stuck(prev_errors, errors):
                    break
                prev_errors = errors

            else:
                # The for-loop exhausted attempts without breaking. Anything
                # still in `errors` becomes unresolved.
                unresolved.extend(errors)

            # Anything still failing validation at exit becomes unresolved.
            # Extend unconditionally: when the loop exits via the stuck
            # detector with unmapped errors already collected, the guard
            # `not unresolved` would otherwise drop the mapped-but-unfixed
            # errors. `_dedupe_errors` removes any overlap.
            if errors:
                unresolved.extend(errors)

            unresolved = _dedupe_errors(unresolved)
            duration_ms = int((time.perf_counter() - start) * 1000)

            audit_writer.write_summary(
                total_errors=total_errors,
                fixed=total_errors - len(unresolved),
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
        audit_writer: AuditWriter,
    ) -> list[RepairAction]:
        """Execute a plan against `resource`, mutating it in place.

        Each batch runs in declared order. After each batch, we re-validate
        and check for regressions. Regressions trigger a rollback of the
        batch and a sequential retry.
        """
        all_actions: list[RepairAction] = []

        for batch in plan.batches:
            before_state = snapshot(resource)
            before_errors = self._validator.validate(resource, self._config.target_profile)

            batch_actions: list[RepairAction] = []
            touched: set[str] = set()

            for chain, error in batch:
                action = self._invoke_chain(chain, resource, error)
                batch_actions.append(action)
                touched.add(error.location)

            after_errors = self._validator.validate(resource, self._config.target_profile)
            regressions = detect_regressions(before_errors, after_errors, touched)

            if regressions:
                # Roll back, retry sequentially. Drop any action that still
                # regresses on its own.
                resource.clear()
                resource.update(before_state)

                for action in batch_actions:
                    pre_seq = self._validator.validate(resource, self._config.target_profile)
                    self._reapply_action(resource, action)
                    post_seq = self._validator.validate(resource, self._config.target_profile)
                    seq_regress = detect_regressions(pre_seq, post_seq, {action.error.location})
                    if seq_regress:
                        # Undo this action and record it as refused.
                        resource.clear()
                        resource.update(before_state)
                        refused = RepairAction(
                            error=action.error,
                            strategy=action.strategy,
                            strategy_version=action.strategy_version,
                            risk="refused",
                            permission_used=action.permission_used,
                            before=action.before,
                            after=action.before,
                            explanation=(
                                f"Refused after rollback: caused regression at "
                                f"{action.error.location}."
                            ),
                            llm=action.llm,
                        )
                        audit_writer.write_action(refused)
                        all_actions.append(refused)
                    else:
                        before_state = snapshot(resource)
                        audit_writer.write_action(action)
                        all_actions.append(action)
            else:
                for action in batch_actions:
                    audit_writer.write_action(action)
                all_actions.extend(batch_actions)

        return all_actions

    def _invoke_chain(
        self,
        chain: list[str],
        resource: dict[str, Any],
        error: ValidationError,
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
            action = self._invoke_strategy(strategy_id, resource, error)
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
    ) -> RepairAction:
        """Look up and run a single strategy, enforcing the hallucination guard."""
        strategy = self._registry.get(strategy_id)

        if not self._guard.is_allowed(strategy.permission):
            return RepairAction(
                error=error,
                strategy=strategy.name,
                strategy_version=strategy.version,
                risk="refused",
                permission_used=strategy.permission,
                before=None,
                after=None,
                explanation=(
                    f"Refused: hallucination guard denies permission {strategy.permission!r}."
                ),
            )

        return strategy.apply(resource, error)

    def _reapply_action(self, resource: dict[str, Any], action: RepairAction) -> None:
        """Re-apply an already-recorded action to a fresh resource snapshot.

        Used during sequential retry after a regression rollback. We invoke
        the strategy again rather than directly writing `action.after`,
        because some strategies depend on the surrounding context (for
        example, an LLM strategy with cache state).
        """
        if action.risk == "refused":
            return
        strategy = self._registry.get(action.strategy)
        strategy.apply(resource, action.error)

    def _open_audit(self, resource: dict[str, Any]) -> AuditWriter:
        """Build the AuditWriter for a single resource."""
        resource_type = resource.get("resourceType", "Unknown")
        resource_id = resource.get("id") or uuid.uuid4().hex[:8]
        full_id = f"{resource_type}/{resource_id}"

        # File name encodes resource type and id. Timestamp suffix prevents
        # collision when the same id is repaired twice in one second.
        ts = time.strftime("%Y%m%dT%H%M%S")
        path = Path(self._config.logging.audit_destination) / (
            f"{resource_type}-{resource_id}-{ts}.audit.jsonl"
        )

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
