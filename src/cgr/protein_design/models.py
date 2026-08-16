"""Canonical artifacts for a bounded protein-design campaign."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.science.canonical import CanonicalModel, validate_identifier

_RESIDUE_COUNT = re.compile(
    r"\b(?P<count>\d{1,4})\s*(?:-\s*)?"
    r"(?P<unit>aa|residues?|amino[\s-]+acids?)\b",
    re.IGNORECASE,
)


def resolve_protein_residue_count(
    text: str,
) -> tuple[int | None, str | None, str | None]:
    """Return one exact scientist-stated protein length or a safe ambiguity."""

    matches = tuple(_RESIDUE_COUNT.finditer(text))
    if not matches:
        return (
            None,
            None,
            "An exact protein length in residues is required for de novo backbone generation.",
        )
    values = {int(item.group("count")) for item in matches}
    if len(values) != 1:
        return (
            None,
            None,
            "Multiple different protein lengths were stated; one exact residue count is required.",
        )
    value = next(iter(values))
    if not 20 <= value <= 2000:
        return (
            None,
            None,
            "The requested protein length must be between 20 and 2000 residues.",
        )
    match = matches[0]
    return value, match.group(0), None


class ProteinDesignSpecification(CanonicalModel):
    """Scientist-grounded design request plus explicit operational policy."""

    schema_version: CapabilityVersion
    specification_identifier: str
    design_kind: Literal["de_novo_protein"] = "de_novo_protein"
    residue_count: int = Field(ge=20, le=2000)
    candidate_count: int = Field(ge=1, le=256)
    random_seed: int = Field(ge=1, le=2_147_483_647)
    residue_count_supporting_quote: str = Field(min_length=1, max_length=512)
    original_objective_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operational_policy_identifier: str
    limitations: tuple[str, ...] = ()

    @field_validator("specification_identifier", "operational_policy_identifier")
    @classmethod
    def identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="protein design specification")


class ProteinDesignVerificationReport(CanonicalModel):
    """Fail-closed evidence that all three external design stages completed."""

    schema_version: CapabilityVersion
    report_identifier: str
    specification_artifact_identifier: str
    backbone_artifact_identifiers: tuple[str, ...] = Field(min_length=1)
    sequence_artifact_identifiers: tuple[str, ...] = Field(min_length=1)
    predicted_structure_artifact_identifiers: tuple[str, ...] = Field(min_length=1)
    lineage_complete: bool
    structure_prediction_is_experimental_evidence: Literal[False] = False
    limitations: tuple[str, ...] = Field(min_length=1)

    @field_validator(
        "report_identifier",
        "specification_artifact_identifier",
    )
    @classmethod
    def identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="protein design verification")

    @field_validator(
        "backbone_artifact_identifiers",
        "sequence_artifact_identifiers",
        "predicted_structure_artifact_identifiers",
    )
    @classmethod
    def artifact_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(validate_identifier(item) for item in value))
        if len(normalized) != len(set(normalized)):
            raise ValueError("Protein design evidence identifiers must be unique.")
        return normalized


__all__ = [
    "ProteinDesignSpecification",
    "ProteinDesignVerificationReport",
    "resolve_protein_residue_count",
]
