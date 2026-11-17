# Changelog

## Unreleased

### Fixed

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

### Changed

- A simple fix costs half as many validator calls: the loop reuses the
  error list it already has instead of validating the same state again.

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
