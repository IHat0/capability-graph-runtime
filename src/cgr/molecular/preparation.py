"""Auditable protonation, tautomer, charge, and spin preparation contracts."""

from __future__ import annotations

import math
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import CanonicalModel
from cgr.science.canonical import validate_identifier

_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)


class MolecularChemicalState(CanonicalModel):
    """One bounded ligand protonation/tautomer alternative."""

    state_identifier: str
    graph_identifier: str
    canonical_smiles: str = Field(min_length=1, max_length=16_384)
    formal_charge: int = Field(ge=-20, le=20)
    explicit_hydrogen_count: int = Field(ge=0)
    tautomer_score: float
    protonation_method: Literal["openbabel_phmodel_3.1.1.23"]
    sampled_ph_values: tuple[float, ...] = Field(min_length=1)
    tautomer_method: Literal["rdkit_2026.03.4"]
    exact_pka_calculated: Literal[False] = False
    assumptions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @field_validator("state_identifier", "graph_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="chemical-state identifier")

    @field_validator("tautomer_score")
    @classmethod
    def validate_score(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Tautomer score must be finite.")
        return float(value)

    @field_validator("sampled_ph_values")
    @classmethod
    def validate_sampled_ph_values(
        cls, value: tuple[float, ...]
    ) -> tuple[float, ...]:
        normalized = tuple(float(item) for item in value)
        if (
            any(not math.isfinite(item) or not 0.0 <= item <= 14.0 for item in normalized)
            or tuple(sorted(set(normalized))) != normalized
        ):
            raise ValueError("Sampled pH values must be unique, ordered, and in [0, 14].")
        return normalized

    @field_validator("assumptions", "warnings")
    @classmethod
    def validate_text(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() or len(item) > 500 for item in value):
            raise ValueError("Chemical-state notes must be nonempty and bounded.")
        return value


class MolecularProtonationPreparation(CanonicalModel):
    """pH-aware bounded ligand-state preparation without false pKa claims."""

    schema_version: CapabilityVersion
    preparation_identifier: str
    source_graph_identifier: str
    target_ph: float = Field(ge=0, le=14)
    ph_window: float = Field(gt=0, le=4)
    ph_sampling_interval: float = Field(gt=0, le=5)
    method: Literal["openbabel_phmodel_3.1.1.23_plus_rdkit_2026.03.4"]
    states: tuple[MolecularChemicalState, ...] = Field(min_length=1, max_length=128)
    selected_state_identifier: str | None = None
    requires_state_selection: bool
    exact_pka_calculated: Literal[False] = False
    protein_residue_states_included: Literal[False] = False
    metal_oxidation_states_included: Literal[False] = False
    warnings: tuple[str, ...] = ()

    @field_validator("preparation_identifier", "source_graph_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="protonation-preparation identifier")

    @field_validator("target_ph", "ph_window", "ph_sampling_interval")
    @classmethod
    def validate_scalars(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Protonation-preparation scalar must be finite.")
        return float(value)

    @field_validator("selected_state_identifier")
    @classmethod
    def validate_selected_identifier(cls, value: str | None) -> str | None:
        return (
            None
            if value is None
            else validate_identifier(value, label="selected chemical-state identifier")
        )

    @field_validator("warnings")
    @classmethod
    def validate_warnings(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() or len(item) > 500 for item in value):
            raise ValueError("Protonation warnings must be nonempty and bounded.")
        return value

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        identifiers = [state.state_identifier for state in self.states]
        graph_identifiers = [state.graph_identifier for state in self.states]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Chemical-state identifiers must be unique.")
        if len(graph_identifiers) != len(set(graph_identifiers)):
            raise ValueError("Chemical-state graph identifiers must be unique.")
        if self.selected_state_identifier is not None and (
            self.selected_state_identifier not in identifiers
        ):
            raise ValueError("Selected chemical state must resolve.")
        if self.requires_state_selection != (self.selected_state_identifier is None):
            raise ValueError("Chemical-state ambiguity flag is inconsistent.")
        if len(self.states) == 1 and self.requires_state_selection:
            raise ValueError("A unique chemical state must be selected automatically.")
        return self


class MolecularProteinResidueState(CanonicalModel):
    """One OpenMM-resolved protein residue protonation state."""

    residue_identifier: str
    chain_identifier: str
    source_sequence_identifier: str
    residue_name: str
    selected_variant: str
    selection_source: Literal["openmm_ph_model", "semantic_override"]
    particle_partial_charge: float
    chemically_ambiguous: bool

    @field_validator(
        "residue_identifier", "chain_identifier", "source_sequence_identifier",
        "residue_name", "selected_variant",
    )
    @classmethod
    def validate_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 128:
            raise ValueError("Protein residue-state text must be bounded and nonblank.")
        return normalized

    @field_validator("particle_partial_charge")
    @classmethod
    def validate_charge(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Protein residue partial charge must be finite.")
        return float(value)


class MolecularProteinProtonationPreparation(CanonicalModel):
    """Auditable OpenMM pH-aware hydrogen and residue-variant preparation."""

    schema_version: CapabilityVersion
    preparation_identifier: str
    source_structure_artifact_identifier: str
    force_field_selection_identifier: str
    target_ph: float = Field(ge=0, le=14)
    method: Literal["openmm_modeller_8.5.2"] = "openmm_modeller_8.5.2"
    source_atom_count: int = Field(gt=0)
    prepared_atom_count: int = Field(gt=0)
    hydrogen_atoms_added: int = Field(ge=0)
    residue_states: tuple[MolecularProteinResidueState, ...] = Field(min_length=1)
    total_particle_partial_charge: float
    explicit_semantic_variant_overrides: tuple[str, ...] = ()
    contains_transition_metal: bool
    metal_oxidation_states_inferred: Literal[False] = False
    exact_pka_calculated: Literal[False] = False
    warnings: tuple[str, ...] = ()

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        if value != _SCHEMA_VERSION:
            raise ValueError("Protein protonation schema version must be 1.0.0.")
        return value

    @field_validator(
        "preparation_identifier", "source_structure_artifact_identifier",
        "force_field_selection_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("target_ph", "total_particle_partial_charge")
    @classmethod
    def validate_scalars(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Protein protonation scalar must be finite.")
        return float(value)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.prepared_atom_count - self.source_atom_count != self.hydrogen_atoms_added:
            raise ValueError("Prepared protein atom and added-hydrogen counts disagree.")
        residue_ids = [state.residue_identifier for state in self.residue_states]
        if len(residue_ids) != len(set(residue_ids)):
            raise ValueError("Prepared protein residue identities must be unique.")
        return self
