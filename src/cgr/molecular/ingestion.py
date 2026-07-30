"""Deterministic ingestion of molecular structure bytes into topology artifacts."""

from __future__ import annotations

import hashlib
import io
import math
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import (
    ArtifactLineageEdge,
    ArtifactReference,
    CreationProvenance,
)
from cgr.science.canonical import validate_identifier

from .contracts import MolecularStructure
from .topology import (
    MolecularTopologyAtom,
    MolecularTopologyBond,
    MolecularTopologyChain,
    MolecularTopologyIndex,
    MolecularTopologyResidue,
)

StructureFormat = Literal["pdb", "mmcif", "mol", "sdf", "xyz"]
CoordinateUnit = Literal["angstrom", "bohr", "nanometer"]

_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_STRUCTURE_FORMATS = frozenset({"pdb", "mmcif", "mol", "sdf", "xyz"})
_COORDINATE_UNITS = frozenset({"angstrom", "bohr", "nanometer"})
_ANGSTROM_FORMATS = frozenset({"pdb", "mmcif", "mol", "sdf"})
_SAFE_IDENTIFIER_PART = re.compile(r"[^A-Za-z0-9._:/-]+")


class StructureIngestionError(ValueError):
    """Controlled failure while validating or parsing molecular structure bytes."""


@dataclass(frozen=True, slots=True)
class StructureIngestionResult:
    """Immutable structure, topology artifact, payload, and lineage result."""

    structure: MolecularStructure
    topology: MolecularTopologyIndex
    topology_artifact: ArtifactReference
    topology_payload: bytes
    lineage_edge: ArtifactLineageEdge


@dataclass(frozen=True, slots=True)
class _ParsedAtom:
    element_symbol: str
    atom_name: str | None
    chain_key: str | None
    residue_key: str | None
    formal_charge: int | None
    source_serial: str | None
    hierarchy_identity: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ParsedBond:
    atom_index_a: int
    atom_index_b: int
    bond_order: float | None
    bond_type: str


@dataclass(frozen=True, slots=True)
class _ParsedResidue:
    residue_key: str
    residue_name: str
    chain_key: str | None
    atom_indices: tuple[int, ...]
    source_sequence_identifier: str | None


@dataclass(frozen=True, slots=True)
class _ParsedChain:
    chain_key: str
    atom_indices: tuple[int, ...]
    residue_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ParsedStructure:
    parser_identifier: str
    parser_version: str
    atoms: tuple[_ParsedAtom, ...]
    bonds: tuple[_ParsedBond, ...]
    residues: tuple[_ParsedResidue, ...]
    chains: tuple[_ParsedChain, ...]
    model_count: int
    frame_count: int


def _safe_identifier(value: str, *, fallback: str) -> str:
    normalized = _SAFE_IDENTIFIER_PART.sub("-", value.strip()).strip("-._:/")
    if not normalized or not normalized[0].isalnum():
        normalized = fallback
    return normalized[:128]


def _stable_identifier(
    kind: str,
    source_index: int,
    identity: tuple[str, ...],
) -> str:
    digest_input = "\x1f".join((kind, str(source_index), *identity))
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:20]
    return f"{kind}-{source_index}-{digest}"


def _finite_coordinates(values: tuple[float, float, float]) -> None:
    if not all(math.isfinite(value) for value in values):
        raise StructureIngestionError("Structure coordinates must be finite.")


def _strict_text(source_bytes: bytes, structure_format: str) -> str:
    try:
        return source_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise StructureIngestionError(
            f"{structure_format} structure bytes must be valid UTF-8 text."
        ) from None


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace("\x00", "")
    return text if text and text not in {".", "?"} else None


