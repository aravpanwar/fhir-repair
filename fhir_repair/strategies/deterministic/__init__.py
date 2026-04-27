"""Deterministic strategies: pure functions, no IO.

Each module exposes:
  - `NAME`: strategy id used in dispatch tables and audit logs
  - `VERSION`: SemVer of the strategy implementation
  - `PERMISSION`: which HallucinationGuard permission this strategy exercises
  - `RISK`: default risk classification
  - `apply(resource, error) -> RepairAction`

To add a new strategy, drop a module here following the same shape, then
register it in `fhir_repair.strategies.registry.default_registry`.
"""
