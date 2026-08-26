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
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from fhir_repair import Repairer
from fhir_repair.core.config import RepairConfig, load_config
from fhir_repair.validators.hapi import HapiRestValidator


def run_benchmark(
    corpus_dir: Path,
    manifest_path: Path,
    config: RepairConfig,
    output_path: Path,
    hapi_url: str | None = None,
) -> dict[str, Any]:
    """Run the tool against every entry in the manifest and compute metrics."""
    manifests = json.loads(manifest_path.read_text(encoding="utf-8"))

    validator = HapiRestValidator(base_url=hapi_url) if hapi_url else HapiRestValidator()
    repairer = Repairer(validator=validator, config=config)

    results: list[dict[str, Any]] = []
    start = time.perf_counter()

    try:
        for manifest in manifests:
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

            results.append(
                {
                    "mutation": manifest["mutation"],
                    "description": manifest["description"],
                    "location": manifest["location"],
                    "validator_detected": detected,
                    "passed_validator": len(outcome.unresolved) == 0,
                    "matches_ground_truth": outcome.fixed_resource == valid,
                    "actions_taken": len(outcome.audit),
                    "actions_refused": sum(1 for a in outcome.audit if a.risk == "refused"),
                    "duration_ms": duration_ms,
                }
            )
    finally:
        validator.close()

    total_duration_ms = int((time.perf_counter() - start) * 1000)
    summary = _aggregate(results, total_duration_ms)

    payload = {
        "summary": summary,
        "results": results,
        "metadata": repairer._metadata(),
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


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
        "--label",
        default=None,
        help="Name for this run in the leaderboard. Defaults to the model id.",
    )
    args = parser.parse_args()

    config = load_config(args.config) if args.config else RepairConfig()
    payload = run_benchmark(
        corpus_dir=args.corpus,
        manifest_path=args.manifest,
        config=config,
        output_path=args.out,
        hapi_url=args.hapi_url,
    )
    print(json.dumps(payload["summary"], indent=2))

    if args.leaderboard is not None:
        label = args.label or payload["metadata"].get("llm_model") or "run"
        append_to_leaderboard(payload, args.leaderboard, label)


if __name__ == "__main__":
    main()
