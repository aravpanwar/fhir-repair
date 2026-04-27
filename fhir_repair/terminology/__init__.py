"""Terminology services.

The default `LocalTerminology` ships only the FHIR-internal enumerations
(administrative-gender, contact-point-system, etc.). Anything beyond that
(SNOMED, LOINC, RxNorm) requires a real terminology server reached via a
`TerminologyService` adapter. Users configure the service via
`repair-config.yaml` and supply the endpoint themselves.

The LLM is never used as the terminology source on its own. If a
deployer wires the LLM to suggest a binding, that strategy declares risk
"high" and the audit log records it explicitly.
"""

from fhir_repair.terminology.base import TerminologyService, ValidateCodeResult
from fhir_repair.terminology.local import LocalTerminology

__all__ = ["LocalTerminology", "TerminologyService", "ValidateCodeResult"]
