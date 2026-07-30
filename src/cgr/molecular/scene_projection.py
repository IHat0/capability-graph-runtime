"""Read-only projection of artifact-backed molecular scenes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from cgr.science import ArtifactReference

from .artifact_repository import (
    MolecularArtifactRepository,
    MolecularArtifactRepositoryError,
)
from .contracts import (
    MolecularComponent,
    MolecularProject,
    MolecularRegion,
    MolecularSceneManifest,
    MolecularSelection,
    MolecularStructure,
)
from .topology import MolecularTopologyIndex

NativeStructureFormat = Literal["pdb", "mmcif", "mol", "sdf", "xyz"]
CoordinateUnit = Literal["angstrom", "bohr", "nanometer"]


class MolecularSceneProjectionError(ValueError):
    """Base error for controlled molecular scene projection failures."""


class MolecularSceneProjectionNotFoundError(MolecularSceneProjectionError):
    """Raised when a requested scene or projected structure is absent."""


class MolecularSceneProjectionIntegrityError(MolecularSceneProjectionError):
    """Raised when projection evidence is missing, corrupt, or inconsistent."""


@dataclass(frozen=True, slots=True)
class ProjectedMolecularStructure:
    """Immutable structure metadata backed by separately retrievable bytes."""

    structure: MolecularStructure
    topology: MolecularTopologyIndex
    source_artifact: ArtifactReference
    topology_artifact: ArtifactReference
    native_format: NativeStructureFormat
    coordinate_unit: CoordinateUnit
    atom_identifiers: tuple[str, ...]

    @property
    def structure_identifier(self) -> str:
        """Return the stable projected structure identifier."""

        return self.structure.structure_identifier


@dataclass(frozen=True, slots=True)
class MolecularSceneProjection:
    """Immutable metadata projection for one molecular scene manifest."""

    project_identifier: str
    scene: MolecularSceneManifest
    structures: tuple[ProjectedMolecularStructure, ...]
    primary_structure_identifier: str
    components: tuple[MolecularComponent, ...]
    selections: tuple[MolecularSelection, ...]
    regions: tuple[MolecularRegion, ...]
    artifact_references: tuple[ArtifactReference, ...]

    @property
    def primary_structure(self) -> ProjectedMolecularStructure:
        """Return the explicitly declared primary projected structure."""

        for projected_structure in self.structures:
            if (
                projected_structure.structure_identifier
                == self.primary_structure_identifier
            ):
                return projected_structure
        raise MolecularSceneProjectionIntegrityError(
            "The primary projected structure is unavailable."
        )


def project_molecular_scene(
    *,
    project: MolecularProject,
    scene_identifier: str,
    repository: MolecularArtifactRepository,
) -> MolecularSceneProjection:
    """Project one declared molecular scene without reading source bytes."""

    scene = _resolve_scene(project, scene_identifier)
    structures_by_identifier = _index_structures(project.structures)
    selections_by_identifier = _index_selections(project.selections)
    regions_by_identifier = _index_regions(project.regions)
    scene_structure_identifiers = set(scene.structure_identifiers)

    structures: list[MolecularStructure] = []
    for structure_identifier in scene.structure_identifiers:
        structure = structures_by_identifier.get(structure_identifier)
        if structure is None:
            raise MolecularSceneProjectionIntegrityError(
                "A scene structure reference does not resolve."
            )
        if structure.topology_artifact is None:
            raise MolecularSceneProjectionIntegrityError(
                "A projected structure has no topology artifact."
            )
        structures.append(structure)

    effective_references = _effective_artifact_references(
        structures=tuple(structures),
        scene=scene,
    )
    projected_structures = tuple(
        _project_structure(structure=structure, repository=repository)
        for structure in structures
    )

    selections: list[MolecularSelection] = []
    for selection_identifier in scene.selection_identifiers:
        selection = selections_by_identifier.get(selection_identifier)
        if selection is None:
            raise MolecularSceneProjectionIntegrityError(
                "A scene selection reference does not resolve."
            )
        if selection.structure_identifier not in scene_structure_identifiers:
            raise MolecularSceneProjectionIntegrityError(
                "A scene selection belongs to an undeclared structure."
            )
        selections.append(selection)

    regions: list[MolecularRegion] = []
    for region_identifier in scene.region_identifiers:
        region = regions_by_identifier.get(region_identifier)
        if region is None:
            raise MolecularSceneProjectionIntegrityError(
                "A scene region reference does not resolve."
            )
        if region.structure_identifier not in scene_structure_identifiers:
            raise MolecularSceneProjectionIntegrityError(
                "A scene region belongs to an undeclared structure."
            )
        if region.selection_identifier not in selections_by_identifier:
            raise MolecularSceneProjectionIntegrityError(
                "A scene region selection does not resolve."
            )
        regions.append(region)

    if scene.project_identifier != project.project_identifier:
        raise MolecularSceneProjectionIntegrityError(
            "The scene project identity is inconsistent."
        )
    if scene.primary_structure_identifier not in {
        structure.structure_identifier for structure in structures
    }:
        raise MolecularSceneProjectionIntegrityError(
            "The primary scene structure does not resolve."
        )

    components = tuple(
        component
        for component in project.components
        if component.structure_identifier in scene_structure_identifiers
    )
    return MolecularSceneProjection(
        project_identifier=project.project_identifier,
        scene=scene,
        structures=projected_structures,
        primary_structure_identifier=scene.primary_structure_identifier,
        components=components,
        selections=tuple(selections),
        regions=tuple(regions),
        artifact_references=effective_references,
    )


def read_projected_structure_bytes(
    *,
    projection: MolecularSceneProjection,
    structure_identifier: str,
    repository: MolecularArtifactRepository,
) -> bytes:
    """Read exact native source bytes for one structure in a projection."""

    projected_structure = next(
        (
            candidate
            for candidate in projection.structures
            if candidate.structure_identifier == structure_identifier
        ),
        None,
    )
    if projected_structure is None:
        raise MolecularSceneProjectionNotFoundError(
            "Projected molecular structure not found."
        )
    try:
        return repository.read(projected_structure.source_artifact)
    except MolecularArtifactRepositoryError:
        raise MolecularSceneProjectionIntegrityError(
            "Projected structure bytes are unavailable or invalid."
        ) from None


def _resolve_scene(
    project: MolecularProject,
    scene_identifier: str,
) -> MolecularSceneManifest:
    for scene in project.scenes:
        if scene.scene_identifier == scene_identifier:
            return scene
    raise MolecularSceneProjectionNotFoundError("Molecular scene not found.")


def _index_structures(
    structures: tuple[MolecularStructure, ...],
) -> dict[str, MolecularStructure]:
    indexed = {
        structure.structure_identifier: structure for structure in structures
    }
    if len(indexed) != len(structures):
        raise MolecularSceneProjectionIntegrityError(
            "Molecular structure identities are ambiguous."
        )
    return indexed


def _index_selections(
    selections: tuple[MolecularSelection, ...],
) -> dict[str, MolecularSelection]:
    indexed = {
        selection.selection_identifier: selection for selection in selections
    }
    if len(indexed) != len(selections):
        raise MolecularSceneProjectionIntegrityError(
            "Molecular selection identities are ambiguous."
        )
    return indexed


def _index_regions(
    regions: tuple[MolecularRegion, ...],
) -> dict[str, MolecularRegion]:
    indexed = {region.region_identifier: region for region in regions}
    if len(indexed) != len(regions):
        raise MolecularSceneProjectionIntegrityError(
            "Molecular region identities are ambiguous."
        )
    return indexed


def _effective_artifact_references(
    *,
    structures: tuple[MolecularStructure, ...],
    scene: MolecularSceneManifest,
) -> tuple[ArtifactReference, ...]:
    ordered: list[ArtifactReference] = []
    references_by_pointer: dict[str, ArtifactReference] = {}
    candidates: list[ArtifactReference] = []
    for structure in structures:
        if structure.topology_artifact is None:
            raise MolecularSceneProjectionIntegrityError(
                "A projected structure has no topology artifact."
            )
        candidates.extend(
            (structure.structure_artifact, structure.topology_artifact)
        )
    candidates.extend(scene.artifact_references)

    for reference in candidates:
        pointer_identity = reference.pointer.to_canonical_json()
        existing = references_by_pointer.get(pointer_identity)
        if existing is None:
            references_by_pointer[pointer_identity] = reference
            ordered.append(reference)
        elif existing != reference:
            raise MolecularSceneProjectionIntegrityError(
                "Artifact pointer evidence conflicts within the scene."
            )
    return tuple(ordered)


def _project_structure(
    *,
    structure: MolecularStructure,
    repository: MolecularArtifactRepository,
) -> ProjectedMolecularStructure:
    topology_artifact = structure.topology_artifact
    if topology_artifact is None:
        raise MolecularSceneProjectionIntegrityError(
            "A projected structure has no topology artifact."
        )
    if structure.structure_artifact.artifact_type != "molecular_structure":
        raise MolecularSceneProjectionIntegrityError(
            "A projected source artifact has an invalid type."
        )
    if topology_artifact.artifact_type != "molecular_topology":
        raise MolecularSceneProjectionIntegrityError(
            "A projected topology artifact has an invalid type."
        )
    try:
        topology_payload = repository.read(topology_artifact)
    except MolecularArtifactRepositoryError:
        raise MolecularSceneProjectionIntegrityError(
            "Projected topology evidence is unavailable or invalid."
        ) from None
    topology = _decode_topology(topology_payload)
    _validate_topology(
        structure=structure,
        topology=topology,
        topology_artifact=topology_artifact,
    )
    return ProjectedMolecularStructure(
        structure=structure,
        topology=topology,
        source_artifact=structure.structure_artifact,
        topology_artifact=topology_artifact,
        native_format=topology.structure_format,
        coordinate_unit=topology.coordinate_unit,
        atom_identifiers=tuple(
            atom.atom_identifier for atom in topology.atoms
        ),
    )


def _decode_topology(payload: bytes) -> MolecularTopologyIndex:
    try:
        decoded = payload.decode("utf-8", errors="strict")
        value = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise MolecularSceneProjectionIntegrityError(
            "Projected topology evidence is malformed."
        ) from None
    if not isinstance(value, dict):
        raise MolecularSceneProjectionIntegrityError(
            "Projected topology evidence must be a JSON object."
        )
    try:
        topology = MolecularTopologyIndex.model_validate(value)
    except ValidationError:
        raise MolecularSceneProjectionIntegrityError(
            "Projected topology evidence failed validation."
        ) from None
    if payload != topology.to_canonical_json().encode("utf-8"):
        raise MolecularSceneProjectionIntegrityError(
            "Projected topology evidence is not canonical."
        )
    return topology


def _validate_topology(
    *,
    structure: MolecularStructure,
    topology: MolecularTopologyIndex,
    topology_artifact: ArtifactReference,
) -> None:
    if topology_artifact != structure.topology_artifact:
        raise MolecularSceneProjectionIntegrityError(
            "The projected topology artifact identity is inconsistent."
        )
    if topology.structure_identifier != structure.structure_identifier:
        raise MolecularSceneProjectionIntegrityError(
            "The topology structure identity is inconsistent."
        )
    if topology.source_structure != structure.structure_artifact.pointer:
        raise MolecularSceneProjectionIntegrityError(
            "The topology source identity is inconsistent."
        )
    _validate_declared_structure_format(
        reference=structure.structure_artifact,
        actual_format=topology.structure_format,
    )
    _validate_declared_structure_format(
        reference=topology_artifact,
        actual_format=topology.structure_format,
    )
    if topology.coordinate_unit != structure.coordinate_unit:
        raise MolecularSceneProjectionIntegrityError(
            "The topology coordinate unit is inconsistent."
        )
    if len(topology.atoms) != structure.atom_count:
        raise MolecularSceneProjectionIntegrityError(
            "The topology atom count is inconsistent."
        )
    if len(topology.bonds) != structure.bond_count:
        raise MolecularSceneProjectionIntegrityError(
            "The topology bond count is inconsistent."
        )
    if len(topology.residues) != structure.residue_count:
        raise MolecularSceneProjectionIntegrityError(
            "The topology residue count is inconsistent."
        )
    if len(topology.chains) != structure.chain_count:
        raise MolecularSceneProjectionIntegrityError(
            "The topology chain count is inconsistent."
        )
    if topology.model_count != structure.model_count:
        raise MolecularSceneProjectionIntegrityError(
            "The topology model count is inconsistent."
        )
    if topology.frame_count != structure.frame_count:
        raise MolecularSceneProjectionIntegrityError(
            "The topology frame count is inconsistent."
        )


def _validate_declared_structure_format(
    *,
    reference: ArtifactReference,
    actual_format: NativeStructureFormat,
) -> None:
    declared_format = reference.metadata.get("structure_format")
    if declared_format is not None and declared_format != actual_format:
        raise MolecularSceneProjectionIntegrityError(
            "The topology structure format is inconsistent."
        )
