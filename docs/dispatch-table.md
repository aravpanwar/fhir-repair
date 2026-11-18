# Dispatch Table

The dispatch table is a YAML file that maps each FHIR validation error code
to the strategy that should fix it. It lives in `repair-config.yaml` under
the `strategies:` key and is the primary user-facing extension point for the
project.

## Why YAML, not code

Strategy choices vary by deployer. A US Core deployment treats certain
errors very differently from an IPS deployment, and a hospital with strong
data quality will refuse cases that a research lab would happily LLM-fix.
Putting strategy choices in a config file means users change behavior
without forking the codebase.

## Format

```yaml
strategies:
  <error-code>: <strategy-identifier>
```

Each key is a HAPI validation error code (the `code` field on each
`OperationOutcome.issue`). Each value is a fully qualified strategy
identifier or one of the special tokens below.

## Strategy identifiers

| Identifier form | Meaning | Example |
|---|---|---|
| `deterministic.<name>` | A registered deterministic strategy | `deterministic.normalize_date` |
| `llm.<name>` | A registered LLM strategy with a specific prompt template | `llm.suggest_terminology_match` |
| `llm` | Generic LLM dispatch using the default prompt | `llm` |
| `refuse` | Mark the error unresolved without attempting a fix | `refuse` |

A strategy id that is not registered is rejected when the `Repairer` is
built, so a typo fails at once rather than partway through a repair.

## Default

A config without a `strategies` section uses this table:

```yaml
strategies:
  processing:
    - deterministic.normalize_date
    - deterministic.normalize_decimal
    - deterministic.unwrap_singleton
    - deterministic.normalize_telecom
    - deterministic.normalize_codeable_concept
    - deterministic.canonicalize_identifier_system
```

HAPI 7.4.0 emits the bare FHIR issue code `processing` for most value-level
problems, with the specifics in the diagnostics text, so the default routes
`processing` through a chain instead of mapping fine-grained codes. It holds
no LLM strategy. Setting `strategies` replaces the default rather than adding
to it.

## Example

```yaml
strategies:
  processing:
    - deterministic.normalize_date
    - deterministic.normalize_decimal
    - deterministic.unwrap_singleton
    - llm.suggest_terminology_match
  unknown-error: refuse
```

Check what your validator actually returns before adding an entry: a key
that never matches is silently dead.

## Removal-only strategies

Most strategies replace the value at the error path. `llm.resolve_invariant`
is different: it may only *remove* an element, and it names that element
itself. Invariants constrain several elements at once, so the repair is a
choice about which element to drop rather than a rewrite, and restricting the
strategy to removal means it cannot introduce clinical content that was not
in the input.

The element the model names is checked against the resource before anything
is deleted, and structural elements (`resourceType`, `id`, `meta`,
`implicitRules`) are refused outright.

HAPI reports an invariant failure against the resource, not the offending
field, so the error code is `processing` and the location is a bare resource
type. That means the strategy belongs at the end of the `processing` chain,
not under an `invariant-failed` key, and it should come last because it
removes data while the other strategies preserve it.

Because dropping submitted data is still a change to existing content, it
requires `allow_change_existing_clinical_value`, which is denied by default.
A removal is recorded in the audit log with `after` set to null.

## Resolution rules

When dispatching an error, the resolver checks in this order:

1. The exact error code (e.g., `processing`).
2. `unknown-error` as a catch-all.
3. If neither matches, no strategy runs, and the error is reported in
   `RepairResult.unresolved` and counted in the audit summary.

This means setting `unknown-error: llm` makes the LLM the default for any
error code you have not explicitly mapped, while `unknown-error: refuse`
makes the system fail closed.

## Versioning

The dispatch table format has its own version, recorded as `dispatch_version`
in the audit log. The current version is `1.0.0`.

A bump to a new major dispatch version means the config format changed in a
way that requires user migration. Migration steps for each version bump are
documented in [CHANGELOG-dispatch.md](CHANGELOG-dispatch.md).

The dispatch version is independent of the package version. Package v0.2 may
ship dispatch table v1.0 unchanged; package v1.0 may ship dispatch table v2.0
that requires a migration.

## Adding new error codes

When HAPI introduces a new validation code that is not covered by your
dispatch table, the resolver records the unmapped code in the audit log and
falls back to `unknown-error`. Watch the `dispatch-miss` count in audit
summaries, and add explicit mappings for codes you see frequently.

## Adding new strategies

A deterministic strategy is a module under
`fhir_repair/strategies/deterministic/` that exports `apply()` plus `NAME`,
`VERSION`, `PERMISSION` and `RISK` constants. It takes
`(resource, error)`, returns a `RepairAction`, does no IO, and refuses
cleanly (`risk="refused"` with a reason in `explanation`) when its
preconditions are not met. `date.py` is a worked example. Once a strategy is
registered, it can be referenced from a dispatch table entry by its `NAME`
constant.
