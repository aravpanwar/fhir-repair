# Wild Sample Screening Procedure

The wild-sample corpus contains real broken FHIR drawn from public sources.
Every sample committed to this repository has been screened for protected
health information (PHI) by the procedure described here. Samples that fail
screening are excluded; the exclusion is logged but the content is never
committed.

## Sources

Samples are drawn from these public sources only:

1. **hapi.fhir.org public test server**: scratchpad uploads that fail
   validation. Available via the server's REST API. We collect samples
   from the public namespace only; we do not query private partitions.
2. **Inferno public test session logs**: failure traces published by the
   ONC Health IT Inferno project on `github.com/onc-healthit`. These are
   redacted before publication, but we re-screen anyway.
3. **Synthea + noisy ETL**: Synthea-generated (synthetic) bundles routed
   through a custom CSV-to-FHIR adapter that introduces realistic-looking
   conversion errors. Origin is fully synthetic; PHI risk is zero, but
   we screen for completeness.

We do not accept samples from any other source without an entry in this
file.

## Exclusion criteria

A sample is excluded if it contains, or appears to contain, any of:

- A name in formats consistent with real personal names: any combination
  of given + family that does not appear in the Synthea name corpus.
- A date of birth more recent than the year the sample was collected
  minus 130. (Synthea uses a controlled date range; outside that range
  is suspicious.)
- An identifier value that matches a known real identifier system format
  (e.g., a US SSN, a real-looking MRN format).
- A free-text note longer than 50 characters that mentions a person,
  place, or condition in narrative form.
- A geographic identifier (postal code, latitude, longitude) outside the
  Synthea generation range.
- An email address with a known real domain (gmail, outlook, etc.).
- A phone number not in the `555-` test prefix.

When in doubt, exclude.

## Two-reviewer sign-off

Every committed sample is reviewed by two people, recorded in the
`reviewers` field of the manifest entry. Reviewers must:

1. Skim the full resource content.
2. Spot-check any free-text fields against the exclusion criteria.
3. Confirm the source URL still resolves and the original was public at
   collection time (so the project did not become the publisher of
   non-public data).

If the two reviewers disagree, the sample is excluded.

## Exclusion log

Excluded samples are recorded in `excluded.json` with:

- Source URL
- Brief reason for exclusion (no quoted content)
- Date of decision
- Reviewer identifiers

The excluded content itself is not committed.

## Manifest format

`wild_sample/manifest.json` records every accepted sample:

```json
[
  {
    "id": "wild-0001",
    "source": "hapi.fhir.org public test server",
    "source_url": "https://hapi.fhir.org/baseR4/Patient/<id>",
    "collected_at": "2026-04-15",
    "reviewers": ["reviewer-a", "reviewer-b"],
    "error_classes": ["invalid-date-format", "invalid-code-binding"],
    "file": "wild-0001.json"
  }
]
```

## Reporting suspected PHI

If you find a wild-sample resource that you believe contains PHI, do not
quote it in the issue. Open a GitHub issue tagged `phi-report` with only
the sample id, and a maintainer will investigate and rewrite history if
needed.

## Status

The wild-sample corpus is empty in v0.1. Source-of-truth procedure for
screening (this document) ships first; the corpus itself is populated in
v0.2 once the procedure has been reviewed.
