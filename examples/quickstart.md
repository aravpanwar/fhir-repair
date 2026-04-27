# Quickstart

This walkthrough goes from a fresh checkout to a fixed FHIR resource.

## 1. Install

```bash
git clone https://github.com/aravpanwar/fhir-repair.git
cd fhir-repair
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev,anthropic]"
```

## 2. Start a HAPI validator

The library does not embed a FHIR validator; it talks to one over REST.
The simplest setup is a local HAPI server:

```bash
docker compose -f docker/docker-compose.yml up -d hapi
```

Wait until the health check passes:

```bash
curl -f http://localhost:8080/fhir/metadata
```

## 3. Try it on a sample

The repository ships a deliberately broken Patient fixture:

```bash
cat tests/fixtures/patient_invalid_date.json
```

That resource has `birthDate: "1990-3-5"`, which is not valid ISO 8601.
HAPI will reject it; our deterministic strategy can fix it.

Run the CLI:

```bash
fhir-repair fix tests/fixtures/patient_invalid_date.json --out /tmp/patient_fixed.json
```

The output:

```bash
cat /tmp/patient_fixed.json
```

Should now contain `"birthDate": "1990-03-05"`.

## 4. Inspect the audit log

Every repair writes a JSON Lines audit log to `./audit-logs/` by default:

```bash
ls audit-logs/
cat audit-logs/Patient-example-invalid-date-*.audit.jsonl | python -m json.tool
```

You should see:

- One `action` entry recording the date-format fix
- One `summary` entry with totals

## 5. Use the library directly

```python
import json

from fhir_repair import Repairer
from fhir_repair.validators.hapi import HapiRestValidator

with open("tests/fixtures/patient_invalid_date.json") as fh:
    broken = json.load(fh)

validator = HapiRestValidator()
try:
    repairer = Repairer(validator=validator)
    result = repairer.repair(broken)
finally:
    validator.close()

print("Fixed:", result.fixed_resource["birthDate"])
print("Actions:", [a.strategy for a in result.audit])
print("Unresolved:", [(e.code, e.location) for e in result.unresolved])
```

## 6. Add the LLM (optional)

For errors deterministic strategies cannot fix, configure an LLM. Set
your API key:

```bash
export LLM_API_KEY=sk-ant-...
export LLM_PROVIDER=anthropic
export LLM_MODEL=claude-sonnet-4-6
```

Pass a config that maps interpretive errors to LLM strategies:

```bash
cp examples/repair-config.yaml my-config.yaml
# Edit my-config.yaml: change `unknown-error: refuse` to `unknown-error: llm`.
fhir-repair fix tests/fixtures/patient_invalid_date.json --config my-config.yaml
```

The audit log now includes `llm` provenance on actions that came from an
LLM call: provider, model, prompt version, prompt hash, token counts,
cache hits, latency.

## 7. Run the benchmark

The benchmark harness mutates a corpus of valid resources and measures
how well the tool repairs them.

```bash
# Generate the mutated corpus from the (committed) valid corpus
python -m benchmark.mutate \
    benchmark/corpus/synthea_valid \
    benchmark/corpus/synthea_mutated

# Run the benchmark
python -m benchmark.run \
    --corpus benchmark/corpus/synthea_mutated \
    --manifest benchmark/corpus/synthea_mutated/manifest.json \
    --config examples/repair-config.yaml \
    --out benchmark/results.json
```

Open `benchmark/leaderboard.html` in a browser to see the results.

## 8. What to do when something is unresolved

A non-zero `unresolved` count is normal. Common reasons:

- The error mapped to `refuse` in your dispatch table (the safe default
  for missing-required errors).
- A strategy detected an ambiguous input and refused rather than guess.
- The hallucination guard denied the permission a strategy needed.

Each unresolved error is recorded in the audit log with the reason. To
allow more aggressive repair, edit `repair-config.yaml`:

- Map more error codes to LLM strategies.
- Grant additional `hallucination_guard` permissions (read the risk
  notes in `docs/audit-log-schema.md` first).
- Set `unknown-error: llm` to use the LLM as a catch-all.

Each of these increases the chance of an automatic fix and the chance of
an undesirable change. The audit log is the receipt that lets you check
which actually happened.
