"""Validator adapters.

A validator takes a FHIR resource and returns a list of `ValidationError`
objects. The default and only adapter in v0.1 is `HapiRestValidator`.
Additional adapters (Firely, Inferno) can be added without changing core
code, as long as they implement the `Validator` Protocol.
"""

from fhir_repair.validators.base import Validator
from fhir_repair.validators.hapi import HapiRestValidator

__all__ = ["HapiRestValidator", "Validator"]
