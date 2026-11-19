"""Benchmark harness.

Runs `fhir-repair` against a mutated corpus and emits a leaderboard JSON
file. The harness uses the tool itself as the system-under-test, so the
benchmark and the shipping product cannot disagree about behaviour.

Usage:

    python -m benchmark.run \\
        --corpus benchmark/corpus/synthea_mutated \\
        --manifest benchmark/corpus/synthea_mutated/manifest.json \\
        --config examples/repair-config.yaml \\
        --out benchmark/results.json \\
        --leaderboard benchmark/leaderboard.json \\
        --label "claude-opus / v1"

Pass `--leaderboard` to append the run to a cumulative leaderboard file that
`leaderboard.html` renders. Omit it to write only the single-run results.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fhir_repair import Repairer
from fhir_repair.core.config import RepairConfig, load_config
from fhir_repair.core.fhirpath import get_at_path
from fhir_repair.core.models import RepairAction
from fhir_repair.validators.hapi import HapiRestValidator

# Seed for choosing a pilot sample. Separate from the mutation seed, which
# fixes the published corpus and must not change.
SAMPLE_SEED = 27373


def run_benchmark(
    corpus_dir: Path,
    manifest_path: Path,
    config: RepairConfig,
    output_path: Path,
    hapi_url: str | None = None,
    sample: int | None = None,
    sample_seed: int = SAMPLE_SEED,
    label: str | None = None,
    workers: int = 1,
) -> dict[str, Any]:
    """Run the tool against every entry in the manifest and compute metrics.

    With `sample`, run a class-stratified subset of that many cases instead,
    for a pilot that measures token use before a full run. `workers` runs
    that many cases at once; per-case latency then includes contention for
    the validator and the LLM, so compare latency only at equal `workers`.
    """
    manifests = json.loads(manifest_path.read_text(encoding="utf-8"))
    if sample is not None:
        manifests = stratified_sample(manifests, sample, sample_seed)

    validator = HapiRestValidator(base_url=hapi_url) if hapi_url else HapiRestValidator()
    repairer = Repairer(validator=validator, config=config)

    def run_case(manifest: dict[str, Any]) -> dict[str, Any]:
        mutated = json.loads(Path(manifest["mutated_path"]).read_text(encoding="utf-8"))
        valid = json.loads(Path(manifest["valid_path"]).read_text(encoding="utf-8"))

        # Did the validator flag the corruption at all? A mutation the
        # validator accepts never reaches a strategy, so scoring it as a
        # repair would credit the tool for doing nothing. Recorded per
        # case rather than assumed per class, because whether a given
        # corruption is detected depends on the validator and profile.
        detected = bool(validator.validate(mutated, config.target_profile))

        t0 = time.perf_counter()
        outcome = repairer.repair(mutated)
        duration_ms = int((time.perf_counter() - t0) * 1000)

        fixed = outcome.fixed_resource
        return {
            "mutation": manifest["mutation"],
            "description": manifest["description"],
            "location": manifest["location"],
            "validator_detected": detected,
            "passed_validator": len(outcome.unresolved) == 0,
            "matches_ground_truth": fixed == valid,
            "actions_taken": len(outcome.audit),
            "actions_refused": sum(1 for a in outcome.audit if a.risk == "refused"),
            "duration_ms": duration_ms,
            **llm_usage(outcome.audit),
            "collateral_changes": collateral_changes(mutated, fixed, valid),
            **deleted_value_outcome(manifest["location"], mutated, fixed, valid),
        }

    start = time.perf_counter()
    try:
        # The Repairer keeps no per-case state and its HTTP clients are
        # thread-safe, so cases can share it. Results keep manifest order.
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            results = list(pool.map(run_case, manifests))
    finally:
        validator.close()

    total_duration_ms = int((time.perf_counter() - start) * 1000)
    summary = _aggregate(results, total_duration_ms)

    payload = {
        "summary": summary,
        "results": results,
        "metadata": {
            **repairer._metadata(),
            # Enough to tell arms apart and rerun one: the dispatch table
            # decides which arm this is, and the options shape every call.
            "label": label,
            "strategies": config.strategies,
            "llm_options": config.llm.options,
            "sample": sample,
            "sample_seed": sample_seed if sample is not None else None,
            "workers": workers,
        },
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def stratified_sample(
    manifests: list[dict[str, Any]], size: int, seed: int = SAMPLE_SEED
) -> list[dict[str, Any]]:
    """Pick `size` cases spread as evenly as possible over mutation classes.

    Classes are visited in turn, each contributing its next case from a
    seeded shuffle, so a small pilot still covers every class rather than
    mostly the two 100-case classes.
    """
    rng = random.Random(seed)
    by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for manifest in manifests:
        by_class[manifest["mutation"]].append(manifest)
    queues = []
    for name in sorted(by_class):
        cases = list(by_class[name])
        rng.shuffle(cases)
        queues.append(cases)

    picked: list[dict[str, Any]] = []
    while len(picked) < size and any(queues):
        for queue in queues:
            if queue and len(picked) < size:
                picked.append(queue.pop(0))
    return picked


def llm_usage(audit: list[RepairAction]) -> dict[str, Any]:
    """LLM calls and tokens for one case, from the audit's provenance blocks.

    Each LLM strategy invocation that reached the provider leaves one block,
    applied or refused. Retries inside an invocation are not visible here.
    """
    blocks = [action.llm for action in audit if action.llm]
    return {
        "llm_calls": len(blocks),
        "input_tokens": sum(int(b.get("input_tokens", 0)) for b in blocks),
        "output_tokens": sum(int(b.get("output_tokens", 0)) for b in blocks),
        "cached_tokens": sum(int(b.get("cached_tokens", 0)) for b in blocks),
        "llm_models": sorted({str(b["model"]) for b in blocks if b.get("model")}),
    }


_ABSENT = object()


def collateral_changes(
    mutated: dict[str, Any], fixed: dict[str, Any], valid: dict[str, Any]
) -> list[str]:
    """Paths the repair changed although the mutation had left them correct.

    Ground truth alone cannot separate "missed the fix" from "broke
    something else". A leaf counts here when the repaired value differs from
    the mutated one while the mutated one still equalled the original, so
    the mutation's own path never counts and a correct fix never counts.
    """
    before, after, truth = _leaves(mutated), _leaves(fixed), _leaves(valid)
    return sorted(
        path
        for path in before.keys() | after.keys()
        if after.get(path, _ABSENT) != before.get(path, _ABSENT)
        and before.get(path, _ABSENT) == truth.get(path, _ABSENT)
    )


def deleted_value_outcome(
    location: str, mutated: dict[str, Any], fixed: dict[str, Any], valid: dict[str, Any]
) -> dict[str, bool | None]:
    """Whether a value was written where the mutation deleted one.

    Nothing in the input says what a deleted field held, so any value
    written there is invented, even one that happens to match the original
    (a guess from context). Both fields are None when the case did not
    delete a value.
    """
    if get_at_path(mutated, location) is not None or get_at_path(valid, location) is None:
        return {"deleted_value_written": None, "deleted_value_matches": None}
    written = get_at_path(fixed, location)
    return {
        "deleted_value_written": written is not None,
        "deleted_value_matches": written is not None and written == get_at_path(valid, location),
    }


def _leaves(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten JSON to {dotted path: scalar}, keeping empty containers as leaves."""
    if isinstance(value, dict) and value:
        out: dict[str, Any] = {}
        for key, item in value.items():
            out.update(_leaves(item, f"{prefix}.{key}" if prefix else str(key)))
        return out
    if isinstance(value, list) and value:
        out = {}
        for index, item in enumerate(value):
            out.update(_leaves(item, f"{prefix}[{index}]"))
        return out
    return {prefix: value}


