"""Ground acquired scientific records into deterministic structural evidence.

This layer preserves source-side PDB identities while reusing Pulsate's
existing molecular ingestion/topology machinery. It does not infer reaction
chemistry or choose a scientific target.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cgr.kernel.contracts import CapabilityVersion
from cgr.molecular import MolecularTopologyIndex, ingest_structure_bytes
from cgr.science import ArtifactReference, CreationProvenance

from .scientific_acquisition import AcquiredScientificInput


_VERSION = CapabilityVersion(major=1, minor=0, patch=0)

PdbRecordType = Literal["ATOM", "HETATM", "UNKNOWN"]
PdbEntityType = Literal[
    "polymer",
    "nonpolymer",
    "branched",
    "water",
    "unknown",
]


class ScientificResolutionError(ValueError):
    """Controlled failure while grounding an acquired scientific record."""


@dataclass(frozen=True, slots=True)
class PdbSourceComponent:
    """One source-side PDB residue/component with original structural labels."""

    record_type: PdbRecordType
    entity_type: PdbEntityType
    residue_name: str
    chain_label: str
    residue_sequence: str
    insertion_code: str | None
    atom_names: tuple[str, ...]
    element_symbols: tuple[str, ...]
    source_serials: tuple[str, ...]

    @property
    def atom_count(self) -> int:
        return len(self.atom_names)


@dataclass(frozen=True, slots=True)
class GroundedPdbInventory:
    """Deterministic inventory produced from one acquired PDB record."""

    source_identifier: str
    source_content_sha256: str
    topology: MolecularTopologyIndex
    protein_residues: tuple[PdbSourceComponent, ...]
    secondary_components: tuple[PdbSourceComponent, ...]
    solvent_components: tuple[PdbSourceComponent, ...]
    ion_components: tuple[PdbSourceComponent, ...]
    unclassified_components: tuple[PdbSourceComponent, ...]


def _normalized_optional(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().replace("\x00", "")
    return normalized or None


def _source_components(payload: bytes) -> tuple[PdbSourceComponent, ...]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ScientificResolutionError(
            "Acquired PDB payload must be valid UTF-8 text."
        ) from None

    try:
        import gemmi
    except ImportError:
        raise ScientificResolutionError(
            "Gemmi is required for grounded PDB entity classification."
        ) from None

    try:
        structure = gemmi.read_pdb_string(text)
        structure.setup_entities()
        models = tuple(structure)
    except Exception:
        raise ScientificResolutionError(
            "Failed to classify the acquired PDB structure."
        ) from None

    if not models:
        raise ScientificResolutionError(
            "Acquired PDB payload contains no molecular models."
        )

    entity_names: list[tuple[object, PdbEntityType]] = [
        (gemmi.EntityType.Polymer, "polymer"),
        (gemmi.EntityType.NonPolymer, "nonpolymer"),
        (gemmi.EntityType.Water, "water"),
    ]
    branched = getattr(gemmi.EntityType, "Branched", None)
    if branched is not None:
        entity_names.append((branched, "branched"))

    def entity_name(value: object) -> PdbEntityType:
        for candidate, name in entity_names:
            if value == candidate:
                return name
        return "unknown"

    components: list[PdbSourceComponent] = []

    for chain in models[0]:
        chain_label = _normalized_optional(getattr(chain, "name", None)) or ""

        for residue in chain:
            atoms = tuple(residue)
            if not atoms:
                continue

            residue_name = (
                _normalized_optional(getattr(residue, "name", None)) or ""
            ).upper()
            if not residue_name:
                raise ScientificResolutionError(
                    "PDB residue contains no source residue name."
                )

            seqid = getattr(residue, "seqid", None)
            sequence_number = getattr(seqid, "num", None)
            if sequence_number is None:
                raise ScientificResolutionError(
                    "PDB residue contains no source sequence identity."
                )

            insertion_code = _normalized_optional(
                getattr(seqid, "icode", None)
            )

            het_flag = _normalized_optional(
                getattr(residue, "het_flag", None)
            )
            if het_flag == "A":
                record_type: PdbRecordType = "ATOM"
            elif het_flag == "H":
                record_type = "HETATM"
            else:
                record_type = "UNKNOWN"

            atom_names: list[str] = []
            element_symbols: list[str] = []
            source_serials: list[str] = []

            for atom in atoms:
                atom_name = _normalized_optional(
                    getattr(atom, "name", None)
                )
                if atom_name is None:
                    raise ScientificResolutionError(
                        "PDB atom contains no source atom name."
                    )

                element = getattr(atom, "element", None)
                element_symbol = _normalized_optional(
                    getattr(element, "name", None)
                )
                if element_symbol is None:
                    raise ScientificResolutionError(
                        "PDB atom contains no element identity."
                    )

                serial = getattr(atom, "serial", None)
                if not isinstance(serial, int) or serial <= 0:
                    raise ScientificResolutionError(
                        "PDB atom contains no positive source serial."
                    )

                atom_names.append(atom_name)
                element_symbols.append(element_symbol)
                source_serials.append(str(serial))

            components.append(
                PdbSourceComponent(
                    record_type=record_type,
                    entity_type=entity_name(
                        getattr(residue, "entity_type", None)
                    ),
                    residue_name=residue_name,
                    chain_label=chain_label,
                    residue_sequence=str(sequence_number),
                    insertion_code=insertion_code,
                    atom_names=tuple(atom_names),
                    element_symbols=tuple(element_symbols),
                    source_serials=tuple(source_serials),
                )
            )

    if not components:
        raise ScientificResolutionError(
            "Acquired PDB payload contains no structural components."
        )

    return tuple(components)


def _topology_for_acquired_pdb(
    acquired: AcquiredScientificInput,
) -> MolecularTopologyIndex:
    digest = acquired.content_sha256

    provenance = CreationProvenance(
        producer="pulsate.scientific-resolution",
        producer_version=_VERSION,
        execution_identifier=f"scientific-resolution-{digest[:24]}",
        source=acquired.source_kind,
    )
    source_reference = ArtifactReference(
        artifact_identifier=f"scientific-resolution-source-{digest[:32]}",
        schema_version=_VERSION,
        artifact_type="molecular_structure",
        media_type=acquired.media_type,
        content_sha256=digest,
        byte_size=len(acquired.payload),
        provenance=provenance,
        metadata=acquired.metadata,
    )

    result = ingest_structure_bytes(
        source_bytes=acquired.payload,
        source_reference=source_reference,
        structure_identifier=f"scientific-resolution-structure-{digest[:24]}",
        system_identifier=f"scientific-resolution-system-{digest[:24]}",
        structure_format="pdb",
        coordinate_unit="angstrom",
        provenance=provenance,
    )
    return result.topology


def ground_acquired_protein_pdb(
    acquired: AcquiredScientificInput,
) -> GroundedPdbInventory:
    """Ground one acquired protein PDB without making chemical assumptions."""

    if acquired.artifact_type != "protein_structure":
        raise ScientificResolutionError(
            "Protein PDB grounding requires a protein_structure acquisition."
        )
    if acquired.media_type != "chemical/x-pdb":
        raise ScientificResolutionError(
            "Protein PDB grounding requires chemical/x-pdb input."
        )

    topology = _topology_for_acquired_pdb(acquired)
    components = _source_components(acquired.payload)

    protein_residues = tuple(
        item for item in components
        if item.entity_type == "polymer"
    )

    solvent_components = tuple(
        item for item in components
        if item.entity_type == "water"
    )

    nonpolymer = tuple(
        item for item in components
        if item.entity_type in {"nonpolymer", "branched"}
    )

    ion_components = tuple(
        item for item in nonpolymer
        if item.atom_count == 1
    )

    secondary_components = tuple(
        item for item in nonpolymer
        if item.atom_count > 1
    )

    unclassified_components = tuple(
        item for item in components
        if item.entity_type == "unknown"
    )

    if not protein_residues:
        raise ScientificResolutionError(
            "Acquired protein PDB contains no classified polymer residues."
        )

    return GroundedPdbInventory(
        source_identifier=acquired.source_identifier,
        source_content_sha256=acquired.content_sha256,
        topology=topology,
        protein_residues=protein_residues,
        secondary_components=secondary_components,
        solvent_components=solvent_components,
        ion_components=ion_components,
        unclassified_components=unclassified_components,
    )


__all__ = [
    "GroundedPdbInventory",
    "PdbSourceComponent",
    "ScientificResolutionError",
    "ground_acquired_protein_pdb",
]


def polymer_only_protein_pdb(
    acquired: AcquiredScientificInput,
) -> AcquiredScientificInput:
    """Derive a protein-only PDB from grounded polymer entity evidence."""

    inventory = ground_acquired_protein_pdb(acquired)

    polymer_serials = {
        serial
        for component in inventory.protein_residues
        for serial in component.source_serials
    }

    if not polymer_serials:
        raise ScientificResolutionError(
            "Grounded PDB contains no polymer atoms."
        )

    try:
        text = acquired.payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ScientificResolutionError(
            "Acquired PDB payload must be valid UTF-8 text."
        ) from None

    selected_lines: list[str] = []

    for line in text.splitlines():
        if not line.startswith(("ATOM  ", "HETATM")):
            continue

        serial = line[6:11].strip() if len(line) >= 11 else ""

        if serial in polymer_serials:
            selected_lines.append(line)

    if len(selected_lines) != len(polymer_serials):
        raise ScientificResolutionError(
            "Polymer atom identities did not resolve completely in the source PDB."
        )

    payload = (
        "\n".join(selected_lines)
        + "\nEND\n"
    ).encode("utf-8")

    return AcquiredScientificInput(
        artifact_type="protein_structure",
        media_type="chemical/x-pdb",
        payload=payload,
        source_kind="grounded_polymer_only_pdb",
        source_identifier=acquired.source_identifier,
        source_url=acquired.source_url,
        generation_method=(
            "gemmi_entity_classification_polymer_only_v1"
        ),
    )


__all__ += [
    "polymer_only_protein_pdb",
]
