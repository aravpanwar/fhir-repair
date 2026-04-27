"""Hallucination guard.

LLM-introduced changes fall into three categories with very different risks:
reformatting wire-format values the user already provided, binding to a
closed ValueSet the spec dictates, and inventing or overwriting clinical
data. A single boolean conflates them.

The guard splits each behaviour into an independent permission, defaulting
conservatively. Strategies declare which permission they require, and the
dispatcher refuses to invoke a strategy without the matching permission,
recording the refusal in the audit log.

This makes the configurable surface explicit. "What level of LLM autonomy
is this run operating at?" becomes five independent dials, not one
ambiguous boolean.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

# The permission names are part of the audit log schema. Renaming a
# permission here breaks downstream log consumers, so any change is a
# major version bump on the audit schema.
PERMISSIONS: tuple[str, ...] = (
    "allow_reformat",
    "allow_bind_required_valueset",
    "allow_bind_extensible_valueset",
    "allow_add_missing_required_field",
    "allow_change_existing_clinical_value",
)


@dataclass
class HallucinationGuard:
    """Five independent permissions for LLM-introduced changes.

    Defaults are conservative: anything that touches clinical content
    requires explicit opt-in.
    """

    # Rewrite an existing value to a valid wire format. Lowest risk: the
    # clinical content is unchanged, only the encoding moves.
    allow_reformat: bool = True

    # Pick a code from a ValueSet bound with `required` strength. Medium
    # risk: the answer set is closed, but the mapping is interpretive.
    allow_bind_required_valueset: bool = True

    # Pick a code from a ValueSet bound with `extensible` or `preferred`
    # strength. Medium-high risk: the answer set is open and the LLM
    # may pick a code outside the suggested set.
    allow_bind_extensible_valueset: bool = False

    # Invent a value for a required field that was missing. High risk:
    # there is no source signal in the input, so any value is interpretation.
    allow_add_missing_required_field: bool = False

    # Replace a clinical value the user provided with a different one.
    # High risk: silently overwriting clinical data is the most dangerous
    # repair we could perform, and is off by default.
    allow_change_existing_clinical_value: bool = False

    def is_allowed(self, permission: str) -> bool:
        """Check whether a named permission is granted.

        Raises ValueError if the permission name is unknown. This catches
        typos in strategy implementations early rather than silently
        denying.
        """
        if permission not in PERMISSIONS:
            raise ValueError(
                f"Unknown permission: {permission!r}. Known permissions: {', '.join(PERMISSIONS)}"
            )
        return bool(getattr(self, permission))

    def to_dict(self) -> dict[str, bool]:
        """Serialise the current permission set, e.g. for audit metadata."""
        return {f.name: bool(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def strict(cls) -> HallucinationGuard:
        """Deny every permission. Used by the `strict_mode` shortcut."""
        return cls(
            allow_reformat=False,
            allow_bind_required_valueset=False,
            allow_bind_extensible_valueset=False,
            allow_add_missing_required_field=False,
            allow_change_existing_clinical_value=False,
        )

    @classmethod
    def permissive(cls) -> HallucinationGuard:
        """Grant every permission. Intended for benchmarking only."""
        return cls(
            allow_reformat=True,
            allow_bind_required_valueset=True,
            allow_bind_extensible_valueset=True,
            allow_add_missing_required_field=True,
            allow_change_existing_clinical_value=True,
        )
