"""Auditable small-molecule force-field parameterization artifacts."""

from __future__ import annotations

import math
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import CanonicalModel
from cgr.science.canonical import validate_identifier

_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)


def _finite(value: float, *, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    return float(value)


class MolecularForceFieldAtom(CanonicalModel):
    """One atom typed and charged by a named force field."""

    atom_index: int = Field(ge=0)
    atom_map_number: int = Field(gt=0)
    element_symbol: str
    atom_type: int = Field(gt=0)
    formal_charge: float
    partial_charge: float
    vdw_r_star: float = Field(gt=0)
    vdw_epsilon: float = Field(gt=0)

    @field_validator("formal_charge", "partial_charge", "vdw_r_star", "vdw_epsilon")
    @classmethod
    def finite_values(cls, value: float) -> float:
        return _finite(value, label="Force-field atom parameter")


class MolecularForceFieldTerm(CanonicalModel):
    """One native force-field term with named, ordered parameter values."""

    term_kind: Literal[
        "bond_stretch",
        "angle_bend",
        "stretch_bend",
        "proper_torsion",
        "out_of_plane",
    ]
    atom_indices: tuple[int, ...]
    parameter_names: tuple[str, ...] = Field(min_length=1)
    parameter_values: tuple[float, ...] = Field(min_length=1)
    native_unit_semantics: str

    @field_validator("native_unit_semantics")
    @classmethod
    def validate_semantics(cls, value: str) -> str:
        return validate_identifier(value, label="native parameter unit semantics")

    @field_validator("parameter_names")
    @classmethod
    def validate_parameter_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        names = tuple(
            validate_identifier(item, label="force-field parameter name")
            for item in value
        )
        if len(names) != len(set(names)):
            raise ValueError("Force-field parameter names must be unique per term.")
        return names

    @field_validator("parameter_values")
    @classmethod
    def finite_parameters(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        return tuple(_finite(item, label="Force-field term parameter") for item in value)

    @model_validator(mode="after")
    def validate_term(self) -> Self:
        expected_atoms = {
            "bond_stretch": 2,
            "angle_bend": 3,
            "stretch_bend": 3,
            "proper_torsion": 4,
            "out_of_plane": 4,
        }[self.term_kind]
        if len(self.atom_indices) != expected_atoms:
            raise ValueError("Force-field term atom arity is invalid.")
        if len(set(self.atom_indices)) != len(self.atom_indices):
            raise ValueError("Force-field terms cannot repeat an atom index.")
        if len(self.parameter_names) != len(self.parameter_values):
            raise ValueError("Force-field parameter names and values must align.")
        return self


class MolecularLigandParameterization(CanonicalModel):
    """Complete native MMFF parameter assignment for one small molecule.

    Values are intentionally retained in RDKit's native MMFF semantics.  This
    artifact is suitable as audited input to a separately verified converter;
    it does not claim that an OpenMM System has already been constructed.
    """

    schema_version: CapabilityVersion
    parameterization_identifier: str
    source_graph_identifier: str
    force_field_identifier: Literal["mmff94s"] = "mmff94s"
    force_field_engine: Literal["rdkit"] = "rdkit"
    force_field_engine_version: str
    atoms: tuple[MolecularForceFieldAtom, ...] = Field(min_length=1)
    terms: tuple[MolecularForceFieldTerm, ...]
    total_formal_charge: int
    total_partial_charge: float
    parameter_assignment_complete: bool
    openmm_system_constructed: bool = False
    energy_expression: Literal["rdkit_mmff94s_native"] = "rdkit_mmff94s_native"
    assumptions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        if value != _SCHEMA_VERSION:
            raise ValueError("Ligand parameterization schema version must be 1.0.0.")
        return value

    @field_validator("parameterization_identifier", "source_graph_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("total_partial_charge")
    @classmethod
    def validate_total_charge(cls, value: float) -> float:
        return _finite(value, label="Total partial charge")

    @model_validator(mode="after")
    def validate_parameterization(self) -> Self:
        indices = [atom.atom_index for atom in self.atoms]
        maps = [atom.atom_map_number for atom in self.atoms]
        if sorted(indices) != list(range(len(self.atoms))):
            raise ValueError("Parameter atom indices must be contiguous from zero.")
        if len(maps) != len(set(maps)):
            raise ValueError("Parameter atom mappings must be unique.")
        if any(index >= len(self.atoms) for term in self.terms for index in term.atom_indices):
            raise ValueError("Every force-field term atom must resolve.")
        if not self.parameter_assignment_complete:
            raise ValueError("Incomplete ligand parameter assignments cannot be canonical.")
        if abs(self.total_partial_charge - self.total_formal_charge) > 1.0e-4:
            raise ValueError("MMFF partial charges must conserve molecular formal charge.")
        if abs(sum(atom.formal_charge for atom in self.atoms) - self.total_formal_charge) > 1.0e-6:
            raise ValueError("MMFF atom formal charges must conserve molecular charge.")
        if self.openmm_system_constructed:
            raise ValueError("This native parameter artifact cannot claim an OpenMM System.")
        return self
