# Starter Corpus

The six JSON files in this directory are a **hand-curated starter corpus**:
two Patient resources, two Observation resources, two Condition resources,
each constructed to pass HAPI R4 validation with no warnings.

The starter corpus exists so contributors can run the benchmark end-to-end
on a small fixture without first installing Java and Synthea. It is
**not** the corpus the empirical study uses.

## Status

| Item | Where it lives |
|---|---|
| Starter corpus (this directory) | committed to git, 6 resources, used for smoke-testing the harness |
| Full Synthea-generated corpus (planned for v0.2) | regenerated locally per `SYNTHEA-GENERATION.md`, target ~100 resources, used for the leaderboard |
| Wild sample corpus (planned for v0.2) | per `wild_sample.SCREENING.md`, used for the empirical study only |

## Why hand-curated and not Synthea

Generating real Synthea output requires Java, Gradle, and a 5-10 minute
build. Most contributors will only ever want to run the harness once to
confirm it works, and the starter corpus is enough for that. When the
empirical study moves into focus, regenerate the full corpus per
`SYNTHEA-GENERATION.md` and replace these files.

## How to verify

The starter corpus must pass HAPI validation. With a HAPI server running:

```bash
docker compose -f docker/docker-compose.yml up -d hapi

# Validate every resource in this directory
for f in benchmark/corpus/synthea_valid/*.json; do
  fhir-repair validate "$f" || echo "FAILED: $f"
done
```

## What is allowed in this directory

- Synthetic, fully fictional resources.
- Phone numbers in the `555-0100` to `555-0199` range (the test prefix).
- Emails on the `example.test` TLD.
- LOINC and SNOMED codes only where they are well-known examples.

What is **not** allowed: anything that could plausibly be a real person,
real location, or real identifier.
