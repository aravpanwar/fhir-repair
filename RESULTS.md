# Benchmark results

Baseline results on Corpus A. Raw per-case output for this run is committed at
[benchmark/published/corpus-a-deterministic.json](benchmark/published/corpus-a-deterministic.json),
with the run history in
[benchmark/published/leaderboard.json](benchmark/published/leaderboard.json).

Those are snapshots kept under version control on purpose. A local
`benchmark.run` writes to `benchmark/results.json`, which is gitignored as
regenerable output, so re-running the benchmark never silently overwrites the
published numbers.

## Run provenance

| Item | Value |
|---|---|
| Corpus | [benchmark/corpus/synthea_full/](benchmark/corpus/synthea_full/), 100 Synthea v3.3.0 resources, seed 12345 |
| Cases | 326 mutated resources across all 8 mutation classes |
| Config | [examples/repair-config.yaml](examples/repair-config.yaml), deterministic strategies only |
| Validator | HAPI FHIR 7.4.0 |
| FHIR version | 4.0.1 |
| Dispatch version | 1.0.0 |
| LLM | none; no API key needed to reproduce this run |

The guard ran at its default permissions: `allow_reformat` and
`allow_bind_required_valueset` on, the three higher-risk permissions off.

## Deterministic-only baseline

| Metric | Result |
|---|---|
| Validator pass rate | **71.2%** (232/326) |
| Ground-truth match rate | **46.9%** (153/326) |
| Mean duration | 267 ms per resource |

Two metrics because they answer different questions. Validator pass means
HAPI accepts the repaired resource. Ground-truth match means the repair
reproduced the original pre-mutation value exactly. Passing the validator
without matching ground truth is not necessarily a bad repair, but it is not
a verified one either, so both are reported.

### Per mutation class

| Mutation class | n | Validator pass | Ground truth | Notes |
|---|---:|---:|---:|---|
| `date_format` | 20 | 1.00 | 1.00 | |
| `decimal_format` | 13 | 1.00 | 1.00 | |
| `identifier_system` | 20 | 1.00 | 1.00 | |
| `singleton_wrap` | 100 | 1.00 | 1.00 | |
| `telecom_format` | 20 | 1.00 | 0.00 | see below |
| `missing_required` | 100 | 0.59 | 0.00 | refuses by design |
| `invalid_code_binding` | 40 | 0.00 | 0.00 | needs an LLM strategy |
| `invariant_violation` | 13 | 0.00 | 0.00 | needs an LLM strategy |

## Reading the zeros

None of the three zero rows is a deterministic strategy failing at something
it claims to handle.

**`invalid_code_binding` and `invariant_violation` are not mapped in this
config.** Both are interpretive and route to LLM strategies
(`llm.suggest_terminology_match`, `llm.resolve_invariant`) that this run does
not enable. Every deterministic strategy in the chain refuses cleanly and the
error is reported unresolved, which is the intended fail-closed behaviour. A
run with an LLM provider configured is the comparison these rows exist for,
and is not yet published.

**`missing_required` refuses by design.** Inventing a value for a field that
was deleted requires `allow_add_missing_required_field`, which is off by
default because there is no signal in the input to recover the value from.
The 59% validator pass rate is the subset where the deleted field was not
actually required by base R4, so removing it left a valid resource; ground
truth is 0% because the original value is unrecoverable, not because a repair
went wrong.

**`telecom_format` passes the validator without repairing anything.** HAPI
7.4.0 reports no error for a `tel:` scheme prefix on `ContactPoint.value`, so
no error is dispatched and no strategy runs. The 1.00 validator column is the
mutated resource being accepted as-is, and the 0.00 ground-truth column is
honest about the value still carrying the prefix. This is a limitation of the
mutation, not of the repair: it produces a resource that is unusual but valid,
so it does not exercise anything. Worth either replacing with a corruption
HAPI rejects, or scoring against a profile that constrains the field.

## Reproducing

With Docker and Python installed:

```bash
docker compose -f docker/docker-compose.yml up -d hapi
# wait for http://localhost:8080/fhir/metadata to return 200

python -m benchmark.mutate benchmark/corpus/synthea_full benchmark/corpus/synthea_mutated
python -m benchmark.run \
  --corpus benchmark/corpus/synthea_mutated \
  --manifest benchmark/corpus/synthea_mutated/manifest.json \
  --config examples/repair-config.yaml \
  --out benchmark/results.json \
  --hapi-url http://localhost:8080/fhir
```

Regenerating the corpus itself needs Java 11+ and Synthea v3.3.0; see
[benchmark/corpus/SYNTHEA-GENERATION.md](benchmark/corpus/SYNTHEA-GENERATION.md).
The committed corpus makes that optional.

## What this run established

Four bugs surfaced only under a real corpus and are fixed in the numbers
above. Each was invisible against the 6-resource starter corpus:

1. **`identifier_system` produced no mutations at all.** The mutation only
   inspected `identifier[0]`; real Synthea patients carry the canonical
   `us-ssn` system at `identifier[2]`, behind Synthea's own generator id. The
   class silently contributed 0 cases.
2. **The identifier strategy refused every case.** HAPI reports the
   absolute-reference error on the `Identifier` element, not on
   `Identifier.system`, so the strategy received a dict and refused. 0% to
   100%.
3. **`value[x]` choice paths resolved to nothing.** HAPI emits
   `Observation.value[x].value` alongside the `ofType` form; only the latter
   was handled, so decimal fixes never applied. This also lifted
   `singleton_wrap` from 0.77 to 1.00.
4. **Decimals were written back as strings.** `"69,64"` became `"69.64"`, and
   HAPI still rejected it: FHIR `decimal` is a JSON number. 0% to 100%.

Overall the run moved from 54.0% / 29.8% to 71.2% / 46.9%.
