"""Deterministic scientific entity resolution over generic molecular topology.

This module converts chemically meaningful structural selectors into stable
topology identities without depending on RDKit, OpenMM, PySCF, or Qiskit.
More advanced pattern and geometry resolution layers build on this foundation.
"""

from __future__ import annotations

import hashlib
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import CanonicalModel
from cgr.science.canonical import validate_identifier

from .topology import MolecularTopologyIndex


_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_MAXIMUM_QUERY_LENGTH = 2048


class MolecularScientificSelectionError(ValueError):
    """Controlled failure to resolve a scientific structural selector."""


def _ordered_identifiers(
    values: tuple[str, ...],
    *,
    label: str,
) -> tuple[str, ...]:
    validated = tuple(
        validate_identifier(value, label=label)
        for value in values
    )
    if len(validated) != len(set(validated)):
        raise ValueError(f"{label.capitalize()} values must be unique.")
    return tuple(sorted(validated))


def _normal_text(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} cannot be blank.")
    return normalized


def _element_symbol(value: str) -> str:
    normalized = _normal_text(value, label="Element symbol")
    if not normalized.isalpha() or len(normalized) > 3:
        raise ValueError("Element symbol is invalid.")
    return normalized[0].upper() + normalized[1:].lower()


def _selection_identifier(
    *,
    structure_identifier: str,
    selection_kind: str,
    atom_identifiers: tuple[str, ...],
    bond_identifiers: tuple[str, ...],
    residue_identifiers: tuple[str, ...],
    query: str,
) -> str:
    payload = "\x1f".join(
        (
            structure_identifier,
            selection_kind,
            ",".join(atom_identifiers),
            ",".join(bond_identifiers),
            ",".join(residue_identifiers),
            query,
        )
    ).encode("utf-8")
    return "scientific-selection-" + hashlib.sha256(payload).hexdigest()[:32]


