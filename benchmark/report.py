"""Summarise benchmark run files as Markdown tables.

Usage:

    python -m benchmark.report RUN.json [RUN.json ...] [--prices PRICES.json] [--by-class]

Runs that share a label (`benchmark.run --label`) are repeats of one arm and
are reported together, with the spread across repeats. Cost is computed here
from the recorded token counts rather than stored in the run, because prices
change and the same run may be priced at peak or off-peak rates.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

DEFAULT_PRICES = Path(__file__).parent / "prices.json"


def load_runs(paths: list[Path]) -> dict[str, list[dict[str, Any]]]:
    """Group run payloads by label, falling back to the file name."""
    runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        label = payload.get("metadata", {}).get("label") or path.stem
        runs[label].append(payload)
    return dict(runs)


def run_cost(payload: dict[str, Any], prices: dict[str, Any]) -> float | None:
    """USD cost of one run from its token counts, or None if unpriced.

    Priced by the configured model id, since that is what the account is
    billed for even when the server routes it to another model.
    """
    model = payload.get("metadata", {}).get("llm_model")
    summary = payload["summary"]
    if "llm_calls" not in summary:
        return None  # recorded before token counts were kept
    if not summary["llm_calls"]:
        return 0.0
    price = prices.get(model or "")
    if price is None:
        return None
    cached = summary.get("cached_tokens", 0)
    uncached = summary.get("input_tokens", 0) - cached
    per_million = (
        uncached * price["input"]
        + cached * price["cached_input"]
        + summary.get("output_tokens", 0) * price["output"]
    )
    return per_million / 1_000_000


def headline_rows(
    runs: dict[str, list[dict[str, Any]]], prices: dict[str, Any]
) -> list[dict[str, Any]]:
    """One row per arm: rates as mean (and spread over repeats), cost and harm per case."""
    rows = []
    for label, payloads in runs.items():
        summaries = [p["summary"] for p in payloads]
        cases = summaries[0]["total"]
        costs = [run_cost(p, prices) for p in payloads]
        rows.append(
            {
                "label": label,
                "runs": len(payloads),
                "cases": cases,
                "pass": _spread([s["validator_pass_rate"] for s in summaries]),
                "truth": _spread([s["ground_truth_match_rate"] for s in summaries]),
                "detected_truth": _spread(
                    [
                        s.get("detected_ground_truth_match_rate", s["ground_truth_match_rate"])
                        for s in summaries
                    ]
                ),
                "llm_calls_per_case": _measured(summaries, "llm_calls", per_case=cases),
                "cost_per_1000_cases": (
                    None
                    if any(c is None for c in costs)
                    else _mean([c / cases * 1000 for c in costs if c is not None])
                ),
                "collateral_cases": _measured(summaries, "cases_with_collateral_changes"),
                "deleted_values_written": _measured(summaries, "deleted_values_written"),
                "mean_ms": _mean([s["mean_duration_ms"] for s in summaries]),
            }
        )
    return rows


def class_rows(runs: dict[str, list[dict[str, Any]]]) -> tuple[list[str], list[list[str]]]:
    """Ground-truth match per mutation class, one column per arm."""
    labels = list(runs)
    classes = sorted({c for ps in runs.values() for p in ps for c in p["summary"]["by_mutation"]})
    table = []
    for name in classes:
        row = [name]
        for label in labels:
            rates = [
                p["summary"]["by_mutation"][name]["ground_truth_match_rate"]
                for p in runs[label]
                if name in p["summary"]["by_mutation"]
            ]
            row.append(_fmt_spread(_spread(rates)) if rates else "")
        table.append(row)
    return labels, table


def render(
    runs: dict[str, list[dict[str, Any]]], prices: dict[str, Any], by_class: bool = False
) -> str:
    lines = [
        "| Arm | Runs | Cases | Validator pass | Ground truth | Detected truth | LLM calls/case "
        "| USD per 1k cases | Cases with collateral | Deleted values written | Mean ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in headline_rows(runs, prices):
        cost = row["cost_per_1000_cases"]
        lines.append(
            f"| {row['label']} | {row['runs']} | {row['cases']} | {_fmt_spread(row['pass'])} "
            f"| {_fmt_spread(row['truth'])} | {_fmt_spread(row['detected_truth'])} "
            f"| {_fmt(row['llm_calls_per_case'], '.2f')} | {_fmt(cost, '.3f')} "
            f"| {_fmt(row['collateral_cases'], 'g')} | {_fmt(row['deleted_values_written'], 'g')} "
            f"| {row['mean_ms']:.0f} |"
        )
    if by_class:
        labels, table = class_rows(runs)
        lines += [
            "",
            "Ground truth by mutation class:",
            "",
            "| Class | " + " | ".join(labels) + " |",
            "|---|" + "---:|" * len(labels),
        ]
        lines += ["| " + " | ".join(row) + " |" for row in table]
    return "\n".join(lines)


def _measured(
    summaries: list[dict[str, Any]], key: str, per_case: int | None = None
) -> float | None:
    """Mean of `key` over repeats, or None for runs recorded before it existed."""
    if any(key not in s for s in summaries):
        return None
    return _mean([s[key] / (per_case or 1) for s in summaries])


def _fmt(value: float | None, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _spread(values: list[float]) -> tuple[float, float | None]:
    """Mean, and standard deviation when there is more than one repeat."""
    return _mean(values), statistics.stdev(values) if len(values) > 1 else None


def _fmt_spread(spread: tuple[float, float | None]) -> str:
    mean, sd = spread
    return f"{mean:.1%}" if sd is None else f"{mean:.1%} ± {sd:.1%}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarise benchmark runs.")
    parser.add_argument("runs", type=Path, nargs="+", help="Run files from benchmark.run.")
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--by-class", action="store_true")
    args = parser.parse_args()

    prices = json.loads(args.prices.read_text(encoding="utf-8"))
    print(render(load_runs(args.runs), prices, by_class=args.by_class))


if __name__ == "__main__":
    main()
