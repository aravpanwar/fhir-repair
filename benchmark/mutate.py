"""Programmatic mutation of valid FHIR resources for benchmarking.

Each mutation is a deterministic function that takes a valid resource,
applies one well-defined corruption, and returns a broken copy plus
metadata describing what was done. The metadata is the ground truth a
benchmark scorer compares the repaired output against.

v0.1 ships three mutations to keep the harness small while exercising
both the deterministic-fix path (date format, singleton wrap) and the
refusal path (missing required). The remaining five mutations from the
study design are added in v0.2.
"""

from __future__ import annotations

import copy
import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# A mutation function signature: takes a resource and a deterministic random
# source, returns the mutated resource plus a description of what changed.
Mutation = Callable[[dict[str, Any], random.Random], "MutationResult"]


@dataclass
class MutationResult:
    """Output of a single mutation applied to a single resource."""

    resource: dict[str, Any]
    description: str
    location: str
    original_value: Any


def mutate_date_format(
    resource: dict[str, Any],
    rng: random.Random,
) -> MutationResult | None:
    """Corrupt a date field by stripping zero-padding."""
    field = _first_date_field(resource)
    if field is None:
        return None

    name, original = field
    parts = original.split("-")
    if len(parts) != 3:
        return None

    # Drop padding on month and day. Result: "1990-3-5".
    new = f"{parts[0]}-{int(parts[1])}-{int(parts[2])}"
    out = copy.deepcopy(resource)
    out[name] = new
    return MutationResult(
        resource=out,
        description="date-format-unpadded",
        location=f"{resource['resourceType']}.{name}",
        original_value=original,
    )


def mutate_singleton_wrap(
    resource: dict[str, Any],
    rng: random.Random,
) -> MutationResult | None:
    """Wrap a scalar field in a singleton list."""
    candidates = _scalar_field_names(resource)
    if not candidates:
        return None

    name = rng.choice(candidates)
    original = resource[name]
    out = copy.deepcopy(resource)
    out[name] = [original]
    return MutationResult(
        resource=out,
        description="singleton-wrapped-array",
        location=f"{resource['resourceType']}.{name}",
        original_value=original,
    )


def mutate_missing_required(
    resource: dict[str, Any],
    rng: random.Random,
) -> MutationResult | None:
    """Delete a field. Whether this triggers a missing-required error depends
    on which field is selected and the target profile.
    """
    candidates = _scalar_field_names(resource)
    if not candidates:
        return None

    name = rng.choice(candidates)
    original = resource[name]
    out = copy.deepcopy(resource)
    del out[name]
    return MutationResult(
        resource=out,
        description=f"missing-{name}",
        location=f"{resource['resourceType']}.{name}",
        original_value=original,
    )


def mutate_decimal_format(
    resource: dict[str, Any],
    rng: random.Random,
) -> MutationResult | None:
    """Corrupt a decimal value with a locale comma separator."""
    quantity = resource.get("valueQuantity")
    if not isinstance(quantity, dict):
        return None

    value = quantity.get("value")
    # bool is a subclass of int; exclude it. Integers stringify without a
    # separator to swap, so only floats produce a meaningful corruption.
    if not isinstance(value, float):
        return None

    out = copy.deepcopy(resource)
    out["valueQuantity"]["value"] = str(value).replace(".", ",")
    return MutationResult(
        resource=out,
        description="decimal-locale-comma",
        location=f"{resource['resourceType']}.valueQuantity.value",
        original_value=value,
    )


# Bound code fields and a corruption for each known value. Replacing a code
# with an abbreviation pushes it outside the bound ValueSet, which is the
# interpretive case the terminology strategy is meant to handle.
_BOUND_CODE_FIELDS = ("gender", "status")
_CODE_CORRUPTION = {
    "male": "M",
    "female": "F",
    "other": "O",
    "unknown": "U",
    "final": "F",
    "preliminary": "P",
    "amended": "A",
    "registered": "R",
}


def mutate_invalid_code_binding(
    resource: dict[str, Any],
    rng: random.Random,
) -> MutationResult | None:
    """Replace a bound code with an out-of-ValueSet abbreviation."""
    for name in _BOUND_CODE_FIELDS:
        value = resource.get(name)
        if not isinstance(value, str) or not value:
            continue

        corrupted = _CODE_CORRUPTION.get(value)
        if corrupted is None or corrupted == value:
            continue

        out = copy.deepcopy(resource)
        out[name] = corrupted
        return MutationResult(
            resource=out,
            description=f"invalid-code-{name}",
            location=f"{resource['resourceType']}.{name}",
            original_value=value,
        )

    return None


# Registry of mutations included in the benchmark.
MUTATIONS: dict[str, Mutation] = {
    "date_format": mutate_date_format,
    "singleton_wrap": mutate_singleton_wrap,
    "missing_required": mutate_missing_required,
    "decimal_format": mutate_decimal_format,
    "invalid_code_binding": mutate_invalid_code_binding,
}


def mutate_corpus(
    valid_dir: Path,
    output_dir: Path,
    seed: int = 12345,
) -> list[dict[str, Any]]:
    """Apply every mutation to every resource in `valid_dir`.

    Writes mutated resources to `output_dir`/<mutation>/<filename> and
    returns the list of mutation manifests for the benchmark scorer.
    """
    rng = random.Random(seed)
    manifests: list[dict[str, Any]] = []

    for resource_path in sorted(valid_dir.glob("*.json")):
        resource = json.loads(resource_path.read_text(encoding="utf-8"))
        for mutation_name, mutation_fn in MUTATIONS.items():
            result = mutation_fn(resource, rng)
            if result is None:
                continue

            target_dir = output_dir / mutation_name
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / resource_path.name
            target_path.write_text(
                json.dumps(result.resource, indent=2),
                encoding="utf-8",
            )

            manifests.append(
                {
                    "mutation": mutation_name,
                    "description": result.description,
                    "location": result.location,
                    "original_value": result.original_value,
                    "valid_path": str(resource_path),
                    "mutated_path": str(target_path),
                }
            )

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifests, indent=2), encoding="utf-8")
    return manifests


def _first_date_field(resource: dict[str, Any]) -> tuple[str, str] | None:
    """Return the first plain-string field whose value looks like an ISO date."""
    import re

    iso = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for name, value in resource.items():
        if isinstance(value, str) and iso.match(value):
            return name, value
    return None


def _scalar_field_names(resource: dict[str, Any]) -> list[str]:
    """Return field names whose value is a scalar (not list, not dict).

    Excludes `resourceType` and `id`, which are structural and either
    required by the spec (resourceType) or optional but commonly present (id).
    """
    skip = {"resourceType", "id", "meta"}
    return [
        name
        for name, value in resource.items()
        if name not in skip and not isinstance(value, list | dict)
    ]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Mutate a FHIR corpus for benchmarking.")
    parser.add_argument("valid_dir", type=Path, help="Directory of valid FHIR JSON files.")
    parser.add_argument("output_dir", type=Path, help="Where to write mutated resources.")
    parser.add_argument("--seed", type=int, default=12345)
    args = parser.parse_args()

    manifests = mutate_corpus(args.valid_dir, args.output_dir, seed=args.seed)
    print(f"Wrote {len(manifests)} mutated resources to {args.output_dir}")
