# Audit Log Schema (v1)

`fhir-repair` writes a JSON Lines audit log for every repair run. One line
per `RepairAction`, plus a final summary line per resource. The schema below
is sealed at v1 and changes only in additive ways. Breaking changes bump the
`v` field.

## Format

JSON Lines (`.jsonl`): one JSON object per line, no array wrapper.

Files are typically named `<resource_type>-<resource_id>-<timestamp>.audit.jsonl`,
written to the directory configured under `logging.audit_destination`.

## Action entry

Each repair action produces one line:

```json
{
  "v": 1,
  "ts": "2026-04-27T14:03:11Z",
  "resource_id": "Patient/abc",
  "resource_type": "Patient",
  "fhir_version": "4.0.1",
  "dispatch_version": "1.0.0",
  "action": {
    "strategy": "deterministic.normalize_date",
    "strategy_version": "1.0.0",
    "risk": "low",
    "permission_used": "allow_reformat",
    "error": {
      "code": "invalid-date-format",
      "severity": "error",
      "location": "Patient.birthDate",
      "message": "..."
    },
    "before": "1990-3-5",
    "after": "1990-03-05",
    "explanation": "Reformatted '1990-3-5' to ISO 8601 '1990-03-05'."
  },
  "llm": null
}
```

### Top-level fields

| Field | Type | Required | Description |
|---|---|---|---|
| `v` | integer | yes | Schema version. Always `1` in this document. |
| `ts` | string (ISO 8601) | yes | UTC timestamp of when the action was applied. |
| `resource_id` | string | yes | Resource type plus id, e.g. `Patient/abc`. May be `Patient/<unknown>` if the input had no id. |
| `resource_type` | string | yes | FHIR resource type. |
| `fhir_version` | string | yes | FHIR version the validator was configured for. |
| `dispatch_version` | string | yes | Version of the dispatch table format. |
| `action` | object | one of action/summary | The applied repair, see below. |
| `summary` | object | one of action/summary | The resource-level summary, see below. |
| `llm` | object or null | yes | LLM call details if `action.strategy` is an LLM strategy, else `null`. |

### `action` object

| Field | Type | Description |
|---|---|---|
| `strategy` | string | Fully qualified strategy identifier, e.g. `deterministic.normalize_date`. |
| `strategy_version` | string | SemVer of the strategy implementation. |
| `risk` | string | One of `low`, `medium`, `high`, `refused`. |
| `permission_used` | string | Hallucination guard permission this action exercised. |
| `error` | object | The validation error this action targeted (see below). |
| `before` | any | Value at the error location before the fix. May be `null` for missing-element fixes. |
| `after` | any | Value at the error location after the fix. May equal `before` for refused actions. |
| `explanation` | string | Human-readable description of what changed and why. |

### `action.error` object

| Field | Type | Description |
|---|---|---|
| `code` | string | HAPI error code, e.g. `invalid-date-format`. |
| `severity` | string | One of `error`, `warning`, `information`. |
| `location` | string | FHIRPath expression locating the error. |
| `message` | string | Diagnostic message from the validator. |

### `llm` object (when present)

```json
{
  "provider": "anthropic",
  "model": "claude-sonnet-4-6",
  "prompt_version": "v1",
  "prompt_hash": "sha256:0a9f...",
  "input_tokens": 412,
  "output_tokens": 38,
  "cached_tokens": 380,
  "latency_ms": 612
}
```

| Field | Type | Description |
|---|---|---|
| `provider` | string | Provider adapter name. |
| `model` | string | Model identifier as seen by the adapter. |
| `prompt_version` | string | Version of the prompt template used. |
| `prompt_hash` | string | `sha256:<hex>` hash of the rendered prompt for reproducibility. |
| `input_tokens` | integer | Total input tokens billed. |
| `output_tokens` | integer | Output tokens generated. |
| `cached_tokens` | integer | Subset of input tokens served from cache. |
| `latency_ms` | integer | Wall-clock latency of the provider call. |

## Summary entry

After all actions on a resource are written, a single summary line closes
the run:

```json
{
  "v": 1,
  "ts": "2026-04-27T14:03:13Z",
  "resource_id": "Patient/abc",
  "summary": {
    "total_errors": 4,
    "fixed": 3,
    "unresolved": 1,
    "duration_ms": 1840
  }
}
```

| Field | Type | Description |
|---|---|---|
| `total_errors` | integer | Number of validation errors on the input. |
| `fixed` | integer | Errors resolved by repair actions. |
| `unresolved` | integer | Errors left after all attempts. |
| `duration_ms` | integer | Total wall-clock duration of the repair. |

## Validating an audit log

The Pydantic model `fhir_repair.core.audit.AuditEntry` validates a single
line. To validate an entire file:

```python
import json
from fhir_repair.core.audit import AuditEntry

with open("Patient-abc-20260427.audit.jsonl") as fh:
    for line in fh:
        entry = AuditEntry.model_validate_json(line)
        # do something with entry
```

## Backwards compatibility

- New fields may be added in a v1-compatible release. Consumers must ignore
  unknown fields rather than rejecting them.
- Existing fields will not be removed or have their semantics changed within
  v1. A breaking change bumps `v`.
- Enum values for `risk` and `severity` may grow but will not lose existing
  values.
