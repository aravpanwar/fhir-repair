# Benchmark results

Published runs on Corpus A. Raw per-case output and the cumulative
leaderboard are committed under
[benchmark/published/](benchmark/published/).

Those are snapshots kept under version control on purpose. A local
`benchmark.run` writes to `benchmark/results.json`, which is gitignored as
regenerable output, so re-running never silently overwrites published
numbers.

## Run provenance

| Item | Value |
|---|---|
| Corpus | [benchmark/corpus/synthea_full/](benchmark/corpus/synthea_full/), 100 Synthea v3.3.0 resources, seed 12345 |
| Cases | 326 mutated resources across all 8 mutation classes |
| Validator | HAPI FHIR 7.4.0 |
| FHIR version | 4.0.1 |
| Dispatch version | 1.0.0 |
| Guard | defaults, plus `allow_change_existing_clinical_value` for the LLM runs (the invariant strategy removes data) |

## Headline

| Run | Validator pass | Ground truth | Mean latency |
|---|---:|---:|---:|
| Deterministic only | 71.2% | 46.9% | 267 ms |
| + DeepSeek V4 Flash | **87.4%** | **62.6%** | 6.6 s |
| + DeepSeek V4 Pro | **87.4%** | **62.9%** | 10.2 s |

Restricted to the 243 cases the validator actually flagged:

| Run | Validator pass | Ground truth |
|---|---:|---:|
| DeepSeek V4 Flash | 83.1% | 82.3% |
| DeepSeek V4 Pro | 83.1% | 82.7% |

The two columns converging on the detected subset is the honest signal: on
cases that posed a real repair problem, a resource that passes the validator
is almost always the original resource restored, not a different resource
that happens to validate.

## Per mutation class

Validator pass / ground truth.

| Mutation class | n | Deterministic | Flash | Pro |
|---|---:|---:|---:|---:|
| `date_format` | 20 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| `decimal_format` | 13 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 0.92 |
| `identifier_system` | 20 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| `singleton_wrap` | 100 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| `invalid_code_binding` | 40 | 0.00 / 0.00 | 1.00 / 0.95 | 1.00 / 1.00 |
| `invariant_violation` | 13 | 0.00 / 0.00 | 1.00 / 1.00 | 1.00 / 1.00 |
| `missing_required` | 100 | 0.59 / 0.00 | 0.59 / 0.00 | 0.59 / 0.00 |
| `telecom_format` | 20 | 1.00 / 0.00 | 1.00 / 0.00 | 1.00 / 0.00 |

## What the LLM adds

Two classes are interpretive and no deterministic strategy can touch them.
Both go from nothing to essentially solved:

- **`invalid_code_binding`** (40 cases). An out-of-ValueSet abbreviation
  (`"F"` for `Observation.status`, `"M"` for `Patient.gender`) has no
  mechanical fix, but it is unambiguous to a reader.
  `llm.suggest_terminology_match` recovers the bound code.
- **`invariant_violation`** (13 cases). obs-6 forbids `dataAbsentReason`
  alongside a value. `llm.resolve_invariant` drops the redundant element and
  keeps the measurement, at 100% on both metrics.

## Flash or Pro

Flash. Pro costs 3x per token, took 55% longer per case (10.2 s against
6.6 s), and scored 0.3 points higher on ground truth, which is inside the
run-to-run noise described below. There is no measured reason to pay for Pro
on this workload.

Both runs used DeepSeek's default thinking mode (`high` effort), which bills
reasoning as output tokens. A full 326-case run cost a few cents.

## Non-determinism

Repair is not reproducible case-for-case even at `temperature: 0.0`. Flash
missed 2 of 40 `invalid_code_binding` cases; re-running one of them
immediately afterwards matched ground truth. Every case in that class took
the same number of actions, so this is variance in the model's output rather
than a different code path.

Read single-case differences between the two model runs as noise. The class
level is where the comparison means something.

## Reading the flat rows

Neither `missing_required` nor `telecom_format` is a strategy failing at
something it claims to do.

**`missing_required` refuses by design.** Inventing a value for a deleted
field needs `allow_add_missing_required_field`, off by default because
nothing in the input recovers the value. The 59% validator pass is the subset
where the deleted field was not required by base R4, so removing it left a
valid resource. Ground truth is 0% because the original value is
unrecoverable, not because a repair went wrong. An LLM does not change this
and should not: the guard is the point.

**`telecom_format` is not detected at all.** `ContactPoint.value` is a plain
FHIR string with no regex constraint, so `tel:555-0100` is structurally valid
and HAPI 7.4.0 reports no error. No error means no dispatch, so no strategy
runs, and the 1.00 validator column is the mutated resource being accepted
untouched.

Runs therefore record `validator_detected` per case, and the summary reports
`undetected_by_validator` plus a detected-only rate, so a class that
dispatches nothing cannot read as a perfect score. 83 of the 326 cases are
undetected: all 20 telecom, 59 of the `missing_required` deletions that base
R4 permits, and 4 `date_format` cases where the unpadded date still parsed.

The `deterministic.normalize_telecom` strategy itself is unaffected and
still useful. Legacy feeds really do emit `tel:`-prefixed numbers, and
stripping the prefix is a correct repair. It is a canonicalization rather
than a validation fix, so it fires when something does flag the field: a
profile constraining `ContactPoint.value`, or a stricter validator. What
changed here is the measurement, not the capability.

## Reproducing

```bash
docker compose -f docker/docker-compose.yml up -d hapi
# wait for http://localhost:8080/fhir/metadata to return 200

python -m benchmark.mutate benchmark/corpus/synthea_full benchmark/corpus/synthea_mutated

# deterministic only, no key needed
python -m benchmark.run \
  --corpus benchmark/corpus/synthea_mutated \
  --manifest benchmark/corpus/synthea_mutated/manifest.json \
  --config examples/repair-config.yaml \
  --out benchmark/results.json \
  --hapi-url http://localhost:8080/fhir
```

For an LLM run, route the interpretive classes to LLM strategies (append
`llm.suggest_terminology_match` and `llm.resolve_invariant` to the
`processing` chain, and enable
`allow_change_existing_clinical_value`), then:

```bash
export LLM_PROVIDER=deepseek
export LLM_MODEL=deepseek-v4-flash
export LLM_API_KEY=...           # BYOK; never committed
```

A full LLM run takes roughly 35 minutes for Flash and 55 for Pro against a
local HAPI.

## Bugs these runs found

The deterministic run surfaced four bugs that the 6-resource starter corpus
could not, all fixed before these numbers:

1. `identifier_system` produced no mutations at all: it inspected
   `identifier[0]`, and real Synthea patients carry the canonical `us-ssn`
   system at `identifier[2]`.
2. The identifier strategy refused every case, because HAPI reports the
   error on the `Identifier` element rather than `Identifier.system`.
3. `Observation.value[x].value` resolved to nothing, so decimal fixes never
   applied. Fixing it also lifted `singleton_wrap` from 0.77 to 1.00.
4. Decimals were written back as strings, which FHIR rejects: `decimal` is a
   JSON number.

The LLM run surfaced three more:

5. An `invariant-failed` dispatch key never matched anything. HAPI reports
   invariant failures as `processing`, so the entry documented in the
   dispatch table was dead.
6. The invariant strategy asked whether to remove the flagged element, but
   HAPI flags the resource, so it was effectively asking whether to delete
   the whole Observation. The model declined every time, correctly. It now
   names the element, checked against the resource and a protected list.
7. An error location that is a bare resource type crashed the generic LLM
   runner through `set_at_path`, aborting a whole benchmark run partway
   through. It refuses now.
