"""Declared structural relationships from acquired PDB records.

Explicit source-declared connections are preferred over geometric inference.
This module extracts those connections without inventing chemical meaning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .scientific_acquisition import AcquiredScientificInput
from .scientific_resolution import ScientificResolutionError


PdbEntityType = Literal[
    "polymer",
    "nonpolymer",
    "branched",
    "water",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class PdbConnectionEndpoint:
    """One source-side endpoint of a PDB-declared structural connection."""

    entity_type: PdbEntityType
    residue_name: str
    chain_label: str
    residue_sequence: str
    insertion_code: str | None
    atom_name: str
    source_serial: str


@dataclass(frozen=True, slots=True)
class PdbDeclaredConnection:
    """One explicit connection declared by the source PDB."""

    connection_type: str
    endpoint_a: PdbConnectionEndpoint
    endpoint_b: PdbConnectionEndpoint


@dataclass(frozen=True, slots=True)
class PdbRelationshipEvidence:
    """Explicit structural relationships extracted from one acquired PDB."""

    all_connections: tuple[PdbDeclaredConnection, ...]
    protein_secondary_connections: tuple[PdbDeclaredConnection, ...]


def _normal(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace("\x00", "")
    return text or None


def _entity_type_name(value: object, gemmi: object) -> PdbEntityType:
    if value == gemmi.EntityType.Polymer:
        return "polymer"
    if value == gemmi.EntityType.NonPolymer:
        return "nonpolymer"
    if value == gemmi.EntityType.Water:
        return "water"

    branched = getattr(gemmi.EntityType, "Branched", None)
    if branched is not None and value == branched:
        return "branched"

    return "unknown"


def _atom_address(
    *,
    chain: object,
    residue: object,
    atom: object,
) -> tuple[str, str, str, str, str]:
    return (
        _normal(getattr(chain, "name", None)) or "chain",
        _normal(getattr(residue, "name", None)) or "residue",
        _normal(getattr(residue, "seqid", None)) or "0",
        _normal(getattr(atom, "name", None)) or "atom",
        _normal(getattr(atom, "altloc", None)) or "",
    )


def _partner_address(partner: object) -> tuple[str, str, str, str, str]:
    residue_id = getattr(partner, "res_id", None)
    return (
        _normal(getattr(partner, "chain_name", None)) or "chain",
        _normal(getattr(residue_id, "name", None)) or "residue",
        _normal(getattr(residue_id, "seqid", None)) or "0",
        _normal(getattr(partner, "atom_name", None)) or "atom",
        _normal(getattr(partner, "altloc", None)) or "",
    )


def _endpoint(
    *,
    chain: object,
    residue: object,
    atom: object,
    gemmi: object,
) -> PdbConnectionEndpoint:
    seqid = getattr(residue, "seqid", None)
    sequence_number = getattr(seqid, "num", None)
    if sequence_number is None:
        raise ScientificResolutionError(
            "A declared PDB connection references a residue without sequence identity."
        )

    serial = getattr(atom, "serial", None)
    if not isinstance(serial, int) or serial <= 0:
        raise ScientificResolutionError(
            "A declared PDB connection references an atom without source serial."
        )

    residue_name = _normal(getattr(residue, "name", None))
    atom_name = _normal(getattr(atom, "name", None))
    if residue_name is None or atom_name is None:
        raise ScientificResolutionError(
            "A declared PDB connection has incomplete source identity."
        )

    return PdbConnectionEndpoint(
        entity_type=_entity_type_name(
            getattr(residue, "entity_type", None),
            gemmi,
        ),
        residue_name=residue_name.upper(),
        chain_label=_normal(getattr(chain, "name", None)) or "",
        residue_sequence=str(sequence_number),
        insertion_code=_normal(getattr(seqid, "icode", None)),
        atom_name=atom_name,
        source_serial=str(serial),
    )


def declared_pdb_relationships(
    acquired: AcquiredScientificInput,
) -> PdbRelationshipEvidence:
    """Extract explicit source-declared relationships from a protein PDB."""

    if acquired.artifact_type != "protein_structure":
        raise ScientificResolutionError(
            "Declared PDB relationships require a protein_structure acquisition."
        )
    if acquired.media_type != "chemical/x-pdb":
        raise ScientificResolutionError(
            "Declared PDB relationships require chemical/x-pdb input."
        )

    try:
        text = acquired.payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ScientificResolutionError(
            "Acquired PDB payload must be valid UTF-8 text."
        ) from None

    try:
        import gemmi
    except ImportError:
        raise ScientificResolutionError(
            "Gemmi is required for declared PDB relationship extraction."
        ) from None

    try:
        structure = gemmi.read_pdb_string(text)
        structure.setup_entities()
        models = tuple(structure)
    except Exception:
        raise ScientificResolutionError(
            "Failed to inspect declared PDB relationships."
        ) from None

    if not models:
        raise ScientificResolutionError(
            "Acquired PDB payload contains no molecular models."
        )

    address_map: dict[
        tuple[str, str, str, str, str],
        list[PdbConnectionEndpoint],
    ] = {}

    for chain in models[0]:
        for residue in chain:
            for atom in residue:
                address_map.setdefault(
                    _atom_address(
                        chain=chain,
                        residue=residue,
                        atom=atom,
                    ),
                    [],
                ).append(
                    _endpoint(
                        chain=chain,
                        residue=residue,
                        atom=atom,
                        gemmi=gemmi,
                    )
                )

    connections: list[PdbDeclaredConnection] = []

    for connection in getattr(structure, "connections", ()):
        left = address_map.get(
            _partner_address(connection.partner1),
            [],
        )
        right = address_map.get(
            _partner_address(connection.partner2),
            [],
        )

        # Ambiguous or unresolved source addresses are not guessed.
        if len(left) != 1 or len(right) != 1:
            continue

        connection_type = (
            str(getattr(connection, "type", "explicit_connection"))
            .strip()
            .casefold()
            .replace(" ", "_")
        )

        connections.append(
            PdbDeclaredConnection(
                connection_type=connection_type,
                endpoint_a=left[0],
                endpoint_b=right[0],
            )
        )

    protein_secondary = tuple(
        connection
        for connection in connections
        if {
            connection.endpoint_a.entity_type,
            connection.endpoint_b.entity_type,
        }
        in (
            {"polymer", "nonpolymer"},
            {"polymer", "branched"},
        )
    )

    return PdbRelationshipEvidence(
        all_connections=tuple(connections),
        protein_secondary_connections=protein_secondary,
    )


__all__ = [
    "PdbConnectionEndpoint",
    "PdbDeclaredConnection",
    "PdbRelationshipEvidence",
    "declared_pdb_relationships",
]
