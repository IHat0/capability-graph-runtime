"""Canonical topology index contracts for artifact-backed structures."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal, Self, TypeVar

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import ArtifactPointer, CanonicalModel
from cgr.science.canonical import validate_identifier

_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_ELEMENT_SYMBOL = re.compile(r"^[A-Za-z]{1,3}$")
_LABEL_MAXIMUM_LENGTH = 128

_TopologyModel = TypeVar("_TopologyModel", bound=CanonicalModel)


def _validate_schema_version(value: CapabilityVersion) -> CapabilityVersion:
    if value != _SCHEMA_VERSION:
        raise ValueError("Molecular topology schema version must be exactly 1.0.0.")
    return value


def _validate_optional_label(value: str | None, *, label: str) -> str | None:
    if value is not None and not value.strip():
        raise ValueError(f"{label} cannot be blank.")
    return value


def _canonical_identifiers(
    value: tuple[str, ...],
    *,
    label: str,
) -> tuple[str, ...]:
    identifiers = tuple(validate_identifier(item, label=label) for item in value)
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"{label.capitalize()} values must be unique.")
    return tuple(sorted(identifiers))


def _canonical_models(
    value: tuple[_TopologyModel, ...],
    *,
    identifier_field: str,
    label: str,
) -> tuple[_TopologyModel, ...]:
    identifiers = [getattr(item, identifier_field) for item in value]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"{label.capitalize()} identifiers must be unique.")
    return tuple(sorted(value, key=lambda item: getattr(item, identifier_field)))


class MolecularTopologyAtom(CanonicalModel):
    """One logical atom in the topology reference model."""

    atom_identifier: str
    atom_index: int = Field(ge=0)
    element_symbol: str
    atom_name: str | None = Field(default=None, max_length=_LABEL_MAXIMUM_LENGTH)
    model_index: int = Field(default=0, ge=0)
    chain_identifier: str | None = None
    residue_identifier: str | None = None
    component_identifier: str | None = None
    formal_charge: int | None = None
    source_serial: str | None = Field(
        default=None,
        max_length=_LABEL_MAXIMUM_LENGTH,
    )

    @field_validator(
        "atom_identifier",
        "chain_identifier",
        "residue_identifier",
        "component_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return (
            validate_identifier(value, label="topology identifier")
            if value is not None
            else None
        )

    @field_validator("element_symbol")
    @classmethod
    def normalize_element_symbol(cls, value: str) -> str:
        stripped = value.strip()
        if not _ELEMENT_SYMBOL.fullmatch(stripped):
            raise ValueError("Element symbol must contain one to three letters.")
        return stripped[0].upper() + stripped[1:].lower()

    @field_validator("atom_name")
    @classmethod
    def validate_atom_name(cls, value: str | None) -> str | None:
        return _validate_optional_label(value, label="Atom name")

    @field_validator("source_serial")
    @classmethod
    def validate_source_serial(cls, value: str | None) -> str | None:
        return _validate_optional_label(value, label="Source serial")


class MolecularTopologyBond(CanonicalModel):
    """Explicit source bond with canonical endpoint ordering."""

    bond_identifier: str
    atom_identifier_a: str
    atom_identifier_b: str
    bond_order: float | None = Field(default=None, gt=0)
    bond_type: str

    @model_validator(mode="before")
    @classmethod
    def canonicalize_endpoints(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        atom_a = value.get("atom_identifier_a")
        atom_b = value.get("atom_identifier_b")
        if isinstance(atom_a, str) and isinstance(atom_b, str) and atom_b < atom_a:
            normalized = dict(value)
            normalized["atom_identifier_a"] = atom_b
            normalized["atom_identifier_b"] = atom_a
            return normalized
        return value

    @field_validator(
        "bond_identifier",
        "atom_identifier_a",
        "atom_identifier_b",
        "bond_type",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="topology bond identifier")

    @model_validator(mode="after")
    def reject_self_bond(self) -> Self:
        if self.atom_identifier_a == self.atom_identifier_b:
            raise ValueError("A molecular topology bond cannot reference one atom twice.")
        return self


class MolecularTopologyResidue(CanonicalModel):
    """Residue membership declared by the parsed source topology."""

    residue_identifier: str
    residue_name: str = Field(min_length=1, max_length=_LABEL_MAXIMUM_LENGTH)
    chain_identifier: str | None = None
    atom_identifiers: tuple[str, ...] = Field(min_length=1)
    source_sequence_identifier: str | None = Field(
        default=None,
        max_length=_LABEL_MAXIMUM_LENGTH,
    )

    @field_validator("residue_identifier", "chain_identifier")
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return (
            validate_identifier(value, label="topology residue identifier")
            if value is not None
            else None
        )

    @field_validator("residue_name")
    @classmethod
    def validate_residue_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Residue name cannot be blank.")
        return value

    @field_validator("source_sequence_identifier")
    @classmethod
    def validate_source_sequence_identifier(
        cls,
        value: str | None,
    ) -> str | None:
        return _validate_optional_label(value, label="Source sequence identifier")

    @field_validator("atom_identifiers")
    @classmethod
    def validate_atom_identifiers(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _canonical_identifiers(value, label="residue atom identifier")


class MolecularTopologyChain(CanonicalModel):
    """Chain membership declared by the parsed source topology."""

    chain_identifier: str
    atom_identifiers: tuple[str, ...] = Field(min_length=1)
    residue_identifiers: tuple[str, ...] = ()

    @field_validator("chain_identifier")
    @classmethod
    def validate_chain_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="topology chain identifier")

    @field_validator("atom_identifiers")
    @classmethod
    def validate_atom_identifiers(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _canonical_identifiers(value, label="chain atom identifier")

    @field_validator("residue_identifiers")
    @classmethod
    def validate_residue_identifiers(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _canonical_identifiers(value, label="chain residue identifier")


class MolecularTopologyIndex(CanonicalModel):
    """Deterministic logical topology index without embedded coordinates."""

    schema_version: CapabilityVersion
    structure_identifier: str
    source_structure: ArtifactPointer
    structure_format: Literal["pdb", "mmcif", "mol", "sdf", "xyz"]
    parser_identifier: str
    parser_version: str
    atoms: tuple[MolecularTopologyAtom, ...] = Field(min_length=1)
    bonds: tuple[MolecularTopologyBond, ...] = ()
    residues: tuple[MolecularTopologyResidue, ...] = ()
    chains: tuple[MolecularTopologyChain, ...] = ()
    model_count: int = Field(gt=0)
    frame_count: int = Field(gt=0)
    coordinate_unit: Literal["angstrom", "bohr", "nanometer"]

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator("structure_identifier", "parser_identifier", "parser_version")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="molecular topology identifier")

    @field_validator("atoms")
    @classmethod
    def validate_atoms(
        cls,
        value: tuple[MolecularTopologyAtom, ...],
    ) -> tuple[MolecularTopologyAtom, ...]:
        identifiers = [item.atom_identifier for item in value]
        indices = [item.atom_index for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Topology atom identifiers must be unique.")
        if len(indices) != len(set(indices)):
            raise ValueError("Topology atom indices must be unique.")
        if sorted(indices) != list(range(len(indices))):
            raise ValueError("Topology atom indices must be contiguous from zero.")
        return tuple(sorted(value, key=lambda item: item.atom_index))

    @field_validator("bonds")
    @classmethod
    def validate_bonds(
        cls,
        value: tuple[MolecularTopologyBond, ...],
    ) -> tuple[MolecularTopologyBond, ...]:
        return _canonical_models(
            value,
            identifier_field="bond_identifier",
            label="topology bond",
        )

    @field_validator("residues")
    @classmethod
    def validate_residues(
        cls,
        value: tuple[MolecularTopologyResidue, ...],
    ) -> tuple[MolecularTopologyResidue, ...]:
        return _canonical_models(
            value,
            identifier_field="residue_identifier",
            label="topology residue",
        )

    @field_validator("chains")
    @classmethod
    def validate_chains(
        cls,
        value: tuple[MolecularTopologyChain, ...],
    ) -> tuple[MolecularTopologyChain, ...]:
        return _canonical_models(
            value,
            identifier_field="chain_identifier",
            label="topology chain",
        )

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        atoms = {item.atom_identifier: item for item in self.atoms}
        residues = {item.residue_identifier: item for item in self.residues}
        chains = {item.chain_identifier: item for item in self.chains}

        if any(atom.model_index >= self.model_count for atom in self.atoms):
            raise ValueError("Topology atom model indices must resolve.")

        for bond in self.bonds:
            if (
                bond.atom_identifier_a not in atoms
                or bond.atom_identifier_b not in atoms
            ):
                raise ValueError("Every topology bond endpoint must resolve.")

        for residue in self.residues:
            if any(identifier not in atoms for identifier in residue.atom_identifiers):
                raise ValueError("Every topology residue atom must resolve.")
            if (
                residue.chain_identifier is not None
                and residue.chain_identifier not in chains
            ):
                raise ValueError("Every topology residue chain must resolve.")

        for chain in self.chains:
            if any(identifier not in atoms for identifier in chain.atom_identifiers):
                raise ValueError("Every topology chain atom must resolve.")
            if any(
                identifier not in residues for identifier in chain.residue_identifiers
            ):
                raise ValueError("Every topology chain residue must resolve.")

        for atom in self.atoms:
            if (
                atom.residue_identifier is not None
                and atom.residue_identifier not in residues
            ):
                raise ValueError("Every topology atom residue must resolve.")
            if (
                atom.chain_identifier is not None
                and atom.chain_identifier not in chains
            ):
                raise ValueError("Every topology atom chain must resolve.")

        return self
