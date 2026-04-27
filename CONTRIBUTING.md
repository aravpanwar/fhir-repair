# Contributing to fhir-repair

Thank you for considering a contribution. This document explains the project
scope, the rules around protected health information, the developer
certificate of origin (DCO) sign-off requirement, and the workflow for
submitting changes.

## Project scope

`fhir-repair` repairs broken FHIR R4 resources within a single profile. The
following are explicitly out of scope:

- DSTU2, STU3, R4B, or R5 (R4 only)
- Profile-to-profile transformation (semantic mapping is a separate problem)
- Non-FHIR-to-FHIR conversion
- Hosted SaaS or any service that touches user data
- A FHIR server, terminology server, or deidentifier

Pull requests outside this scope will be closed with a pointer to the relevant
upstream project where one exists.

## No PHI, ever

This is a hard rule. Do not paste, attach, or upload protected health
information into any project surface, including but not limited to:

- GitHub issues
- Pull request descriptions or commits
- Test fixtures
- Benchmark corpus
- Comments on code review

Any issue or PR containing apparent PHI will be edited or closed immediately.
If you find PHI in the project history, open an issue tagged `phi-report`
without quoting the content and a maintainer will rewrite history.

For testing, use [Synthea](https://github.com/synthetichealth/synthea) output
or [HAPI test fixtures](https://hapifhir.io/). The benchmark corpus is
documented in [benchmark/corpus/SYNTHEA-GENERATION.md](benchmark/corpus/SYNTHEA-GENERATION.md).

## Developer Certificate of Origin (DCO)

Every commit must be signed off under the [Developer Certificate of Origin
1.1](https://developercertificate.org/). Add a `Signed-off-by` line by
committing with `git commit -s`.

Example:

```
Add normalize_decimal deterministic strategy

Signed-off-by: Jane Developer <jane@example.com>
```

The DCO certifies that you wrote the change or have the right to submit it
under the project's license. A GitHub Actions check enforces sign-off on
every commit; PRs without sign-off cannot merge.

## Development setup

Requirements:

- Python 3.11 or newer
- Docker (for the HAPI validator)

```bash
git clone https://github.com/aravpanwar/fhir-repair.git
cd fhir-repair
python -m venv .venv
source .venv/bin/activate    # on Windows: .venv\Scripts\activate
pip install -e ".[dev,anthropic]"

docker compose -f docker/docker-compose.yml up -d hapi
pytest
```

## Adding a deterministic strategy

Deterministic strategies are pure functions. Each lives in its own module
under `fhir_repair/strategies/deterministic/`, exports `apply()`,
`NAME`, `VERSION`, `PERMISSION`, and `RISK` constants, and is registered in
the strategy registry.

A strategy must:

1. Take `(resource: dict, error: ValidationError)` and return a `RepairAction`.
2. Have no IO. No network calls, no file reads, no clock reads.
3. Declare a hallucination_guard permission. `allow_reformat` is the right
   default for fixes that change wire format only.
4. Refuse cleanly when its preconditions are not met. Return a `RepairAction`
   with `risk="refused"` and a reason in `explanation`.
5. Ship with unit tests covering both the apply and refuse paths.

See `fhir_repair/strategies/deterministic/date.py` for a worked example.

## Adding an LLM provider

LLM provider adapters implement the `LLMProvider` Protocol in
`fhir_repair/llm/base.py`. The adapter receives structured `PromptSegment`
lists and is responsible for translating `stable=True` hints into the
provider's native caching primitive (or ignoring the hint where the
provider handles caching transparently).

A provider adapter must:

1. Implement `complete()` and `supports_caching()`.
2. Read configuration from environment variables, never from a config file.
3. Return a `Completion` with token counts populated where the provider
   reports them.
4. Ship integration tests gated behind an environment-variable flag so they
   do not run in CI by default.

See `fhir_repair/llm/anthropic.py` for the reference implementation.

## Code style

- `ruff` for lint and format
- `mypy --strict` for type checking on `fhir_repair/`
- Type hints on all public functions
- Docstrings on public functions and classes only; no docstring on
  trivially-named private helpers
- Comments where the why is non-obvious; do not narrate what the code does

Run before submitting:

```bash
ruff check fhir_repair tests
ruff format fhir_repair tests
mypy fhir_repair
pytest
```

## Pull request workflow

1. Fork and create a feature branch.
2. Open the PR against `main`. Reference the issue it resolves.
3. CI must pass: lint, type, test, validator-smoke, dco.
4. A maintainer reviews. Typical turnaround is one week.
5. PRs adding strategies should include a description of the error class
   they address and an example before/after.

## Reporting bugs

Open an issue with:

- Input resource (synthetic only, never PHI)
- Expected output
- Actual output
- Audit log of the repair run
- `fhir-repair` version, HAPI version, Python version

Reduce the input to a minimal failing case where possible.

## Security

For security-relevant issues, do not open a public issue. Email the
maintainer privately. Coordinated disclosure timelines follow standard open
source practice (typically 90 days).

## Releases

`fhir-repair` follows SemVer. The stable surface is:

- Public Python API (`from fhir_repair import ...`)
- CLI flags
- Dispatch table format (versioned independently as `dispatch_version`)
- Audit log schema (versioned independently as `v` field in each entry)

`fhir_repair._internal` is not stable and may change in any release.