def _parse_xyz(text: str) -> _ParsedStructure:
    lines = text.splitlines()
    if len(lines) < 2:
        raise StructureIngestionError(
            "XYZ input must contain an atom count and comment line."
        )
    try:
        atom_count = int(lines[0].strip())
    except ValueError:
        raise StructureIngestionError(
            "XYZ atom count must be a positive integer."
        ) from None
    if atom_count <= 0:
        raise StructureIngestionError("XYZ atom count must be a positive integer.")

    records = lines[2 : 2 + atom_count]
    if len(records) != atom_count:
        raise StructureIngestionError(
            "XYZ input does not contain the declared number of atom records."
        )
    if any(line.strip() for line in lines[2 + atom_count :]):
        raise StructureIngestionError("XYZ input contains trailing nonblank records.")

    atoms: list[_ParsedAtom] = []
    for atom_index, record in enumerate(records):
        fields = record.split()
        if len(fields) != 4:
            raise StructureIngestionError(
                "Each XYZ atom record must contain an element and three coordinates."
            )
        try:
            coordinates = (float(fields[1]), float(fields[2]), float(fields[3]))
        except ValueError:
            raise StructureIngestionError(
                "XYZ coordinates must be finite numeric values."
            ) from None
        _finite_coordinates(coordinates)
        atoms.append(
            _ParsedAtom(
                element_symbol=fields[0],
                atom_name=None,
                chain_key=None,
                residue_key=None,
                formal_charge=None,
                source_serial=str(atom_index + 1),
                hierarchy_identity=(fields[0], str(atom_index)),
            )
        )

    return _ParsedStructure(
        parser_identifier="pulsate.xyz",
        parser_version="1.0.0",
        atoms=tuple(atoms),
        bonds=(),
        residues=(),
        chains=(),
        model_count=1,
        frame_count=1,
    )


def _gemmi_atom_identity(
    *,
    chain_name: str,
    residue_name: str,
    sequence_identifier: str,
    atom_name: str,
    alternate_location: str,
    occurrence: int,
    element_symbol: str,
) -> tuple[str, ...]:
    return (
        chain_name,
        residue_name,
        sequence_identifier,
        atom_name,
        alternate_location,
        str(occurrence),
        element_symbol,
    )


def _gemmi_model_signature(model: object) -> tuple[tuple[str, ...], ...]:
    signature: list[tuple[str, ...]] = []
    occurrence_counts: dict[tuple[str, str, str, str, str], int] = {}
    for chain in model:
        chain_name = _normalize_optional_text(getattr(chain, "name", None)) or "chain"
        for residue in chain:
            residue_name = (
                _normalize_optional_text(getattr(residue, "name", None)) or "residue"
            )
            sequence_identifier = (
                _normalize_optional_text(getattr(residue, "seqid", None)) or "0"
            )
            for atom in residue:
                atom_name = (
                    _normalize_optional_text(getattr(atom, "name", None)) or "atom"
                )
                alternate_location = (
                    _normalize_optional_text(getattr(atom, "altloc", None)) or ""
                )
                element = getattr(atom, "element", None)
                element_symbol = (
                    _normalize_optional_text(getattr(element, "name", None)) or "X"
                )
                occurrence_key = (
                    chain_name,
                    residue_name,
                    sequence_identifier,
                    atom_name,
                    alternate_location,
                )
                occurrence = occurrence_counts.get(occurrence_key, 0)
                occurrence_counts[occurrence_key] = occurrence + 1
                signature.append(
                    _gemmi_atom_identity(
                        chain_name=chain_name,
                        residue_name=residue_name,
                        sequence_identifier=sequence_identifier,
                        atom_name=atom_name,
                        alternate_location=alternate_location,
                        occurrence=occurrence,
                        element_symbol=element_symbol,
                    )
                )
                position = atom.pos
                _finite_coordinates(
                    (float(position.x), float(position.y), float(position.z))
                )
    return tuple(signature)


def _gemmi_connection_key(partner: object) -> tuple[str, str, str, str, str]:
    residue_id = getattr(partner, "res_id", None)
    return (
        _normalize_optional_text(getattr(partner, "chain_name", None)) or "chain",
        _normalize_optional_text(getattr(residue_id, "name", None)) or "residue",
        _normalize_optional_text(getattr(residue_id, "seqid", None)) or "0",
        _normalize_optional_text(getattr(partner, "atom_name", None)) or "atom",
        _normalize_optional_text(getattr(partner, "altloc", None)) or "",
    )