def _aggregate(results: list[dict[str, Any]], total_duration_ms: int) -> dict[str, Any]:
    """Roll up per-case results into a leaderboard-friendly summary."""
    total = len(results)
    if total == 0:
        return {"total": 0}

    passed = sum(1 for r in results if r["passed_validator"])
    matched = sum(1 for r in results if r["matches_ground_truth"])

    # Cases the validator never flagged are counted separately. They inflate
    # `validator_pass_rate` (the resource was already accepted, so nothing
    # had to be repaired) without telling us anything about repair quality.
    detected = [r for r in results if r.get("validator_detected", True)]
    undetected = total - len(detected)

    summary = {
        "total": total,
        "validator_pass_rate": passed / total,
        "ground_truth_match_rate": matched / total,
        "mean_duration_ms": sum(r["duration_ms"] for r in results) // total,
        "total_duration_ms": total_duration_ms,
        "by_mutation": _by_mutation(results),
        "undetected_by_validator": undetected,
        # Totals rather than rates: cost and harm are what a reader weighs
        # against the pass rates. Older result files lack these fields.
        "llm_calls": sum(r.get("llm_calls", 0) for r in results),
        "input_tokens": sum(r.get("input_tokens", 0) for r in results),
        "output_tokens": sum(r.get("output_tokens", 0) for r in results),
        "cached_tokens": sum(r.get("cached_tokens", 0) for r in results),
        "cases_with_collateral_changes": sum(1 for r in results if r.get("collateral_changes")),
        "deleted_values_written": sum(1 for r in results if r.get("deleted_value_written")),
    }

    if detected and undetected:
        # Headline rates over only the cases that posed a repair problem.
        summary["detected_total"] = len(detected)
        summary["detected_validator_pass_rate"] = sum(
            1 for r in detected if r["passed_validator"]
        ) / len(detected)
        summary["detected_ground_truth_match_rate"] = sum(
            1 for r in detected if r["matches_ground_truth"]
        ) / len(detected)

    return summary


