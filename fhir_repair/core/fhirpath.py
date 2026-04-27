"""FHIRPath helper.

Two operations are exposed:

  - `evaluate(resource, path)`: full FHIRPath evaluation via `fhirpathpy`.
    Returns a flat collection per FHIRPath semantics. Used by RAG and any
    place that wants FHIRPath query semantics.
  - `get_at_path(resource, path)` and `set_at_path(resource, path, value)`:
    direct dict walks symmetric with each other, used by repair strategies
    that need to inspect or assign the *raw* value at a path.

Why two surfaces: FHIRPath is a query language and flattens collections.
That is correct for spec-lookup queries but wrong for cardinality fixes,
where the strategy needs to see "this scalar field has been wrapped in a
list" and not have FHIRPath silently unwrap the list before the strategy
sees it. Walking the dict directly preserves the raw structure.

HAPI emits error locations using a small syntactic subset (member access
plus integer indices), so we parse that subset directly. General FHIRPath
assignment is undefined and out of scope.
"""

from __future__ import annotations

import re
from typing import Any

import fhirpathpy

# Matches a path segment: `name` or `name[42]`.
_SEGMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?$")


def evaluate(resource: dict[str, Any], path: str) -> list[Any]:
    """Evaluate a FHIRPath expression against a resource.

    Returns a list because FHIRPath always returns a collection. An empty
    list means the path matched nothing. Note: FHIRPath flattens collections,
    so `Observation.status` against `{"status": ["final"]}` returns
    `["final"]` flat. Use `get_at_path` if you want to see the raw list.
    """
    return list(fhirpathpy.evaluate(resource, path))


def get_at_path(resource: dict[str, Any], path: str) -> Any:
    """Return the raw value at `path` on `resource`, or None if absent.

    Walks the resource as a plain dict tree. Unlike `evaluate`, this does
    not apply FHIRPath collection flattening: a singleton-wrapped scalar
    comes back as a one-element list, which is what cardinality fixes
    need to see.
    """
    parts = _parse_simple_path(path)

    if parts and isinstance(parts[0], str) and parts[0] == resource.get("resourceType"):
        parts = parts[1:]

    cursor: Any = resource
    for part in parts:
        try:
            cursor = _descend(cursor, part)
        except (KeyError, IndexError, TypeError):
            return None

    return cursor


def set_at_path(resource: dict[str, Any], path: str, value: Any) -> None:
    """Assign `value` at `path` on `resource`, mutating in place.

    Only handles simple member-access-with-index paths
    (e.g., `Patient.contact[0].telecom[0].value`). This is the syntactic
    subset HAPI emits as error locations. Raises ValueError on syntax we
    do not handle.
    """
    parts = _parse_simple_path(path)

    # Drop the leading resourceType segment if present, so the parsed parts
    # start from the first child of the resource dict.
    if parts and isinstance(parts[0], str) and parts[0] == resource.get("resourceType"):
        parts = parts[1:]

    if not parts:
        raise ValueError(f"Path {path!r} did not contain any addressable segments")

    cursor: Any = resource
    for part in parts[:-1]:
        cursor = _descend(cursor, part)

    _assign(cursor, parts[-1], value)


def _parse_simple_path(path: str) -> list[str | tuple[str, int]]:
    """Parse `Patient.contact[0].telecom[1].value` into a sequence of segments.

    Each segment is either a string (plain attribute) or a (name, index)
    tuple (indexed attribute).
    """
    if not path:
        raise ValueError("Cannot parse an empty path")

    out: list[str | tuple[str, int]] = []
    for raw in path.split("."):
        match = _SEGMENT_RE.match(raw)
        if not match:
            raise ValueError(f"Cannot parse FHIRPath segment: {raw!r}")
        name, index = match.group(1), match.group(2)
        out.append((name, int(index)) if index is not None else name)
    return out


def _descend(cursor: Any, part: str | tuple[str, int]) -> Any:
    if isinstance(part, tuple):
        name, index = part
        return cursor[name][index]
    return cursor[part]


def _assign(cursor: Any, part: str | tuple[str, int], value: Any) -> None:
    if isinstance(part, tuple):
        name, index = part
        cursor[name][index] = value
    else:
        cursor[part] = value