def _parse_gemmi(text: str, structure_format: str) -> _ParsedStructure:
    try:
        import gemmi
    except ImportError:
        raise StructureIngestionError(
            f"Missing optional dependency 'gemmi' required for {structure_format}."
        ) from None

    try:
        if structure_format == "pdb":
            structure = gemmi.read_pdb_string(text)
        else:
            document = gemmi.cif.read_string(text)
            structure = gemmi.make_structure_from_block(document.sole_block())
        models = tuple(structure)
        if not models:
            raise StructureIngestionError(
                f"{structure_format} input contains no molecular models."
            )

        first_signature = _gemmi_model_signature(models[0])
        if not first_signature:
            raise StructureIngestionError(
                f"{structure_format} input contains no atoms."
            )
        for model in models[1:]:
            if _gemmi_model_signature(model) != first_signature:
                raise StructureIngestionError(
                    f"{structure_format} models do not share one ordered topology."
                )

        atoms: list[_ParsedAtom] = []
        residues: list[_ParsedResidue] = []
        chains: list[_ParsedChain] = []
        address_indices: dict[tuple[str, str, str, str, str], list[int]] = {}
        occurrence_counts: dict[tuple[str, str, str, str, str], int] = {}

        for chain_index, chain in enumerate(models[0]):
            chain_name = (
                _normalize_optional_text(getattr(chain, "name", None)) or "chain"
            )
            chain_key = f"chain-source-{chain_index}"
            chain_atom_indices: list[int] = []
            chain_residue_keys: list[str] = []
            for residue_index, residue in enumerate(chain):
                residue_name = (
                    _normalize_optional_text(getattr(residue, "name", None))
                    or "residue"
                )
                sequence_identifier = (
                    _normalize_optional_text(getattr(residue, "seqid", None)) or "0"
                )
                residue_key = f"{chain_key}.residue-source-{residue_index}"
                residue_atom_indices: list[int] = []
                for atom in residue:
                    atom_index = len(atoms)
                    atom_name = (
                        _normalize_optional_text(getattr(atom, "name", None))
                        or "atom"
                    )
                    alternate_location = (
                        _normalize_optional_text(getattr(atom, "altloc", None)) or ""
                    )
                    element = getattr(atom, "element", None)
                    element_symbol = (
                        _normalize_optional_text(getattr(element, "name", None)) or "X"
                    )
                    address_key = (
                        chain_name,
                        residue_name,
                        sequence_identifier,
                        atom_name,
                        alternate_location,
                    )
                    occurrence = occurrence_counts.get(address_key, 0)
                    occurrence_counts[address_key] = occurrence + 1
                    address_indices.setdefault(address_key, []).append(atom_index)
                    serial_value = getattr(atom, "serial", None)
                    source_serial = (
                        str(serial_value)
                        if isinstance(serial_value, int) and serial_value > 0
                        else None
                    )
                    charge_value = getattr(atom, "charge", None)
                    formal_charge = (
                        int(charge_value)
                        if isinstance(charge_value, int)
                        else None
                    )
                    atoms.append(
                        _ParsedAtom(
                            element_symbol=element_symbol,
                            atom_name=atom_name,
                            chain_key=chain_key,
                            residue_key=residue_key,
                            formal_charge=formal_charge,
                            source_serial=source_serial,
                            hierarchy_identity=_gemmi_atom_identity(
                                chain_name=chain_name,
                                residue_name=residue_name,
                                sequence_identifier=sequence_identifier,
                                atom_name=atom_name,
                                alternate_location=alternate_location,
                                occurrence=occurrence,
                                element_symbol=element_symbol,
                            ),
                        )
                    )
                    chain_atom_indices.append(atom_index)
                    residue_atom_indices.append(atom_index)
                if residue_atom_indices:
                    residues.append(
                        _ParsedResidue(
                            residue_key=residue_key,
                            residue_name=residue_name,
                            chain_key=chain_key,
                            atom_indices=tuple(residue_atom_indices),
                            source_sequence_identifier=sequence_identifier,
                        )
                    )
                    chain_residue_keys.append(residue_key)
            if chain_atom_indices:
                chains.append(
                    _ParsedChain(
                        chain_key=chain_key,
                        atom_indices=tuple(chain_atom_indices),
                        residue_keys=tuple(chain_residue_keys),
                    )
                )

        bonds: list[_ParsedBond] = []
        seen_endpoints: set[tuple[int, int]] = set()
        for connection in getattr(structure, "connections", ()):
            endpoint_a = address_indices.get(
                _gemmi_connection_key(connection.partner1),
                [],
            )
            endpoint_b = address_indices.get(
                _gemmi_connection_key(connection.partner2),
                [],
            )
            if len(endpoint_a) != 1 or len(endpoint_b) != 1:
                continue
            ordered = tuple(sorted((endpoint_a[0], endpoint_b[0])))
            if ordered[0] == ordered[1] or ordered in seen_endpoints:
                continue
            seen_endpoints.add(ordered)
            connection_type = _safe_identifier(
                str(getattr(connection, "type", "explicit_connection")).lower(),
                fallback="explicit_connection",
            )
            bonds.append(
                _ParsedBond(
                    atom_index_a=ordered[0],
                    atom_index_b=ordered[1],
                    bond_order=None,
                    bond_type=connection_type,
                )
            )

        return _ParsedStructure(
            parser_identifier="gemmi",
            parser_version=_safe_identifier(
                str(getattr(gemmi, "__version__", "0.7.5")),
                fallback="0.7.5",
            ),
            atoms=tuple(atoms),
            bonds=tuple(bonds),
            residues=tuple(residues),
            chains=tuple(chains),
            model_count=len(models),
            frame_count=len(models),
        )
    except StructureIngestionError:
        raise
    except Exception:
        raise StructureIngestionError(
            f"Failed to parse {structure_format} structure bytes with Gemmi."
        ) from None


