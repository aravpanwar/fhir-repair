# Spec Indexes

This directory ships preprocessed indexes derived from the FHIR R4
specification:

- `r4_codesystems.json`: small, FHIR-internal CodeSystems keyed by URL.
  Used by the `LocalTerminology` adapter for offline code validation.
- `r4_index.json` (planned): retrieval index for LLM strategies, keyed by
  FHIRPath. Returns the relevant element definition, ValueSet binding, and
  invariants for a given path. Not shipped in v0.1.
- `us_core_index.json` (planned): same shape as `r4_index.json`, scoped to
  the US Core profile. Not shipped in v0.1.

## Regenerating

The indexes are built from the official FHIR R4 definitions bundle, which
is downloadable as a zip from `hl7.org/fhir/R4/definitions.json.zip`. The
preprocessing script lives in `tools/build_spec_index.py` (planned for
v0.2). Until then, the shipped `r4_codesystems.json` is a hand-curated
extract of the most commonly needed enumerations.

To rebuild manually:

1. Download `definitions.json.zip` from the [FHIR R4 download page](https://hl7.org/fhir/R4/downloads.html).
2. Extract to a working directory.
3. Run `python tools/build_spec_index.py <path-to-definitions>` (when implemented).
4. Commit the resulting JSON files.

The output is committed to the repository so users do not need to fetch
the spec at install time.

## Coverage in v0.1

`r4_codesystems.json` covers:

- `http://hl7.org/fhir/administrative-gender`
- `http://hl7.org/fhir/contact-point-system`
- `http://hl7.org/fhir/contact-point-use`
- `http://hl7.org/fhir/address-use`
- `http://hl7.org/fhir/address-type`
- `http://hl7.org/fhir/identifier-use`
- `http://hl7.org/fhir/name-use`

This is enough for the most common binding errors on `Patient`,
`Practitioner`, and similar administrative resources. Full coverage
requires either the full preprocessed index or a real terminology server.