class ScientificSelectionResolution(CanonicalModel):
    """Immutable result of resolving one scientific structural selector."""

    schema_version: CapabilityVersion = _SCHEMA_VERSION
    resolution_identifier: str
    structure_identifier: str
    selection_kind: Literal["residue", "atom", "bond"]
    atom_identifiers: tuple[str, ...] = Field(min_length=1)
    bond_identifiers: tuple[str, ...] = ()
    residue_identifiers: tuple[str, ...] = ()
    query: str = Field(min_length=1, max_length=_MAXIMUM_QUERY_LENGTH)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        if value != _SCHEMA_VERSION:
            raise ValueError(
                "Scientific-selection schema version must be exactly 1.0.0."
            )
        return value

    @field_validator("resolution_identifier", "structure_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(
            value,
            label="scientific selection identifier",
        )

    @field_validator(
        "atom_identifiers",
        "bond_identifiers",
        "residue_identifiers",
    )
    @classmethod
    def validate_identifier_sets(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _ordered_identifiers(
            value,
            label="scientific selection member identifier",
        )

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        return _normal_text(value, label="Scientific selection query")

    @model_validator(mode="after")
    def validate_kind_content(self) -> Self:
        if self.selection_kind == "bond" and len(self.bond_identifiers) != 1:
            raise ValueError(
                "A resolved bond selection must contain exactly one bond."
            )
        return self


def _resolution(
    topology: MolecularTopologyIndex,
    *,
    selection_kind: Literal["residue", "atom", "bond"],
    atom_identifiers: tuple[str, ...],
    bond_identifiers: tuple[str, ...] = (),
    residue_identifiers: tuple[str, ...] = (),
    query: str,
) -> ScientificSelectionResolution:
    atoms = _ordered_identifiers(
        atom_identifiers,
        label="resolved atom identifier",
    )
    bonds = _ordered_identifiers(
        bond_identifiers,
        label="resolved bond identifier",
    )
    residues = _ordered_identifiers(
        residue_identifiers,
        label="resolved residue identifier",
    )
    return ScientificSelectionResolution(
        resolution_identifier=_selection_identifier(
            structure_identifier=topology.structure_identifier,
            selection_kind=selection_kind,
            atom_identifiers=atoms,
            bond_identifiers=bonds,
            residue_identifiers=residues,
            query=query,
        ),
        structure_identifier=topology.structure_identifier,
        selection_kind=selection_kind,
        atom_identifiers=atoms,
        bond_identifiers=bonds,
        residue_identifiers=residues,
        query=query,
    )


def resolve_residue_selection(
    topology: MolecularTopologyIndex,
    *,
    residue_name: str,
    source_sequence_identifier: str | None = None,
    chain_identifier: str | None = None,
    require_unique: bool = True,
) -> ScientificSelectionResolution:
    """Resolve a residue by scientific name, sequence identity and chain."""

    if not isinstance(topology, MolecularTopologyIndex):
        raise TypeError(
            "Residue resolution requires a MolecularTopologyIndex."
        )

    requested_name = _normal_text(
        residue_name,
        label="Residue name",
    ).casefold()

    requested_sequence = (
        _normal_text(
            source_sequence_identifier,
            label="Residue sequence identifier",
        ).casefold()
        if source_sequence_identifier is not None
        else None
    )

    requested_chain = (
        validate_identifier(
            chain_identifier,
            label="chain identifier",
        )
        if chain_identifier is not None
        else None
    )

    matches = tuple(
        residue
        for residue in topology.residues
        if residue.residue_name.strip().casefold() == requested_name
        and (
            requested_sequence is None
            or (
                residue.source_sequence_identifier is not None
                and residue.source_sequence_identifier.strip().casefold()
                == requested_sequence
            )
        )
        and (
            requested_chain is None
            or residue.chain_identifier == requested_chain
        )
    )

    if not matches:
        raise MolecularScientificSelectionError(
            "No topology residue matches the requested scientific selector."
        )

    if require_unique and len(matches) != 1:
        raise MolecularScientificSelectionError(
            "The requested residue selector is ambiguous."
        )

    residue_identifiers = tuple(
        residue.residue_identifier
        for residue in matches
    )
    atom_identifiers = tuple(
        atom_identifier
        for residue in matches
        for atom_identifier in residue.atom_identifiers
    )
    atom_set = set(atom_identifiers)
    bond_identifiers = tuple(
        bond.bond_identifier
        for bond in topology.bonds
        if bond.atom_identifier_a in atom_set
        and bond.atom_identifier_b in atom_set
    )

    query = (
        f"residue_name={residue_name.strip()};"
        f"source_sequence_identifier="
        f"{source_sequence_identifier.strip() if source_sequence_identifier is not None else '*'};"
        f"chain_identifier={chain_identifier if chain_identifier is not None else '*'}"
    )

    return _resolution(
        topology,
        selection_kind="residue",
        atom_identifiers=atom_identifiers,
        bond_identifiers=bond_identifiers,
        residue_identifiers=residue_identifiers,
        query=query,
    )


def resolve_atom_selection(
    topology: MolecularTopologyIndex,
    *,
    atom_name: str | None = None,
    element_symbol: str | None = None,
    residue_identifiers: tuple[str, ...] = (),
    require_unique: bool = False,
) -> ScientificSelectionResolution:
    """Resolve atoms using exact topology names, elements and optional residues."""

    if not isinstance(topology, MolecularTopologyIndex):
        raise TypeError(
            "Atom resolution requires a MolecularTopologyIndex."
        )

    if atom_name is None and element_symbol is None:
        raise ValueError(
            "Atom resolution requires an atom name or element symbol."
        )

    requested_atom_name = (
        _normal_text(atom_name, label="Atom name").casefold()
        if atom_name is not None
        else None
    )
    requested_element = (
        _element_symbol(element_symbol)
        if element_symbol is not None
        else None
    )

    requested_residues = _ordered_identifiers(
        residue_identifiers,
        label="requested residue identifier",
    )
    known_residues = {
        residue.residue_identifier
        for residue in topology.residues
    }
    if any(
        residue_identifier not in known_residues
        for residue_identifier in requested_residues
    ):
        raise MolecularScientificSelectionError(
            "An atom selector references an unknown residue."
        )

    matches = tuple(
        atom
        for atom in topology.atoms
        if (
            requested_atom_name is None
            or (
                atom.atom_name is not None
                and atom.atom_name.strip().casefold()
                == requested_atom_name
            )
        )
        and (
            requested_element is None
            or atom.element_symbol == requested_element
        )
        and (
            not requested_residues
            or atom.residue_identifier in requested_residues
        )
    )

    if not matches:
        raise MolecularScientificSelectionError(
            "No topology atom matches the requested scientific selector."
        )

    if require_unique and len(matches) != 1:
        raise MolecularScientificSelectionError(
            "The requested atom selector is ambiguous."
        )

    matched_residues = tuple(
        sorted(
            {
                atom.residue_identifier
                for atom in matches
                if atom.residue_identifier is not None
            }
        )
    )

    query = (
        f"atom_name={atom_name.strip() if atom_name is not None else '*'};"
        f"element_symbol={requested_element if requested_element is not None else '*'};"
        f"residue_identifiers={','.join(requested_residues) if requested_residues else '*'}"
    )

    return _resolution(
        topology,
        selection_kind="atom",
        atom_identifiers=tuple(
            atom.atom_identifier
            for atom in matches
        ),
        residue_identifiers=matched_residues,
        query=query,
    )


def resolve_bond_selection(
    topology: MolecularTopologyIndex,
    *,
    atom_identifier_a: str,
    atom_identifier_b: str,
) -> ScientificSelectionResolution:
    """Resolve one explicit topology bond from its stable endpoint identities."""

    if not isinstance(topology, MolecularTopologyIndex):
        raise TypeError(
            "Bond resolution requires a MolecularTopologyIndex."
        )

    endpoint_a = validate_identifier(
        atom_identifier_a,
        label="bond atom identifier",
    )
    endpoint_b = validate_identifier(
        atom_identifier_b,
        label="bond atom identifier",
    )
    if endpoint_a == endpoint_b:
        raise ValueError(
            "A scientific bond selector requires two distinct atoms."
        )

    atom_map = {
        atom.atom_identifier: atom
        for atom in topology.atoms
    }
    if endpoint_a not in atom_map or endpoint_b not in atom_map:
        raise MolecularScientificSelectionError(
            "A bond selector references an unknown atom."
        )

    left, right = sorted((endpoint_a, endpoint_b))
    matches = tuple(
        bond
        for bond in topology.bonds
        if bond.atom_identifier_a == left
        and bond.atom_identifier_b == right
    )

    if len(matches) != 1:
        raise MolecularScientificSelectionError(
            "The requested explicit topology bond does not resolve uniquely."
        )

    residues = tuple(
        sorted(
            {
                atom_map[identifier].residue_identifier
                for identifier in (left, right)
                if atom_map[identifier].residue_identifier is not None
            }
        )
    )

    return _resolution(
        topology,
        selection_kind="bond",
        atom_identifiers=(left, right),
        bond_identifiers=(matches[0].bond_identifier,),
        residue_identifiers=residues,
        query=f"bond={left},{right}",
    )
