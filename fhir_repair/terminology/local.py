"""Local terminology adapter.

Loads the small FHIR-internal CodeSystems shipped under
`fhir_repair/specs/r4_codesystems.json` and answers lookups against them
without any network call. Sufficient for administrative enumerations
(gender, contact-point-system, address-use, etc.) and the boolean-coded
sets the spec defines inline.

Anything outside the local index returns "not found." For SNOMED, LOINC,
RxNorm, and other large terminologies, configure a real terminology
service in `repair-config.yaml`.
"""

from __future__ import annotations

import json
from pathlib import Path

from fhir_repair.terminology.base import CodeMatch, ValidateCodeResult

# The shipped index is keyed by CodeSystem URL. Each entry maps a code to
# a display string. Compact and small enough to load at module import.
_DEFAULT_INDEX_PATH = Path(__file__).parent.parent / "specs" / "r4_codesystems.json"


class LocalTerminology:
    """In-process terminology service for FHIR-internal enumerations."""

    def __init__(self, index_path: Path | None = None):
        path = index_path or _DEFAULT_INDEX_PATH
        if path.exists():
            self._index: dict[str, dict[str, str]] = json.loads(path.read_text(encoding="utf-8"))
        else:
            self._index = {}

    def validate_code(self, system: str, code: str) -> ValidateCodeResult:
        codes = self._index.get(system)
        if codes is None:
            return ValidateCodeResult(
                valid=False,
                message=f"CodeSystem {system!r} not in local index",
            )
        if code in codes:
            return ValidateCodeResult(valid=True, display=codes[code])
        return ValidateCodeResult(
            valid=False,
            message=f"Code {code!r} not in CodeSystem {system!r}",
        )

    def lookup_in_value_set(
        self,
        value_set_url: str,
        term: str,
    ) -> list[CodeMatch]:
        """Naive substring + exact-code search across the local index.

        Production deployments should plug in a real terminology server.
        This local fallback is intended only for the small enumerations
        commonly needed during repair (`AdministrativeGender`, etc.).
        """
        # The local index is keyed by CodeSystem URL, but binding errors
        # reference ValueSet URLs. For the small FHIR-internal cases, the
        # ValueSet and CodeSystem urls share a stem; we accept either.
        candidates: list[CodeMatch] = []
        term_lower = term.strip().lower()

        for system, codes in self._index.items():
            for code, display in codes.items():
                if code.lower() == term_lower:
                    candidates.append(
                        CodeMatch(system=system, code=code, display=display, confidence=1.0)
                    )
                elif term_lower and term_lower in display.lower():
                    candidates.append(
                        CodeMatch(system=system, code=code, display=display, confidence=0.5)
                    )

        # Stable sort: highest confidence first, then alphabetically by code
        # so output is deterministic across runs.
        candidates.sort(key=lambda m: (-m.confidence, m.code))
        return candidates
