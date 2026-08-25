"""Tests for the Synthea bundle extractor.

The fixtures here mimic Synthea's export shape (transaction bundles with
urn:uuid intra-bundle references) without needing Java or a real Synthea
run.
"""

from __future__ import annotations

import json

from benchmark.extract_synthea import extract_corpus


def _bundle(*resources):
    return {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [{"resource": r} for r in resources],
    }


def _patient(pid="p1"):
    return {
        "resourceType": "Patient",
        "id": pid,
        "meta": {"lastUpdated": "2026-01-01T00:00:00Z"},
        "text": {"status": "generated", "div": "<div>noise</div>"},
        "gender": "female",
        "birthDate": "1980-04-11",
    }


def _observation(oid="o1", subject="p1"):
    return {
        "resourceType": "Observation",
        "id": oid,
        "status": "final",
        "subject": {"reference": f"urn:uuid:{subject}"},
        "valueQuantity": {"value": 70.5, "unit": "kg"},
    }


def _write(tmp_path, name, bundle):
    path = tmp_path / name
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return path


def test_extracts_one_file_per_resource(tmp_path):
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    out = tmp_path / "out"
    _write(bundles, "a.json", _bundle(_patient(), _observation()))

    manifest = extract_corpus(bundles, out)

    assert (out / "Patient-001.json").exists()
    assert (out / "Observation-001.json").exists()
    assert len(manifest) == 2


def test_strips_meta_and_narrative(tmp_path):
    """Synthea's meta and generated narrative add diff churn, not signal."""
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    out = tmp_path / "out"
    _write(bundles, "a.json", _bundle(_patient()))

    extract_corpus(bundles, out)
    written = json.loads((out / "Patient-001.json").read_text(encoding="utf-8"))

    assert "meta" not in written
    assert "text" not in written
    assert written["birthDate"] == "1980-04-11"


def test_rewrites_intra_bundle_reference(tmp_path):
    """urn:uuid only resolves inside the bundle, so it becomes Type/id."""
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    out = tmp_path / "out"
    _write(bundles, "a.json", _bundle(_patient("abc"), _observation("o1", subject="abc")))

    extract_corpus(bundles, out)
    obs = json.loads((out / "Observation-001.json").read_text(encoding="utf-8"))

    assert obs["subject"]["reference"] == "Patient/abc"


def test_drops_unresolvable_reference(tmp_path):
    """A dangling urn:uuid would fail validation before any mutation ran."""
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    out = tmp_path / "out"
    _write(bundles, "a.json", _bundle(_observation("o1", subject="not-in-bundle")))

    extract_corpus(bundles, out)
    obs = json.loads((out / "Observation-001.json").read_text(encoding="utf-8"))

    assert "reference" not in obs["subject"]


def test_relative_and_absolute_references_pass_through(tmp_path):
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    out = tmp_path / "out"
    observation = _observation()
    observation["subject"] = {"reference": "Patient/already-relative"}
    observation["performer"] = [{"reference": "https://example.test/fhir/Practitioner/1"}]
    _write(bundles, "a.json", _bundle(observation))

    extract_corpus(bundles, out)
    obs = json.loads((out / "Observation-001.json").read_text(encoding="utf-8"))

    assert obs["subject"]["reference"] == "Patient/already-relative"
    assert obs["performer"][0]["reference"] == "https://example.test/fhir/Practitioner/1"


def test_quota_caps_each_resource_type(tmp_path):
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    out = tmp_path / "out"
    many = [_observation(f"o{i}") for i in range(10)]
    _write(bundles, "a.json", _bundle(_patient(), *many))

    manifest = extract_corpus(bundles, out, quotas={"Observation": 3})

    observations = [m for m in manifest if m["resource_type"] == "Observation"]
    assert len(observations) == 3
    # Patient was not in the quota map, so it is not extracted at all.
    assert not any(m["resource_type"] == "Patient" for m in manifest)


def test_numbering_continues_across_bundles(tmp_path):
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    out = tmp_path / "out"
    _write(bundles, "a.json", _bundle(_patient("p1")))
    _write(bundles, "b.json", _bundle(_patient("p2")))

    extract_corpus(bundles, out, quotas={"Patient": 5})

    assert (out / "Patient-001.json").exists()
    assert (out / "Patient-002.json").exists()


def test_extraction_is_deterministic(tmp_path):
    """Same input, same corpus: the benchmark depends on it."""
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    _write(bundles, "b.json", _bundle(_patient("p2"), _observation("o2")))
    _write(bundles, "a.json", _bundle(_patient("p1"), _observation("o1")))

    first = extract_corpus(bundles, tmp_path / "one")
    second = extract_corpus(bundles, tmp_path / "two")

    assert first == second
    # Sorted bundle order, so a.json is processed first.
    assert first[0]["source_bundle"] == "a.json"


def test_non_bundle_files_are_skipped(tmp_path):
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    out = tmp_path / "out"
    _write(bundles, "practitioners.json", {"resourceType": "Patient", "id": "loose"})
    _write(bundles, "a.json", _bundle(_patient()))

    manifest = extract_corpus(bundles, out)

    assert len(manifest) == 1
    assert manifest[0]["source_bundle"] == "a.json"


def test_manifest_records_original_id(tmp_path):
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    out = tmp_path / "out"
    _write(bundles, "a.json", _bundle(_patient("original-uuid")))

    manifest = extract_corpus(bundles, out)

    assert manifest[0]["original_id"] == "original-uuid"
    assert manifest[0]["file"] == "Patient-001.json"
