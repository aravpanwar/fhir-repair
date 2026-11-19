# Changelog

## Unreleased

### Added

- `llm.options`, passed to every provider call: `max_tokens` and
  `temperature` override the adapter defaults (a null `temperature` sends
  none), and other keys go to the vendor as given, such as DeepSeek's
  `reasoning_effort`.
- Benchmark runs record LLM calls and tokens, the model id the server
  reported, fields changed that the mutation had left correct, and whether a
  value was written where a field was deleted. `--sample` runs a
  class-stratified pilot.
- Benchmark arm configs under `benchmark/configs/` (deterministic, hybrid,
  LLM only, and the published 0.2.0 table) and `benchmark.report`, which
  turns run files into tables, with cost from `benchmark/prices.json`.

### Fixed

- LLM refusals that followed a successful call dropped the call's tokens
  from the audit log, understating cost.
- DeepSeek cache hits were recorded as zero.
- An error that an earlier fix had already cleared was still dispatched,
  so the chain ran on a correct value and, with an LLM at its end, paid
  for a call to rewrite it.
- Rollback after a regression left the refused fix in the output, replayed
  an identifier system fix over the whole Identifier, and raised when
  replaying the removal of an element reported against the resource.
- An error no strategy could fix ran its strategy chain twice, so an LLM
  strategy made a second paid call for nothing.
- Errors cleared by a later fix were still reported as unresolved, and the
  audit summary's `fixed` count could go negative.
- A misspelled strategy id in the dispatch table raised partway through a
  repair instead of when the `Repairer` was built.
- A resource id containing path separators could place its audit log
  outside the audit directory.
- `limits.max_llm_calls_per_resource` was never enforced.
- `normalize_date` wrote days the calendar does not have (`1990-02-31`) and
  reported an already-ISO value, even `2023-13-45`, as a fix, which kept
  the rest of the dispatch chain from running. Now 1.1.0.
- `normalize_decimal` wrote back a rounded value when the input had more
  significant digits than a JSON number holds. It now refuses. Now 1.1.0.
- LLM strategies retried 4xx responses such as a bad key or an unknown
  model. Only connection errors, timeouts, 408, 409, 429 and 5xx are
  retried now, and `llm.resolve_invariant` retries like the others.
- The CLI printed a traceback for bad input, a broken config or an
  unreachable validator, and its summary subtracted error counts from
  action counts.

### Changed

- A simple fix costs half as many validator calls: the loop reuses the
  error list it already has instead of validating the same state again.
- The provider SDKs no longer retry on their own underneath the
  strategies' retry loop, which could turn three retries into twelve
  requests.
- The Bedrock adapter sends `temperature` only when asked and allows 16000
  output tokens, matching the Anthropic adapter.
- The CLI exits 2 when it cannot run, keeping 1 for errors left unresolved.
- Supports anthropic 2.x, openai 3.x and typer up to 0.29.
- A config without `strategies` now uses a default table that chains every
  deterministic strategy under `processing`, the code HAPI uses for
  malformed values. Before, the table was empty, so the README quickstart
  and the CLI without `--config` fixed nothing.
- The `deepseek` provider defaults to `deepseek-flash`. DeepSeek retired
  V4 Flash on 2026-09-10 and routes `deepseek-v4-flash` to its successor
  only temporarily.
- `OnPremProvider` moved into `fhir_repair.llm.openai`, as a subclass of
  `OpenAIProvider`; `fhir_repair.llm.on_prem` is gone.
- The audit log's `llm.model` is the model id the server reports, which can
  differ from the configured id when a provider routes an alias.

### Removed

- **Breaking for config files:** `llm.temperature`, `logging.log_level` and
  the `terminology` section. None of them was read, and a config that still
  sets one now fails to load.
- The `fhir_repair.terminology` package and the `specs/` code system index,
  which no strategy used.
- The spec retriever (`strategies.llm.rag`), which never had an index to
  read. The prompt templates are unchanged.
- `fhirpath.evaluate` and the `fhirpathpy` dependency, used only by the
  retriever.
- The `DELETE` sentinel, `dispatcher.check_permission` and the empty
  `fhir_repair._internal` package.

## 0.2.0 - 2026-10-08

### Added

- Deterministic strategies `normalize_telecom`, `normalize_codeable_concept`
  and `canonicalize_identifier_system` (#5).
- `llm.resolve_invariant`, a removal-only invariant repair strategy (#12).
- LLM providers: OpenAI (#8), AWS Bedrock and on-prem (#13), DeepSeek (#16).
- Retry with exponential backoff for transient LLM errors (#3).
- Optional HTTP service built on FastAPI (#9).
- Config validation after load (#11).
- Benchmark: the full 12-class mutation set (#6, #17), the leaderboard
  (#7), the Synthea bundle extractor (#14), and the 100-resource Synthea
  corpus (#15).
- Published benchmark runs, deterministic only and with DeepSeek V4 Flash
  and Pro. See [RESULTS.md](RESULTS.md).

### Changed

- The Anthropic provider defaults to `claude-sonnet-5-5`, sends
  `temperature` only when asked, and allows 16000 output tokens so thinking
  does not crowd out the answer.
- Benchmark runs record whether the validator flagged each case, and
  summaries report a detected-only rate (#16).

### Fixed

- Repair and benchmark bugs found by the full Synthea runs (#10, #15, #16,
  #17). See "Bugs these runs found" in [RESULTS.md](RESULTS.md).

### Removed

- The planned wild-sample error study and its screening procedure.

## 0.1.0 - 2026-04-27

First release. Repair loop with depth-batched dispatch and regression
rollback, the hallucination guard, the JSON Lines audit log, the HAPI
validator adapter, the Anthropic provider, a generic LLM runner, the
`normalize_date`, `normalize_decimal` and `unwrap_singleton` strategies, and
the CLI.
