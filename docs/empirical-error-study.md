# Empirical Error Study (planned)

> Status: planned. The study is the citable artifact of this project. v0.1
> ships only the harness needed to produce it. Methodology and results
> below are the design; numbers are TBD.

## Question

Where do FHIR validation errors actually fall on the deterministic /
interpretive / inventive spectrum, and how does the distribution differ
between synthetic mutation corpora and real-world failure samples?

## Why this matters

Existing FHIR repair work assumes errors are uniformly distributed across
classes, or measures only on synthetic corpora that the authors generated
themselves. There is no public characterization of real-world FHIR
failures, no canonical methodology for benchmarking repair tools, and no
empirical evidence that synthetic mutations are a good proxy for what
breaks in production.

If this study finds that synthetic mutations approximate real distributions
well, it validates a generation of FHIR research that has relied on them.
If it finds the distributions are different, it shows the field has been
measuring the wrong thing, and proposes a corrected approach.

## Two corpora

### Corpus A: Synthea Mutations (controlled, full ground truth)

- 100 valid Synthea-generated resources, sampled across 5 resource types
  (Patient, Observation, Condition, MedicationRequest, Encounter), 20 each.
- 8 mutation classes, each implemented as a deterministic function in
  `benchmark/mutate.py`:
  1. Date format corruption
  2. Decimal format corruption
  3. Singleton wrapped in array
  4. Missing required element
  5. Invalid code binding
  6. Invariant violation
  7. Telecom format corruption
  8. Identifier system mismatch
- 100 x 8 = 800 broken cases. Ground truth is the pre-mutation resource.
- Used to: drive the leaderboard, score model-vs-prompt comparisons,
  measure semantic preservation and hallucination rates.

### Corpus B: Wild Sample (uncontrolled, partial ground truth)

- Real broken FHIR drawn from public sources where validation fails:
  hapi.fhir.org public test server scratchpads, Inferno public test session
  failure logs from `github.com/onc-healthit`, Synthea routed through a
  noisy CSV-to-FHIR adapter to simulate legacy ETL behaviour.
- No ground truth. Each sample is categorized by error class.
- Manual PHI screening before any sample is committed (procedure in
  [`benchmark/corpus/wild_sample.SCREENING.md`](../benchmark/corpus/wild_sample.SCREENING.md)).
- Used to: characterize real-world error distribution, never to score
  repair quality (without ground truth, "did we fix it" is meaningless).

## Methodology

### For each corpus

1. Run HAPI `$validate` against every input resource.
2. Group errors by `code`.
3. For each error, classify into one of three buckets by hand or by rule:
   - **Deterministic**: a pure function with no clinical knowledge can fix
     it (date padding, singleton unwrap).
   - **Interpretive**: choosing among a closed set of valid options
     requires reading clinical context (mapping `"M"` to `"male"` in a
     bound ValueSet).
   - **Inventive**: producing a fix would require inventing clinical data
     (filling a missing `Condition.code` with no source signal).
4. Report the distribution per resource type, per error code, and overall.

### Comparison

Side by side: distribution of buckets in Corpus A vs Corpus B. Statistical
test (chi-squared on bucket counts, with sample size caveats noted) for
whether the distributions differ.

## Reporting

The study is published as a paper (target: JAMIA Open or JMIR Medical
Informatics) and reproduced as a section in this repo. Reproducibility
requires:

- Pinned Synthea version and seed (documented in
  [`benchmark/corpus/SYNTHEA-GENERATION.md`](../benchmark/corpus/SYNTHEA-GENERATION.md))
- Pinned HAPI validator version
- Pinned dispatch table version
- All classification labels published as supplementary data

## Threats to validity

- Wild sample size is small. Real broken FHIR with public availability and
  PHI-clean status is rare; the wild corpus will likely be 50 to 200 cases,
  which limits statistical power.
- Wild sample is biased toward sources we can access. Hospital production
  data is the population we ultimately care about; public test servers are
  a convenience sample.
- Bucket classification has interpretive judgement. Inter-rater agreement
  measured on a sub-sample, kappa reported.
- Synthea outputs are valid by construction. Mutations exercise specific
  failure modes but cannot test errors Synthea cannot produce (e.g.,
  malformed extensions, Synthea has no extensions).

## Status

- [ ] Corpus A generated
- [ ] Corpus B sourced and screened
- [ ] Classification rubric drafted
- [ ] Inter-rater agreement measured
- [ ] Distributions reported
- [ ] Paper drafted
- [ ] Submitted

Track progress via the `study` label in GitHub Issues.
