# Deployment and Compliance Guide

`fhir-repair` is a self-deployed library and CLI. The project itself is not
HIPAA-certified and makes no compliance claims. Deployers are responsible for
the compliance posture of their own environment.

This document describes how `fhir-repair` is designed so that it can be
**deployed inside a HIPAA-compliant environment**, when the deployer
configures it appropriately.

## Design properties relevant to compliance

### Statelessness

`fhir-repair` keeps no state between requests. There is no database, no
session storage, no user-account system. Every call is self-contained:
input goes in, repaired resource and audit log come out, nothing persists
inside the library.

### No telemetry

The library makes no outbound network calls except to:

1. The validator endpoint you configure (typically a HAPI server you run).
2. The LLM endpoint you configure, if you have enabled an LLM strategy.
3. The terminology service you configure, if you have enabled one.

There is no analytics, no error reporting to a third party, no version
check against an upstream server. You can verify this by running the
library in an air-gapped environment with the validator and LLM endpoints
mocked locally.

### BYO endpoints

The LLM endpoint and terminology server endpoint are configured by you,
through environment variables. The library ships with no default endpoint
that would route data outside your control. If you do not configure an
LLM endpoint, no LLM is invoked; the library degrades to deterministic
strategies only.

### Audit log

Every repair action is recorded in a JSON Lines audit log written to a
location you configure. The audit log contains the input value, the
output value, and the strategy that produced the change. Treat the audit
log as PHI when the input was PHI.

## Configuration recommendations for HIPAA-relevant deployments

These are starting points, not legal advice. Consult your compliance
officer or legal counsel for your specific deployment.

### LLM endpoint

If you use an LLM with PHI, you need a Business Associate Agreement (BAA)
with the LLM provider. Major providers offering BAAs as of writing:

- AWS Bedrock (covered under the standard AWS BAA)
- Google Cloud Vertex AI (under the Google Workspace / GCP BAA)
- Microsoft Azure OpenAI (under the Microsoft BAA)
- Anthropic (BAA available; contact sales)

Self-hosted models (vLLM, llama.cpp, Ollama) keep data inside your network
and require no third-party BAA.

Set the LLM endpoint via environment variable:

```bash
export LLM_ENDPOINT=https://my-bedrock-proxy.internal.example
export LLM_API_KEY=...
```

Never put credentials in `repair-config.yaml`.

### Terminology server

The default `tx.fhir.org` adapter sends codes to a public test server.
**Do not use this with PHI**. For deployments touching PHI, run a local
terminology server (HAPI with terminology loaded, Snowstorm for SNOMED,
or a commercial terminology service) and point the adapter at it.

### Audit log destination

Audit logs are written as files. For PHI deployments:

- Write to a volume with encryption at rest enabled.
- Apply your standard log retention and access controls.
- Ship to your existing SIEM if you log PHI access centrally.

The library does not encrypt the audit log itself. Encryption is the
deployer's responsibility.

### Network egress

If you are running in a sensitive environment, restrict egress at the
network layer:

- Allow outbound to your validator (typically `localhost`).
- Allow outbound to your LLM endpoint.
- Allow outbound to your terminology server.
- Block everything else.

## Things this library deliberately does not do

- It does not encrypt PHI in transit. Use HTTPS for your validator and LLM
  endpoints; the library does not enforce this.
- It does not deidentify resources. If you need deidentification, use a
  dedicated tool first (HAPI's deidentification operations, Smart on FHIR
  deidentifiers, etc.) and pass the deidentified output to `fhir-repair`.
- It does not store or transmit data to anyone except the endpoints you
  configure.
- It does not provide BAA coverage. The library is open source software
  with no vendor on the other end of it.

## Verifying behavior

To confirm `fhir-repair` is not making unexpected network calls in your
environment:

```bash
sudo tcpdump -i any -n host not <your-validator-host> and host not <your-llm-host>
fhir-repair fix sample.json --config repair-config.yaml --out /tmp/fixed.json
```

You should see no traffic.

## Reporting compliance-relevant issues

If you discover behavior in `fhir-repair` that is inconsistent with this
document (network calls to unexpected endpoints, audit log gaps, persistence
that should not exist), report it as a security issue per the procedure in
`CONTRIBUTING.md`.
