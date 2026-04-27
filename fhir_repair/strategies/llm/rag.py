"""Spec excerpt retrieval (RAG) for LLM strategies.

The FHIR R4 spec is large and stable. Every resource we repair will need a
small slice of it (the relevant element definition, the bound ValueSet, the
applicable invariants), but we never want to send the whole spec.

This module loads a preprocessed index of the R4 spec and looks up the
slice relevant to a given error. The index is built ahead of time and
shipped under `fhir_repair/specs/r4_index.json`. Building the index is not
part of v0.1; the v0.1 retriever returns an empty string when no index is
present, and LLM strategies fall back to running without spec context.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fhir_repair.core.models import ValidationError


class SpecRetriever:
    """Look up spec excerpts relevant to a validation error.

    The retriever is a single object reused across many calls; the index is
    loaded once and held in memory. The lookup is O(1) on a path-keyed
    dictionary, so retrieval cost is negligible compared to the LLM call
    itself.
    """

    def __init__(self, index_path: Path | None = None):
        self._index: dict[str, Any] = {}
        if index_path is not None and index_path.exists():
            self._index = json.loads(index_path.read_text(encoding="utf-8"))

    def retrieve(self, error: ValidationError) -> str:
        """Return the spec excerpt relevant to `error`, or an empty string.

        The lookup uses the error location stripped of array indices, so
        `Patient.contact[0].telecom[0].value` looks up `Patient.contact.telecom.value`.
        """
        if not self._index:
            return ""

        normalised = _strip_indices(error.location)
        excerpt = self._index.get(normalised, "")
        return str(excerpt) if excerpt else ""


def _strip_indices(path: str) -> str:
    """Remove `[N]` index suffixes from each segment.

    `Patient.contact[0].telecom[1].value` -> `Patient.contact.telecom.value`
    """
    out = []
    for raw in path.split("."):
        bracket = raw.find("[")
        out.append(raw[:bracket] if bracket >= 0 else raw)
    return ".".join(out)