def _parsed_rdkit_molecule(molecule: object, parser_version: str) -> _ParsedStructure:
    conformer_count = int(molecule.GetNumConformers())
    if conformer_count <= 0:
        raise StructureIngestionError(
            "RDKit structure input must contain atom coordinates."
        )
    for conformer in molecule.GetConformers():
        for atom_index in range(int(molecule.GetNumAtoms())):
            position = conformer.GetAtomPosition(atom_index)
            _finite_coordinates(
                (float(position.x), float(position.y), float(position.z))
            )

    atoms = tuple(
        _ParsedAtom(
            element_symbol=atom.GetSymbol(),
            atom_name=None,
            chain_key=None,
            residue_key=None,
            formal_charge=int(atom.GetFormalCharge()),
            source_serial=str(atom.GetIdx() + 1),
            hierarchy_identity=(
                atom.GetSymbol(),
                str(atom.GetIdx()),
            ),
        )
        for atom in molecule.GetAtoms()
    )
    bonds = tuple(
        _ParsedBond(
            atom_index_a=int(bond.GetBeginAtomIdx()),
            atom_index_b=int(bond.GetEndAtomIdx()),
            bond_order=float(bond.GetBondTypeAsDouble()),
            bond_type=_safe_identifier(
                str(bond.GetBondType()).lower(),
                fallback="explicit",
            ),
        )
        for bond in molecule.GetBonds()
    )
    return _ParsedStructure(
        parser_identifier="rdkit",
        parser_version=parser_version,
        atoms=atoms,
        bonds=bonds,
        residues=(),
        chains=(),
        model_count=1,
        frame_count=conformer_count,
    )


def _parse_rdkit(
    source_bytes: bytes,
    text: str,
    structure_format: str,
) -> _ParsedStructure:
    try:
        from rdkit import Chem, rdBase
    except ImportError:
        raise StructureIngestionError(
            f"Missing optional dependency 'rdkit' required for {structure_format}."
        ) from None

    parser_version = _safe_identifier(
        str(getattr(rdBase, "rdkitVersion", "unknown")),
        fallback="unknown",
    )
    try:
        if structure_format == "mol":
            molecule = Chem.MolFromMolBlock(
                text,
                sanitize=True,
                removeHs=False,
                strictParsing=True,
            )
            if molecule is None:
                raise StructureIngestionError("MOL input is invalid.")
        else:
            supplier = Chem.ForwardSDMolSupplier(
                io.BytesIO(source_bytes),
                sanitize=True,
                removeHs=False,
                strictParsing=True,
            )
            records = list(supplier)
            if not records:
                raise StructureIngestionError("SDF input contains no molecule record.")
            if any(record is None for record in records):
                raise StructureIngestionError("SDF input contains an invalid record.")
            if len(records) != 1:
                raise StructureIngestionError(
                    "SDF ingestion requires exactly one valid molecule record."
                )
            molecule = records[0]
        return _parsed_rdkit_molecule(molecule, parser_version)
    except StructureIngestionError:
        raise
    except Exception:
        raise StructureIngestionError(
            f"Failed to parse {structure_format} structure bytes with RDKit."
        ) from None


