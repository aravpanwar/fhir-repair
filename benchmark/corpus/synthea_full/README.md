# Corpus A: full Synthea corpus

100 resources extracted from a Synthea v3.3.0 run: 20 each of `Patient`,
`Observation`, `Condition`, `MedicationRequest`, and `Encounter`. This is the
corpus the benchmark and leaderboard use. The 6 hand-curated resources in
[../synthea_valid/](../synthea_valid/) remain the no-Java smoke fixture.

## Provenance

| Item | Value |
|---|---|
| Synthea version | v3.3.0 |
| Seed | 12345 |
| Population | 100 (`-p 100`), 113 bundles including deceased records |
| US Core IG | off (`--exporter.fhir.use_us_core_ig false`) |
| Years of history | 10 |
| Validated against | HAPI FHIR 7.4.0, 100/100 with zero errors |

Regenerate with the commands in
[../SYNTHEA-GENERATION.md](../SYNTHEA-GENERATION.md). Generation needs Java 11
or newer. `synthea_full-extraction.json` records the source bundle and
original id for every file.

## Why these transformations

Extraction strips `meta` and the generated `text` narrative, and rewrites
`urn:uuid:` references to `<Type>/<id>`. The reference rewriting is not
cosmetic: a `urn:uuid:` reference only resolves inside its own bundle, so
without it every extracted resource fails validation on dangling references
before any mutation is applied, and the benchmark would measure that instead
of repair quality.

## Content

Fully synthetic and CC0, per Synthea's licensing. The data is synthetic by
construction, not by scrubbing: Synthea appends digits to every name
(`Claudia969 Galvan169`), SSNs use the reserved `999-` block that is never
issued, and phone numbers use the `555-` test prefix. No PHI.

## Mutation and benchmark

```bash
python -m benchmark.mutate benchmark/corpus/synthea_full benchmark/corpus/synthea_mutated
python -m benchmark.run \
  --corpus benchmark/corpus/synthea_mutated \
  --manifest benchmark/corpus/synthea_mutated/manifest.json \
  --config examples/repair-config.yaml \
  --out benchmark/results.json \
  --leaderboard benchmark/leaderboard.json \
  --label "deterministic only"
```

Mutating this corpus yields 326 pairs across all 8 mutation classes. See
[RESULTS.md](../../../RESULTS.md) for the published baseline.
