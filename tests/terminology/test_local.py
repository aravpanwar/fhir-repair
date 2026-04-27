"""Tests for the local terminology adapter."""

from __future__ import annotations

from fhir_repair.terminology.local import LocalTerminology


def test_validate_known_code():
    tx = LocalTerminology()
    result = tx.validate_code("http://hl7.org/fhir/administrative-gender", "male")
    assert result.valid is True
    assert result.display == "Male"


def test_validate_unknown_code():
    tx = LocalTerminology()
    result = tx.validate_code("http://hl7.org/fhir/administrative-gender", "M")
    assert result.valid is False


def test_validate_unknown_system():
    tx = LocalTerminology()
    result = tx.validate_code("http://example.com/codes", "foo")
    assert result.valid is False
    assert "not in local index" in (result.message or "")


def test_lookup_exact_code_match():
    tx = LocalTerminology()
    matches = tx.lookup_in_value_set(
        "http://hl7.org/fhir/ValueSet/administrative-gender",
        "male",
    )
    assert any(m.code == "male" and m.confidence == 1.0 for m in matches)


def test_lookup_substring_match():
    tx = LocalTerminology()
    matches = tx.lookup_in_value_set(
        "http://hl7.org/fhir/ValueSet/contact-point-system",
        "phone",
    )
    assert any(m.code == "phone" for m in matches)
