"""Provider-neutral protein generation and validation contracts."""

from .external_adapter import (
    BACKBONE_GENERATE,
    SEQUENCE_DESIGN,
    STRUCTURE_PREDICT,
    ExternalProteinEngineAdapter,
    ExternalProteinEngineConfiguration,
    configured_protein_design_adapters,
)
from .models import (
    ProteinDesignSpecification,
    ProteinDesignVerificationReport,
    resolve_protein_residue_count,
)

__all__ = [
    "BACKBONE_GENERATE",
    "SEQUENCE_DESIGN",
    "STRUCTURE_PREDICT",
    "ExternalProteinEngineAdapter",
    "ExternalProteinEngineConfiguration",
    "ProteinDesignSpecification",
    "ProteinDesignVerificationReport",
    "configured_protein_design_adapters",
    "resolve_protein_residue_count",
]
