"""fhir-repair: deterministic-first, LLM-fallback FHIR R4 repair toolkit.

The public surface is intentionally small. Everything else lives under
submodules and is not part of the stability guarantee.
"""

from fhir_repair.core.guard import HallucinationGuard
from fhir_repair.core.models import (
    PromptSegment,
    RepairAction,
    RepairResult,
    ValidationError,
)
from fhir_repair.core.repairer import Repairer

__version__ = "0.1.0"

__all__ = [
    "HallucinationGuard",
    "PromptSegment",
    "RepairAction",
    "RepairResult",
    "Repairer",
    "ValidationError",
    "__version__",
]
