# Architecture

This document describes how `fhir-repair` is put together: the data flow, the
component boundaries, the multi-error handling protocol, and the reasoning
behind the major design decisions.

## Data flow

```
Input resource
    |
    v
1. Parse + version detect
    |
    v
2. Validate (HAPI adapter)  ->  list[ValidationError]
    |
    +-- valid? -- yes --> return as-is
    |
    no
    |
    v
3. Dispatch each error via strategy table
   Sort errors leaf-first (deepest FHIRPath first)
    |
    +--------------------+
    v                    v
deterministic       llm queue
fixes (pure          (batched)
fns, depth-batched)
    |                    |
    v                    v
4. Re-validate
   Detect regressions, roll back offending batches
    |
    +-- valid? -- yes --> return + audit
    |
    no, attempts < max, not stuck
    |
    v
5. Send to LLM with:
   - resource
   - remaining errors only
   - retrieved spec excerpt
   - hallucination guard permissions
   - PromptSegment[stable] hints
    |
    v
6. Re-validate, loop until valid OR budget exhausted OR stuck
    |
    v
Output:
   - fixed_resource
   - audit log (JSONL)
   - unresolved list
   - duration_ms
```

## Components

| Layer | Module | Responsibility | Pluggable |
|---|---|---|---|
| Core repairer | `fhir_repair.core.repairer` | Orchestrates the loop, manages budget, writes audit log | No |
| Dispatcher | `fhir_repair.core.dispatcher` | Strategy lookup, leaf-first ordering, regression rollback | No |
| FHIRPath utility | `fhir_repair.core.fhirpath` | Wraps `fhirpathpy` for evaluation and assignment | No |
| Hallucination guard | `fhir_repair.core.guard` | Five independent permissions checked before each strategy runs | No |
| Audit writer | `fhir_repair.core.audit` | JSON Lines output with sealed v1 schema | Output destination configurable |
| Validator adapter | `fhir_repair.validators.hapi` | Wraps HAPI `$validate`, normalizes errors | Yes (Firely, Inferno later) |
| Strategy base | `fhir_repair.strategies.base` | Strategy Protocol; declares required permission | No |
| Deterministic strategies | `fhir_repair.strategies.deterministic.*` | Pure functions, one per fix pattern | Yes (community PRs) |
| LLM strategies | `fhir_repair.strategies.llm.*` | Runner, RAG, prompt templates | Yes |
| LLM provider | `fhir_repair.llm.*` | Provider adapters (Anthropic, OpenAI, etc.) | Yes (BYO via env) |
| Terminology service | `fhir_repair.terminology.*` | Bound-set lookups | Yes (BYO endpoint) |
| Surfaces | `fhir_repair.cli.main` | CLI entry point | Library + CLI in v0.1, FastAPI later |

## Multi-error interaction protocol

When a resource has multiple errors, naive parallel application produces wrong
results: fixing a parent path can undo a fix at a child path, and two fixes
at the same path can conflict. The dispatcher follows a fixed protocol:

### 1. Leaf-first ordering

Errors are sorted by FHIRPath depth, deepest first. Fixing
`Patient.contact[0].telecom[0].value` runs before any fix to
`Patient.contact`. At equal depth, deterministic strategies run before LLM
strategies.

Rationale: parent-level fixes (replacing an entire `Patient.contact` element)
will overwrite any child-level fix that happened first, wasting work. Going
leaf-first means each fix is durable.

### 2. Depth-batched application

All same-depth deterministic fixes are applied in one batch, then we
re-validate, then we proceed to the next depth. Pure-function strategies
have no IO, so a batch is conceptually parallel even though we run it in
declared order to make audit logs deterministic.

### 3. Scope conflict serialization

Each `RepairAction` declares a path scope (the set of FHIRPaths it touches).
Actions with overlapping scope in the same batch are serialized in declared
order rather than parallelized. In practice, two strategies rarely target
the same path, but this rule prevents subtle bugs where they do.

### 4. Regression rollback

After re-validation, the dispatcher diffs the error list against the prior
list. If the new error list contains errors that:

- did not exist before the batch ran, and
- are at paths that a just-applied action touched

then the batch is rolled back, and each action retries sequentially with
re-validation between actions. An action that still produces a regression on
sequential retry is dropped and recorded with `risk: "refused"` and a
description of the regression in `explanation`.

### 5. Termination

Two budgets bound the loop:

- `max_attempts` (default 5): caps the number of validate-fix-revalidate
  iterations.
- Stuck-detector: if two consecutive iterations produce the exact same error
  set, halt and return whatever is in `unresolved`.

This avoids infinite loops when a strategy "succeeds" but produces output
that re-triggers the same error.

## Hallucination guard

LLM-introduced changes are not one risk class. The guard splits them into
five independent permissions:

