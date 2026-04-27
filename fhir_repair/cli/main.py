"""Command-line entry point.

Usage:

    fhir-repair fix <input.json> [--config PATH] [--out PATH]
    fhir-repair validate <input.json> [--config PATH]

The CLI is a thin wrapper over the library. All heavy lifting happens in
`fhir_repair.core.repairer.Repairer`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from fhir_repair import Repairer
from fhir_repair.core.config import RepairConfig, load_config
from fhir_repair.validators.hapi import HapiRestValidator

app = typer.Typer(
    name="fhir-repair",
    help="Repair broken FHIR R4 resources.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def fix(
    input_path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            readable=True,
            help="Path to a FHIR resource JSON file.",
        ),
    ],
    config_path: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            exists=True,
            readable=True,
            help="Path to repair-config.yaml. Defaults to built-in defaults.",
        ),
    ] = None,
    output_path: Annotated[
        Path | None,
        typer.Option(
            "--out",
            "-o",
            help="Where to write the fixed resource. Defaults to stdout.",
        ),
    ] = None,
    hapi_url: Annotated[
        str | None,
        typer.Option(
            "--hapi-url",
            envvar="HAPI_BASE_URL",
            help="Base URL of the HAPI validator. Defaults to http://localhost:8080/fhir.",
        ),
    ] = None,
) -> None:
    """Repair a FHIR resource and write the result to a file or stdout."""
    config = load_config(config_path) if config_path else RepairConfig()
    resource = json.loads(input_path.read_text(encoding="utf-8"))

    validator = HapiRestValidator(base_url=hapi_url) if hapi_url else HapiRestValidator()
    try:
        repairer = Repairer(validator=validator, config=config)
        result = repairer.repair(resource)
    finally:
        validator.close()

    payload = json.dumps(result.fixed_resource, indent=2)
    if output_path is not None:
        output_path.write_text(payload, encoding="utf-8")
        typer.echo(f"Wrote fixed resource to {output_path}", err=True)
    else:
        typer.echo(payload)

    typer.echo(
        f"Fixed {len(result.audit) - len(result.unresolved)} of "
        f"{len(result.audit)} actions; {len(result.unresolved)} unresolved; "
        f"{result.duration_ms} ms",
        err=True,
    )

    if result.unresolved:
        # Non-zero exit so shell pipelines can detect partial repair.
        sys.exit(1)


@app.command()
def validate(
    input_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    config_path: Annotated[
        Path | None,
        typer.Option("--config", "-c", exists=True, readable=True),
    ] = None,
    hapi_url: Annotated[
        str | None,
        typer.Option("--hapi-url", envvar="HAPI_BASE_URL"),
    ] = None,
) -> None:
    """Validate a resource without attempting any repair.

    Useful for inspecting what HAPI sees before running `fix`.
    """
    config = load_config(config_path) if config_path else RepairConfig()
    resource = json.loads(input_path.read_text(encoding="utf-8"))

    validator = HapiRestValidator(base_url=hapi_url) if hapi_url else HapiRestValidator()
    try:
        errors = validator.validate(resource, profile=config.target_profile)
    finally:
        validator.close()

    if not errors:
        typer.echo("Resource is valid.")
        return

    for error in errors:
        typer.echo(
            f"{error.severity:>11}  {error.code:<32}  {error.location}\n"
            f"             {error.message}"
        )
    sys.exit(1)


if __name__ == "__main__":
    app()
