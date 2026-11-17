# Dispatch Table Format Changelog

This file tracks changes to the dispatch table format (the YAML schema under
the `strategies:` key in `repair-config.yaml`). The dispatch format is
versioned independently of the `fhir-repair` package and recorded as
`dispatch_version` in every audit log entry.

A new major version means existing config files require migration. New minor
versions are backwards compatible: configs using the prior minor version
load unchanged.

## v1.0.0 (current)

Initial release.

### Format

```yaml
strategies:
  <error-code>: <strategy-identifier>
```

### Resolution

Strategies resolve in this order:
1. Exact `error-code` match
2. `unknown-error` catch-all
3. Recorded as unresolved with `dispatch-miss`

### Strategy identifiers

| Form | Meaning |
|---|---|
| `deterministic.<name>` | Registered deterministic strategy |
| `llm.<name>` | Registered LLM strategy with named prompt |
| `llm` | Generic LLM dispatch with default prompt |
| `refuse` | Skip without attempting |
| `human` | Reserved for future use |

### Migration from prior versions

Not applicable; this is the first version.

## Strategy versions

Each strategy records its own `VERSION` in every audit entry
(`strategy_version`). It changes when the strategy's output changes in a
way a benchmark consumer would want to see.

| Strategy | Version | Change |
|---|---|---|
| `deterministic.normalize_date` | 1.1.0 | Refuses a day the calendar does not have, and a value already in ISO 8601 form instead of reporting it as a fix. |
| `deterministic.normalize_decimal` | 1.1.0 | Refuses a value with more significant digits than a JSON number carries exactly, instead of writing back a rounded value. |
