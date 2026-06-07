# fhir-repair

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Status: Pre-release (v0.1)](https://img.shields.io/badge/status-pre--release-orange.svg)](#project-status)

A toolkit that takes broken FHIR R4 resources and produces validator-passing
fixed versions, using a deterministic-first, LLM-fallback approach with
explicit hallucination guards and full audit logging.

`fhir-repair` is **not** "AI fixes your FHIR." It is **code fixes the easy 80%,
the LLM only sees the hard interpretive 20%, and nothing invents clinical data
without saying so**.

## Why

Every FHIR interoperability project deals with non-conformant resources daily:
cross-vendor variance, legacy-to-FHIR conversions, profile drift, malformed
output from upstream systems. Existing tools either reject invalid input
outright or silently coerce it. `fhir-repair` does neither: it fixes what can
be fixed deterministically, asks an LLM only for the genuinely interpretive
cases, refuses to invent clinical data by default, and records every action it
takes in a machine-readable audit log.

## How it works

1. **Parse** the input resource and detect the FHIR version.
2. **Validate** against a HAPI FHIR validator. If the resource is already
   valid, return it as-is.
3. **Dispatch** each error through a YAML-configurable strategy table:
   deterministic fix, LLM fix, refuse, or escalate to human review.
4. **Apply** deterministic strategies in parallel (pure functions, no IO),
   re-validate, then queue any remaining errors for the LLM with a retrieved
   spec excerpt and a hallucination guard.
5. **Re-validate** after each round. Roll back any fix that introduces a new
   error. Halt when valid, when budget is exhausted, or when the error set
   stops changing.
6. **Return** the fixed resource, an unresolved-error list, and a JSON Lines
   audit log of every action with provenance.

See [docs/architecture.md](docs/architecture.md) for the full flow.

## Installation

```bash
pip install fhir-repair
```

For the Anthropic LLM provider:

```bash
pip install "fhir-repair[anthropic]"
```

You will also need a running FHIR validator. The simplest setup is a local
HAPI server via Docker:

```bash
docker compose -f docker/docker-compose.yml up -d hapi
```

## Quickstart

```python
from fhir_repair import Repairer
from fhir_repair.validators.hapi import HapiRestValidator

broken = {
    "resourceType": "Patient",
    "birthDate": "1990-3-5",
    "gender": ["male"],
}

repairer = Repairer(validator=HapiRestValidator())
result = repairer.repair(broken)

print(result.fixed_resource["birthDate"])  # "1990-03-05"
print(result.fixed_resource["gender"])     # "male"

for action in result.audit:
    print(f"{action.strategy}: {action.before} -> {action.after}")
```

CLI:

```bash
fhir-repair fix patient.json --config repair-config.yaml --out fixed.json
```

See [examples/quickstart.md](examples/quickstart.md) for a complete walkthrough.

## Configuration

Strategy choices, LLM endpoint, hallucination guard permissions, and
terminology service are all set in a single YAML file:

```yaml
target_profile: http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient
fhir_version: "4.0.1"

strategies:
  invalid-date-format: deterministic.normalize_date
  invalid-decimal-format: deterministic.normalize_decimal
  unexpected-array: deterministic.unwrap_singleton
  invalid-code-binding: llm.suggest_terminology_match
  missing-required-element: refuse

llm:
  provider: ${LLM_PROVIDER}
  model: ${LLM_MODEL}
  endpoint: ${LLM_ENDPOINT}
  api_key: ${LLM_API_KEY}

hallucination_guard:
  allow_reformat: true
  allow_bind_required_valueset: true
  allow_bind_extensible_valueset: false
  allow_add_missing_required_field: false
  allow_change_existing_clinical_value: false
```

See [examples/repair-config.yaml](examples/repair-config.yaml) for the full
reference.

## Project status

This is a v0.1 pre-release. Current capabilities:

- 6 deterministic strategies: `normalize_date`, `normalize_decimal`,
  `unwrap_singleton`, `normalize_telecom`, `normalize_codeable_concept`,
  `canonicalize_identifier_system`
- 2 LLM strategies (opt-in via dispatch table):
  `llm.suggest_terminology_match`, generic `llm`
- HAPI REST validator adapter (pinned to HAPI 7.4.0)
- Anthropic LLM provider adapter
- JSON Lines audit log with sealed v1 schema
- CLI and Python library
- Starter benchmark corpus (6 hand-curated R4 resources)

Deferred to later releases:

- FastAPI HTTP service
- Additional deterministic strategies (invariant)
- Additional LLM providers (OpenAI, Bedrock, on-prem)
- Full Synthea-generated benchmark corpus and wild-sample empirical study corpus
- Public model-vs-prompt benchmark leaderboard

## Deployment and compliance

`fhir-repair` is a self-deployed library and CLI. No hosted service exists or
is planned. The project does not accept PHI in any surface (issues, pull
requests, fixtures, benchmark corpus). See
[DEPLOYMENT-COMPLIANCE.md](DEPLOYMENT-COMPLIANCE.md) for guidance on running
the tool inside a HIPAA-compliant deployment. The project itself is not, and
makes no claim to be, HIPAA-certified.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
scope rules, the PHI ban, the DCO sign-off requirement, and the new-strategy
PR template.

## License

[Apache License 2.0](LICENSE).

## Acknowledgements

This project uses [Synthea](https://github.com/synthetichealth/synthea) for
synthetic test data, the [HAPI FHIR](https://hapifhir.io/) validator, and
[fhirpathpy](https://github.com/beda-software/fhirpath-py) for FHIRPath
evaluation.