def _by_mutation(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Pass and match rates grouped by mutation class.

    The leaderboard uses this to show which error classes a model handles
    well and which it does not, instead of a single blended number.
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        groups[result["mutation"]].append(result)

    out: dict[str, dict[str, Any]] = {}
    for name, group in sorted(groups.items()):
        count = len(group)
        entry: dict[str, Any] = {
            "total": count,
            "validator_pass_rate": sum(1 for r in group if r["passed_validator"]) / count,
            "ground_truth_match_rate": sum(1 for r in group if r["matches_ground_truth"]) / count,
        }
        # Flag a class the validator does not flag at all. Without this the
        # row reads as a perfect score when in fact nothing was dispatched.
        undetected = sum(1 for r in group if not r.get("validator_detected", True))
        if undetected:
            entry["undetected_by_validator"] = undetected
        out[name] = entry
    return out


def append_to_leaderboard(
    payload: dict[str, Any],
    leaderboard_path: Path,
    label: str,
) -> list[dict[str, Any]]:
    """Append a run summary to a cumulative leaderboard file.

    The leaderboard is a JSON array, one entry per run. Each entry carries
    the headline rates plus enough provenance (model, prompt, dispatch
    version) to make the comparison reproducible. New runs append rather
    than overwrite, so a model-vs-prompt sweep builds up over several
    invocations.
    """
    entries: list[dict[str, Any]] = []
    if leaderboard_path.exists():
        entries = json.loads(leaderboard_path.read_text(encoding="utf-8"))

    summary = payload.get("summary", {})
    metadata = payload.get("metadata", {})
    entries.append(
        {
            "label": label,
            "model": metadata.get("llm_model"),
            "prompt_version": metadata.get("prompt_version"),
            "dispatch_version": metadata.get("dispatch_version"),
            "total": summary.get("total"),
            "validator_pass_rate": summary.get("validator_pass_rate"),
            "ground_truth_match_rate": summary.get("ground_truth_match_rate"),
            "mean_duration_ms": summary.get("mean_duration_ms"),
            "by_mutation": summary.get("by_mutation", {}),
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )

    leaderboard_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the fhir-repair benchmark.")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--hapi-url", default=None)
    parser.add_argument(
        "--leaderboard",
        type=Path,
        default=None,
        help="Append this run's summary to a cumulative leaderboard JSON file.",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Run a class-stratified sample of this many cases (a pilot).",
    )
    parser.add_argument("--sample-seed", type=int, default=SAMPLE_SEED)
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Cases to run at once. Latency is comparable only at equal workers.",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="Name for this run; repeats of one arm share a label. Defaults to the model id.",
    )
    args = parser.parse_args()

    config = load_config(args.config) if args.config else RepairConfig()
    payload = run_benchmark(
        corpus_dir=args.corpus,
        manifest_path=args.manifest,
        config=config,
        output_path=args.out,
        hapi_url=args.hapi_url,
        sample=args.sample,
        sample_seed=args.sample_seed,
        label=args.label,
        workers=args.workers,
    )
    print(json.dumps(payload["summary"], indent=2))

    if args.leaderboard is not None:
        label = args.label or payload["metadata"].get("llm_model") or "run"
        append_to_leaderboard(payload, args.leaderboard, label)


if __name__ == "__main__":
    main()
