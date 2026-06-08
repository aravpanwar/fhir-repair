"""Optional self-hosted HTTP service.

This package exposes the same `Repairer` over HTTP for deployers who want a
service rather than a CLI or library. It is opt-in and self-hosted only:
the project runs no hosted service and accepts no PHI on any project
surface. See DEPLOYMENT-COMPLIANCE.md before running it against real data.

Install the dependencies with `pip install "fhir-repair[service]"` and run:

    uvicorn fhir_repair.service:app --host 0.0.0.0 --port 8000

`app` is built from environment configuration on import. Tests build their
own app with an injected repairer via `create_app`.
"""

from __future__ import annotations

from fhir_repair.service.app import create_app

# Module-level app for `uvicorn fhir_repair.service:app`. Construction is
# deferred to the lifespan handler, so importing this module does not open
# a connection to the validator.
app = create_app()

__all__ = ["app", "create_app"]
