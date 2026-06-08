"""FastAPI application factory for the optional HTTP service.

The service is a thin wrapper over `Repairer`, mirroring the CLI: one
endpoint repairs a resource, one reports health. All repair logic lives in
the core library; this module only handles transport, validation of the
request envelope, and error mapping.

`create_app` builds the application. When called with no arguments it
constructs the repairer from environment configuration inside the lifespan
handler, so importing the module never opens a validator connection. Tests
inject a ready-made repairer instead.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from fhir_repair import Repairer
from fhir_repair.core.config import RepairConfig, load_config
from fhir_repair.validators.hapi import HapiRestValidator


class RepairRequest(BaseModel):
    """Envelope for a repair request: a single FHIR resource."""

    resource: dict[str, Any]


class RepairResponse(BaseModel):
    """Repair outcome, mirroring `RepairResult`."""

    fixed_resource: dict[str, Any]
    audit: list[dict[str, Any]]
    unresolved: list[dict[str, Any]]
    duration_ms: int
    metadata: dict[str, Any]


def _load_config_from_env() -> RepairConfig:
    """Load config from the path in FHIR_REPAIR_CONFIG, or use defaults."""
    config_path = os.environ.get("FHIR_REPAIR_CONFIG")
    return load_config(config_path) if config_path else RepairConfig()


def create_app(repairer: Repairer | None = None) -> FastAPI:
    """Build the FastAPI app.

    Pass `repairer` to inject a pre-built instance (used in tests). Leave it
    None in production: the lifespan handler builds the validator and
    repairer from the environment at startup and closes the validator at
    shutdown.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if repairer is not None:
            app.state.repairer = repairer
            yield
            return

        config = _load_config_from_env()
        base_url = os.environ.get("HAPI_BASE_URL")
        validator = HapiRestValidator(base_url=base_url) if base_url else HapiRestValidator()
        app.state.repairer = Repairer(validator=validator, config=config)
        try:
            yield
        finally:
            validator.close()

    app = FastAPI(
        title="fhir-repair",
        description="Self-hosted HTTP wrapper over the fhir-repair library.",
        lifespan=lifespan,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/repair", response_model=RepairResponse)
    def repair(req: RepairRequest, request: Request) -> RepairResponse:
        resource = req.resource
        if "resourceType" not in resource:
            raise HTTPException(
                status_code=422,
                detail="resource must include a resourceType field",
            )

        repairer: Repairer = request.app.state.repairer
        try:
            result = repairer.repair(resource)
        except httpx.HTTPError as exc:
            # The validator backend is unreachable or returned an error
            # status. That is a gateway problem, not a client one.
            raise HTTPException(
                status_code=502,
                detail=f"validator request failed: {exc}",
            ) from exc

        return RepairResponse(
            fixed_resource=result.fixed_resource,
            audit=[asdict(action) for action in result.audit],
            unresolved=[asdict(error) for error in result.unresolved],
            duration_ms=result.duration_ms,
            metadata=result.metadata,
        )

    return app
