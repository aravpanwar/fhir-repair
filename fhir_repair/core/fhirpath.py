"""FHIRPath helper.

Three operations are exposed:

  - `evaluate(resource, path)`: full FHIRPath evaluation via `fhirpathpy`.
    Returns a flat collection per FHIRPath semantics. Used by RAG and any
    place that wants FHIRPath query semantics.
  - `get_at_path(resource, path)` and `set_at_path(resource, path, value)`:
    direct dict walks symmetric with each other, used by repair strategies
    that need to inspect or assign the *raw* value at a path.
  - `delete_at_path(resource, path)`: remove the element at a path. Needed
    because some fixes are a removal, not a replacement: an invariant that
    forbids two fields coexisting is satisfied by dropping one of them, and
    writing a null in its place would leave the resource just as invalid.

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

# Matches a path segment: `name`, `name[42]`, or `name[x]`.
_SEGMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+|x)\])?$")

# FHIRPath choice-element notation: `<base>.ofType(<Type>)`. HAPI emits
# this for polymorphic fields like `value[x]`, e.g. `Observation.value.
# ofType(Quantity)`. The JSON property name is `<base><Type>` with the
# type's first letter capitalised, e.g. `valueQuantity`.
_OF_TYPE_RE = re.compile(r"\.ofType\(([A-Za-z][A-Za-z0-9]*)\)")

# Abstract choice-element notation: `<base>[x]`, e.g. `Observation.value[x]`.
# Unlike the ofType form this does not name a concrete type, so it cannot be
# canonicalised from the path alone. It is resolved against the resource by
# looking for the one `<base><Type>` key that is actually present.
_CHOICE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\[x\]$")


class Choice:
    """An unresolved `<base>[x]` path segment.

    Carries only the base name. Which concrete property it refers to
    (`valueQuantity`, `valueString`, ...) depends on the resource, so it is
    resolved during the walk rather than at parse time.
    """

    __slots__ = ("base",)

    def __init__(self, base: str) -> None:
        self.base = base

    def resolve(self, cursor: Any) -> str | None:
        """Return the concrete property name present on `cursor`, if any.

        Ambiguity is treated as unresolvable: a well-formed resource carries
        exactly one member of a choice element, and guessing between two
        would risk repairing the wrong field.
        """
        if not isinstance(cursor, dict):
            return None
        prefix = self.base
        matches = [
            key
            for key in cursor
            if key.startswith(prefix) and len(key) > len(prefix) and key[len(prefix)].isupper()
        ]
        return matches[0] if len(matches) == 1 else None

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Choice) and other.base == self.base

    def __hash__(self) -> int:
        return hash((Choice, self.base))

    def __repr__(self) -> str:
        return f"Choice({self.base!r})"


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

    The abstract `<name>[x]` choice notation is resolved against the
    resource: `Observation.value[x].value` finds `valueQuantity.value` when
    that is the member present. Returns None when the path is unparseable,
    when it matches nothing, or when a choice element is ambiguous.
    Strategies that depend on a concrete value handle the None case as a
    refusal, which routes the error to the next chain entry or to
    unresolved.
    """
    try:
        parts = _parse_simple_path(path)
    except ValueError:
        return None

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


def delete_at_path(resource: dict[str, Any], path: str) -> bool:
    """Remove the element at `path` on `resource`, mutating in place.

    Returns True if something was removed, False if the path was already
    absent. Deleting an indexed segment removes that list entry and shifts
    the rest down, which is the FHIR-correct result: a list with a hole in
    it is not representable in JSON.

    Raises ValueError on path syntax we do not handle, matching
    `set_at_path`.
    """
    parts = _parse_simple_path(path)

    if parts and isinstance(parts[0], str) and parts[0] == resource.get("resourceType"):
        parts = parts[1:]

    if not parts:
        raise ValueError(f"Path {path!r} did not contain any addressable segments")

    cursor: Any = resource
    for part in parts[:-1]:
        try:
            cursor = _descend(cursor, part)
        except (KeyError, IndexError, TypeError):
            return False

    return _remove(cursor, parts[-1])


def _remove(cursor: Any, part: str | tuple[str, int] | Choice) -> bool:
    """Delete a single segment from its parent container."""
    try:
        if isinstance(part, Choice):
            resolved = part.resolve(cursor)
            if resolved is None:
                return False
            del cursor[resolved]
        elif isinstance(part, tuple):
            name, index = part
            del cursor[name][index]
        else:
            del cursor[part]
    except (KeyError, IndexError, TypeError):
        return False
    return True


def _parse_simple_path(path: str) -> list[str | tuple[str, int] | Choice]:
    """Parse `Patient.contact[0].telecom[1].value` into a sequence of segments.

    Each segment is a string (plain attribute), a (name, index) tuple
    (indexed attribute), or a `Choice` (abstract `name[x]` element, resolved
    later against the resource). FHIRPath's concrete choice notation is
    canonicalised first: `Observation.value.ofType(Quantity).value` becomes
    `Observation.valueQuantity.value`.
    """
    if not path:
        raise ValueError("Cannot parse an empty path")

    canonical = _canonicalise_choice_elements(path)

    out: list[str | tuple[str, int] | Choice] = []
    for raw in canonical.split("."):
        match = _SEGMENT_RE.match(raw)
        if not match:
            raise ValueError(f"Cannot parse FHIRPath segment: {raw!r}")
        name, index = match.group(1), match.group(2)
        if index == "x":
            out.append(Choice(name))
        elif index is not None:
            out.append((name, int(index)))
        else:
            out.append(name)
    return out


def _canonicalise_choice_elements(path: str) -> str:
    """Translate FHIRPath choice-element notation to JSON property names.

    `Observation.value.ofType(Quantity).value` ->
    `Observation.valueQuantity.value`

    Idempotent: paths with no choice elements pass through unchanged.
    """

    def replace(match: re.Match[str]) -> str:
        type_name = match.group(1)
        return type_name[0].upper() + type_name[1:]

    return _OF_TYPE_RE.sub(replace, path)


def _descend(cursor: Any, part: str | tuple[str, int] | Choice) -> Any:
    if isinstance(part, Choice):
        resolved = part.resolve(cursor)
        if resolved is None:
            raise KeyError(part.base)
        return cursor[resolved]
    if isinstance(part, tuple):
        name, index = part
        return cursor[name][index]
    return cursor[part]


def _assign(cursor: Any, part: str | tuple[str, int] | Choice, value: Any) -> None:
    if isinstance(part, Choice):
        resolved = part.resolve(cursor)
        if resolved is None:
            # Writing to an unresolved choice element would have to invent
            # the type suffix, so refuse rather than guess.
            raise ValueError(f"Cannot resolve choice element {part.base}[x] on this resource")
        cursor[resolved] = value
    elif isinstance(part, tuple):
        name, index = part
        cursor[name][index] = value
    else:
        cursor[part] = value