def _topology_from_parsed(
    *,
    parsed: _ParsedStructure,
    source_reference: ArtifactReference,
    structure_identifier: str,
    structure_format: StructureFormat,
    coordinate_unit: CoordinateUnit,
) -> MolecularTopologyIndex:
    chain_identifiers = {
        chain.chain_key: _stable_identifier(
            "chain",
            index,
            (chain.chain_key,),
        )
        for index, chain in enumerate(parsed.chains)
    }
    residue_identifiers = {
        residue.residue_key: _stable_identifier(
            "residue",
            index,
            (
                residue.chain_key or "",
                residue.residue_key,
                residue.residue_name,
                residue.source_sequence_identifier or "",
            ),
        )
        for index, residue in enumerate(parsed.residues)
    }
    atom_identifiers = tuple(
        _stable_identifier("atom", index, atom.hierarchy_identity)
        for index, atom in enumerate(parsed.atoms)
    )

    atoms = tuple(
        MolecularTopologyAtom(
            atom_identifier=atom_identifiers[index],
            atom_index=index,
            element_symbol=atom.element_symbol,
            atom_name=atom.atom_name,
            model_index=0,
            chain_identifier=(
                chain_identifiers[atom.chain_key]
                if atom.chain_key is not None
                else None
            ),
            residue_identifier=(
                residue_identifiers[atom.residue_key]
                if atom.residue_key is not None
                else None
            ),
            component_identifier=None,
            formal_charge=atom.formal_charge,
            source_serial=atom.source_serial,
        )
        for index, atom in enumerate(parsed.atoms)
    )
    bonds = tuple(
        MolecularTopologyBond(
            bond_identifier=_stable_identifier(
                "bond",
                index,
                (
                    atom_identifiers[bond.atom_index_a],
                    atom_identifiers[bond.atom_index_b],
                    bond.bond_type,
                ),
            ),
            atom_identifier_a=atom_identifiers[bond.atom_index_a],
            atom_identifier_b=atom_identifiers[bond.atom_index_b],
            bond_order=bond.bond_order,
            bond_type=bond.bond_type,
        )
        for index, bond in enumerate(parsed.bonds)
    )
    residues = tuple(
        MolecularTopologyResidue(
            residue_identifier=residue_identifiers[residue.residue_key],
            residue_name=residue.residue_name,
            chain_identifier=(
                chain_identifiers[residue.chain_key]
                if residue.chain_key is not None
                else None
            ),
            atom_identifiers=tuple(
                atom_identifiers[index] for index in residue.atom_indices
            ),
            source_sequence_identifier=residue.source_sequence_identifier,
        )
        for residue in parsed.residues
    )
    chains = tuple(
        MolecularTopologyChain(
            chain_identifier=chain_identifiers[chain.chain_key],
            atom_identifiers=tuple(
                atom_identifiers[index] for index in chain.atom_indices
            ),
            residue_identifiers=tuple(
                residue_identifiers[key] for key in chain.residue_keys
            ),
        )
        for chain in parsed.chains
    )
    return MolecularTopologyIndex(
        schema_version=_SCHEMA_VERSION,
        structure_identifier=structure_identifier,
        source_structure=source_reference.pointer,
        structure_format=structure_format,
        parser_identifier=parsed.parser_identifier,
        parser_version=parsed.parser_version,
        atoms=atoms,
        bonds=bonds,
        residues=residues,
        chains=chains,
        model_count=parsed.model_count,
        frame_count=parsed.frame_count,
        coordinate_unit=coordinate_unit,
    )


