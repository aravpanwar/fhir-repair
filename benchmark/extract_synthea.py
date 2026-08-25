"""Extract individual resources from Synthea FHIR bundles.

Synthea exports one transaction Bundle per patient, each holding hundreds of
entries. The benchmark corpus wants a flat directory of single resources, so
this walks the bundles, picks a quota of each resource type, and writes one
file per resource.

Two properties matter for the benchmark to mean anything:

  - Determinism. Bundles are processed in sorted filename order and entries
    in document order, so the same Synthea output always yields the same
    corpus. There is no sampling and no randomness.
  - Round-trippable references. Extracting a resource out of its bundle
    breaks `urn:uuid:` references, which HAPI reports as errors and would
    show up as pre-existing validation failures in every benchmark run.
    References are rewritten to `<Type>/<id>` form when the target is in
    the same bundle, and dropped when it is not.

Usage:

    python -m benchmark.extract_synthea output/fhir benchmark/corpus/synthea_valid

See `benchmark/corpus/SYNTHEA-GENERATION.md` for how to produce the input
bundles.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Resource types the corpus covers, with how many of each to keep. These are
# the types the mutation set can actually corrupt: mutations need a date, a
# decimal, a bound code, a telecom, or an identifier to work on.
DEFAULT_QUOTAS: dict[str, int] = {
    "Patient": 20,
    "Observation": 20,
    "Condition": 20,
    "MedicationRequest": 20,
    "Encounter": 20,
}

# Elements stripped from every extracted resource. `meta` carries Synthea's
# own versioning and lastUpdated, which add churn to diffs without affecting
# validation. `text` is a generated narrative: large, noisy, and irrelevant
# to the structural errors the benchmark measures.
_STRIP_ELEMENTS = ("meta", "text")


def extract_corpus(
    bundle_dir: Path,
    output_dir: Path,
    quotas: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Extract resources from every bundle in `bundle_dir` into `output_dir`.

    Returns a manifest describing what was written, one entry per file, in
    the order written.
    """
    resolved_quotas = dict(quotas or DEFAULT_QUOTAS)
    counts: dict[str, int] = {rtype: 0 for rtype in resolved_quotas}
    manifest: list[dict[str, Any]] = []

    output_dir.mkdir(parents=True, exist_ok=True)

    for bundle_path in sorted(bundle_dir.glob("*.json")):
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        if bundle.get("resourceType") != "Bundle":
            continue

        resources = _bundle_resources(bundle)
        id_index = _build_id_index(resources)

        for resource in resources:
            rtype = resource.get("resourceType")
            if rtype not in resolved_quotas:
                continue
            if counts[rtype] >= resolved_quotas[rtype]:
                continue

            counts[rtype] += 1
            cleaned = _clean(resource, id_index)

            # Sequential names keep the corpus stable and readable, and match
            # the flat `<Type>-NNN.json` layout the mutation harness globs.
            filename = f"{rtype}-{counts[rtype]:03d}.json"
            (output_dir / filename).write_text(
                json.dumps(cleaned, indent=2) + "\n",
                encoding="utf-8",
            )
            manifest.append(
                {
                    "file": filename,
                    "resource_type": rtype,
                    "source_bundle": bundle_path.name,
                    "original_id": resource.get("id"),
                }
            )

        if all(counts[rtype] >= resolved_quotas[rtype] for rtype in resolved_quotas):
            break

    return manifest


def _bundle_resources(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the resources in a bundle, in document order."""
    out = []
    for entry in bundle.get("entry", []):
        resource = entry.get("resource")
        if isinstance(resource, dict) and resource.get("resourceType"):
            out.append(resource)
    return out


def _build_id_index(resources: list[dict[str, Any]]) -> dict[str, str]:
    """Map each resource id to its `<Type>/<id>` reference form.

    Synthea writes intra-bundle references as `urn:uuid:<id>`, where `<id>`
    is the target's `id`. Indexing by bare id lets us rewrite those to the
    relative form that resolves outside the bundle.
    """
    index: dict[str, str] = {}
    for resource in resources:
        rid = resource.get("id")
        rtype = resource.get("resourceType")
        if isinstance(rid, str) and isinstance(rtype, str):
            index[rid] = f"{rtype}/{rid}"
    return index


def _clean(resource: dict[str, Any], id_index: dict[str, str]) -> dict[str, Any]:
    """Strip noisy elements and fix references, without mutating the input."""
    out = {k: v for k, v in resource.items() if k not in _STRIP_ELEMENTS}
    return _rewrite_references(out, id_index)


def _rewrite_references(node: Any, id_index: dict[str, str]) -> Any:
    """Rewrite or drop `urn:uuid:` references throughout a resource tree.

    A reference whose target is in the same bundle becomes `<Type>/<id>`.
    One whose target is not (Synthea references some resources it does not
    export) is dropped: leaving an unresolvable urn:uuid would make every
    resource fail validation before any mutation was applied.
    """
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "reference" and isinstance(value, str):
                rewritten = _resolve_reference(value, id_index)
                if rewritten is None:
                    continue
                out[key] = rewritten
            else:
                out[key] = _rewrite_references(value, id_index)
        # A Reference object whose only content was an unresolvable pointer
        # is dropped by the caller if it is now empty except for display.
        return out
    if isinstance(node, list):
        return [_rewrite_references(item, id_index) for item in node]
    return node


def _resolve_reference(value: str, id_index: dict[str, str]) -> str | None:
    """Turn a urn:uuid reference into relative form, or None if unresolvable.

    Non-urn references (already relative, or absolute URLs to an external
    server) pass through untouched.
    """
    if not value.startswith("urn:uuid:"):
        return value
    target_id = value[len("urn:uuid:") :]
    return id_index.get(target_id)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract single resources from Synthea FHIR bundles.",
    )
    parser.add_argument(
        "bundle_dir",
        type=Path,
        help="Directory of Synthea bundle JSON files (Synthea's output/fhir).",
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Where to write extracted resources.",
    )
    parser.add_argument(
        "--per-type",
        type=int,
        default=None,
        help="Override the per-resource-type quota for every type.",
    )
    args = parser.parse_args()

    override = None
    if args.per_type is not None:
        override = {rtype: args.per_type for rtype in DEFAULT_QUOTAS}

    written = extract_corpus(args.bundle_dir, args.output_dir, quotas=override)

    # Deliberately a sibling of the corpus directory, not inside it: the
    # mutation harness globs `*.json` in the corpus and would try to read
    # the manifest as a resource.
    manifest_path = args.output_dir.parent / f"{args.output_dir.name}-extraction.json"
    manifest_path.write_text(json.dumps(written, indent=2) + "\n", encoding="utf-8")

    by_type: dict[str, int] = {}
    for item in written:
        by_type[item["resource_type"]] = by_type.get(item["resource_type"], 0) + 1
    summary = ", ".join(f"{count} {rtype}" for rtype, count in sorted(by_type.items()))
    print(f"Wrote {len(written)} resources to {args.output_dir} ({summary})")