| Permission | Default | What it allows | Risk |
|---|---|---|---|
| `allow_reformat` | true | Rewrite an existing value to a valid wire format | Low |
| `allow_bind_required_valueset` | true | Pick a code from a `required`-strength bound ValueSet | Medium |
| `allow_bind_extensible_valueset` | false | Pick a code from `extensible` or `preferred` strength | Medium-high |
| `allow_add_missing_required_field` | false | Invent a value for a required field that was absent | High |
| `allow_change_existing_clinical_value` | false | Replace a clinical value the user provided | High |

Strategies declare which permission they need via a `PERMISSION` constant.
The dispatcher checks the permission before invoking the strategy. If the
permission is denied, the strategy is skipped and the error remains in the
unresolved list, recorded as `risk: "refused"` with the permission name in
`explanation`.

This makes "what level of LLM autonomy is this run operating at" a
configuration question with five orthogonal dials, rather than one ambiguous
boolean.

### Known limitation: the guard gates invocation, not output

The guard authorizes which *class* of change a strategy may attempt before
it runs. It does not yet verify that the LLM's returned value actually
stayed within that class. `llm.suggest_terminology_match` holds
`allow_bind_required_valueset`, but nothing checks that the code it returns
is a member of the bound ValueSet, and the generic `llm` strategy can return
any replacement value while holding only the bind permission. A wrong-but-
well-formed value (a plausible code that is not the right one, or a value
outside the bound set) is written and re-validated, but not rejected on
membership grounds.

Two things bound the blast radius today: the repair loop re-validates after
every LLM fix and rolls back anything that introduces a validator error, and
every LLM action is written to the audit log with its before/after values
and full provenance, so no change is silent. What is missing is output-side
enforcement of the permission scope. Closing it requires a terminology
service to check ValueSet membership (the `terminology` config exists for
this and currently defaults to `none`); until then, treat LLM-strategy
output as recorded-but-unverified and keep the guard permissions
conservative for runs on data you cannot review.

## Caching as a provider capability

The Anthropic API offers an explicit `cache_control: {type: "ephemeral"}`
marker that lets you cache a stable prompt prefix across calls (~10% input
cost on cache hit). Other providers handle caching differently: OpenAI does
automatic prefix caching with no manual markers, AWS Bedrock has its own
scheme, and on-prem vLLM caches transparently.

To keep the core LLM runner provider-agnostic, prompts are passed as a list
of `PromptSegment` objects:

```python
@dataclass
class PromptSegment:
    role: Literal["system", "user", "assistant"]
    text: str
    stable: bool = False
```

The runner sets `stable=True` on segments that repeat across calls (the
system prompt, the retrieved FHIR spec excerpt) and `stable=False` on
segments that vary per request (the broken resource, the error list). Each
provider adapter maps the hint to its native primitive:

- Anthropic adapter: emits `cache_control` markers on `stable=True` blocks.
- OpenAI adapter: ignores the hint (automatic prefix caching handles it).
- Bedrock adapter: emits Bedrock's caching markers where supported.
- On-prem adapters: ignore the hint (vLLM caches transparently).

No vendor-specific syntax appears in the core runner. Switching providers
is a configuration change.

## FHIRPath handling

Paths returned by HAPI's `$validate` are FHIRPath expressions. We use
`fhirpathpy` to evaluate them, wrapped in `fhir_repair.core.fhirpath` so the
rest of the codebase imports a single internal surface.

For assignment back to the resource (which FHIRPath does not natively
support), we parse simple dotted-and-indexed paths (`Patient.contact[0].
telecom[0].value`) directly. General FHIRPath assignment is undefined, so we
limit ourselves to the syntactic subset HAPI actually emits as error
locations.

If `fhirpathpy` proves insufficient for a particular invariant evaluation,
the fallback is to round-trip through HAPI's FHIRPath via the validator's
`$evaluate` operation (a future extension; not implemented in v0.1).

## Reproducibility

Every run records the full version provenance in `RepairResult.metadata`:

- FHIR version
- HAPI validator version
- LLM model identifier
- Prompt version
- Dispatch table version

Six months from now, someone can re-run a benchmark with the same versions
and get the same numbers. Without this, the benchmark is not reproducible
and the empirical study is not citable.

## Statelessness and no telemetry

The library has no shared state, no database, no session. Every `repair()`
call is self-contained.

The library makes no outbound network calls except to the validator, the
LLM, and the terminology server you configure. There is no analytics, no
phone-home, no version check. This is what makes the legal posture work:
"deployable in HIPAA-compliant environments per deployer's configuration"
is only true if there are no surprise endpoints.

See [DEPLOYMENT-COMPLIANCE.md](../DEPLOYMENT-COMPLIANCE.md) for how to
verify this in your environment.
