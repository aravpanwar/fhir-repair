"""Benchmark harness.

Runs `fhir-repair` against a mutated corpus and emits a leaderboard JSON
file. The harness uses the tool itself as the system-under-test, so the
benchmark and the shipping product cannot disagree about behaviour.

Usage:

    python -m benchmark.run \\
        --corpus benchmark/corpus/synthea_mutated \\
        --manifest benchmark/corpus/synthea_mutated/manifest.json \\
        --config examples/repair-config.yaml \\
        --out benchmark/results.json
"""

from __future__ import annotations

import argparse
import json
import time
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

            t0 = time.perf_counter()
            outcome = repairer.repair(mutated)
            duration_ms = int((time.perf_counter() - t0) * 1000)

            results.append(
                {
                    "mutation": manifest["mutation"],
                    "description": manifest["description"],
                    "location": manifest["location"],
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

    return {
        "total": total,
        "validator_pass_rate": passed / total,
        "ground_truth_match_rate": matched / total,
        "mean_duration_ms": sum(r["duration_ms"] for r in results) // total,
        "total_duration_ms": total_duration_ms,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the fhir-repair benchmark.")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--hapi-url", default=None)
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


if __name__ == "__main__":
    main()