def ingest_structure_bytes(
    *,
    source_bytes: bytes,
    source_reference: ArtifactReference,
    structure_identifier: str,
    system_identifier: str,
    structure_format: StructureFormat,
    coordinate_unit: CoordinateUnit,
    provenance: CreationProvenance,
) -> StructureIngestionResult:
    """Validate exact source bytes and create deterministic topology evidence."""

    if not source_bytes:
        raise StructureIngestionError("Molecular structure input cannot be empty.")
    if source_reference.artifact_type != "molecular_structure":
        raise StructureIngestionError(
            "Source artifact type must be molecular_structure."
        )
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if source_sha256 != source_reference.content_sha256:
        raise StructureIngestionError(
            "Source structure bytes do not match the declared SHA-256."
        )
    if (
        source_reference.byte_size is not None
        and source_reference.byte_size != len(source_bytes)
    ):
        raise StructureIngestionError(
            "Source structure byte size does not match the artifact reference."
        )
    if structure_format not in _STRUCTURE_FORMATS:
        raise StructureIngestionError("Unsupported molecular structure format.")
    if coordinate_unit not in _COORDINATE_UNITS:
        raise StructureIngestionError("Unsupported molecular coordinate unit.")
    if (
        structure_format in _ANGSTROM_FORMATS
        and coordinate_unit != "angstrom"
    ):
        raise StructureIngestionError(
            f"{structure_format} coordinates must be declared in angstrom."
        )
    try:
        validate_identifier(structure_identifier, label="structure identifier")
        validate_identifier(system_identifier, label="system identifier")
    except ValueError as exc:
        raise StructureIngestionError(str(exc)) from None

    text = _strict_text(source_bytes, structure_format)
    if structure_format == "xyz":
        parsed = _parse_xyz(text)
    elif structure_format in {"pdb", "mmcif"}:
        parsed = _parse_gemmi(text, structure_format)
    else:
        parsed = _parse_rdkit(source_bytes, text, structure_format)

    try:
        topology = _topology_from_parsed(
            parsed=parsed,
            source_reference=source_reference,
            structure_identifier=structure_identifier,
            structure_format=structure_format,
            coordinate_unit=coordinate_unit,
        )
        topology_payload = topology.to_canonical_json().encode("utf-8")
        topology_sha256 = hashlib.sha256(topology_payload).hexdigest()
        topology_artifact = ArtifactReference(
            artifact_identifier=(
                "molecular-topology-"
                + hashlib.sha256(
                    structure_identifier.encode("utf-8")
                ).hexdigest()[:32]
            ),
            schema_version=_SCHEMA_VERSION,
            artifact_type="molecular_topology",
            media_type="application/vnd.pulsate.molecular-topology+json",
            content_sha256=topology_sha256,
            byte_size=len(topology_payload),
            storage_location=None,
            metadata={
                "structure_format": structure_format,
                "parser_identifier": topology.parser_identifier,
                "parser_version": topology.parser_version,
            },
            provenance=provenance,
            parents=(source_reference.pointer,),
        )
        lineage_edge = ArtifactLineageEdge(
            source=source_reference.pointer,
            destination=topology_artifact.pointer,
            relationship_type="parsed_into",
            producing_capability="molecular.structure_ingestion",
            producing_capability_version=_SCHEMA_VERSION,
        )
        structure = MolecularStructure(
            structure_identifier=structure_identifier,
            system_identifier=system_identifier,
            coordinate_unit=coordinate_unit,
            structure_artifact=source_reference,
            topology_artifact=topology_artifact,
            atom_count=len(topology.atoms),
            bond_count=len(topology.bonds),
            residue_count=len(topology.residues),
            chain_count=len(topology.chains),
            model_count=topology.model_count,
            frame_count=topology.frame_count,
        )
    except (ValidationError, ValueError, IndexError, KeyError) as exc:
        raise StructureIngestionError(
            "Parsed structure topology failed controlled validation."
        ) from exc

    return StructureIngestionResult(
        structure=structure,
        topology=topology,
        topology_artifact=topology_artifact,
        topology_payload=topology_payload,
        lineage_edge=lineage_edge,
    )
