"""Tests for the hallucination guard."""

from __future__ import annotations

import pytest

from fhir_repair.core.guard import PERMISSIONS, HallucinationGuard


def test_default_permissions_are_conservative():
    guard = HallucinationGuard()
    assert guard.allow_reformat is True
    assert guard.allow_bind_required_valueset is True
    assert guard.allow_bind_extensible_valueset is False
    assert guard.allow_add_missing_required_field is False
    assert guard.allow_change_existing_clinical_value is False


def test_strict_denies_everything():
    guard = HallucinationGuard.strict()
    for permission in PERMISSIONS:
        assert guard.is_allowed(permission) is False


def test_permissive_grants_everything():
    guard = HallucinationGuard.permissive()
    for permission in PERMISSIONS:
        assert guard.is_allowed(permission) is True


def test_unknown_permission_raises():
    guard = HallucinationGuard()
    with pytest.raises(ValueError, match="Unknown permission"):
        guard.is_allowed("allow_break_things")


def test_to_dict_contains_all_permissions():
    guard = HallucinationGuard()
    snapshot = guard.to_dict()
    assert set(snapshot.keys()) == set(PERMISSIONS)
    assert all(isinstance(v, bool) for v in snapshot.values())
