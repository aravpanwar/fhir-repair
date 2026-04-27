"""Shared pytest fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_dir() -> Path:
    return FIXTURE_DIR


@pytest.fixture
def patient_valid() -> dict[str, Any]:
    return _load("patient_valid.json")


@pytest.fixture
def patient_invalid_date() -> dict[str, Any]:
    return _load("patient_invalid_date.json")


@pytest.fixture
def observation_singleton_array() -> dict[str, Any]:
    return _load("observation_singleton_array.json")


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
