"""Command-line entry point.

Usage:

    fhir-repair fix <input.json> [--config PATH] [--out PATH]
    fhir-repair validate <input.json> [--config PATH]

Exit status: 0 when the resource is valid, 1 when validation errors remain,
2 when the command could not run (unreadable input or config, validator
unreachable).

The CLI is a thin wrapper over the library. All heavy lifting happens in
`fhir_repair.core.repairer.Repairer`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any, NoReturn

import httpx
import typer
import yaml

from fhir_repair import Repairer
from fhir_repair.core.config import RepairConfig, load_config
from fhir_repair.validators.hapi import DEFAULT_HAPI_URL, HapiRestValidator

app = typer.Typer(
    name="fhir-repair",
    help="Repair broken FHIR R4 resources.",
    no_args_is_help=True,
    add_completion=False,
)

# Distinct from 1 ("errors remain") so a pipeline can tell a partial repair
# from a run that never happened. Matches click's own status for usage errors.
_CANNOT_RUN = 2


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
    config = _load_config(config_path)
    resource = _load_resource(input_path)

    url = hapi_url or DEFAULT_HAPI_URL
    validator = HapiRestValidator(base_url=url)
    try:
        try:
            repairer = Repairer(validator=validator, config=config)
        except (ImportError, NotImplementedError, ValueError) as exc:
            _fail(f"couldn't set up the repair: {exc}")
        try:
            result = repairer.repair(resource)
        except httpx.HTTPError as exc:
            _fail(f"validator request to {url} failed: {exc}")
    finally:
        validator.close()

    payload = json.dumps(result.fixed_resource, indent=2)
    if output_path is not None:
        try:
            output_path.write_text(payload, encoding="utf-8")
        except OSError as exc:
            _fail(f"couldn't write {output_path}: {exc}")
        typer.echo(f"Wrote fixed resource to {output_path}", err=True)
    else:
        typer.echo(payload)

    applied = sum(1 for action in result.audit if action.risk != "refused")
    typer.echo(
        f"{applied} fixes applied, {len(result.audit) - applied} refused; "
        f"{len(result.unresolved)} errors unresolved; {result.duration_ms} ms",
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
    config = _load_config(config_path)
    resource = _load_resource(input_path)

    url = hapi_url or DEFAULT_HAPI_URL
    validator = HapiRestValidator(base_url=url)
    try:
        errors = validator.validate(resource, profile=config.target_profile)
    except httpx.HTTPError as exc:
        _fail(f"validator request to {url} failed: {exc}")
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


def _load_config(path: Path | None) -> RepairConfig:
    if path is None:
        return RepairConfig()
    try:
        return load_config(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        _fail(f"couldn't load config {path}: {exc}")


def _load_resource(path: Path) -> dict[str, Any]:
    try:
        resource = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail(f"expected JSON in {path}: {exc}")
    if not isinstance(resource, dict) or not resource.get("resourceType"):
        _fail(f"expected a FHIR resource with a resourceType in {path}")
    return resource


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(_CANNOT_RUN)


if __name__ == "__main__":
    app()
