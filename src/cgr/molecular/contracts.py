"""Canonical contracts for artifact-backed molecular projects and scenes.

A molecular system describes the complete declared system and its structures.
Selections and regions identify logical structural subsets without loading
topology artifacts. Electronic active spaces are separate, later scientific
contracts and are not represented by these structural declarations.
"""

from __future__ import annotations

from typing import Literal, Self, TypeVar

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import (
    ArtifactLineageGraph,
    ArtifactReference,
    CanonicalModel,
    CreationProvenance,
)
from cgr.science.canonical import validate_identifier

_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_LABEL_MAXIMUM_LENGTH = 512

_Model = TypeVar("_Model", bound=CanonicalModel)


def _validate_schema_version(value: CapabilityVersion) -> CapabilityVersion:
    if value != _SCHEMA_VERSION:
        raise ValueError("Molecular schema version must be exactly 1.0.0.")
    return value


def _validate_label(value: str | None) -> str | None:
    if value is not None and not value.strip():
        raise ValueError("Molecular labels cannot be blank.")
    return value


def _ordered_identifiers(
    value: tuple[str, ...],
    *,
    label: str,
) -> tuple[str, ...]:
    validated = tuple(validate_identifier(item, label=label) for item in value)
    if len(validated) != len(set(validated)):
        raise ValueError(f"{label.capitalize()}s must be unique.")
    return tuple(sorted(validated))


def _ordered_models(
    value: tuple[_Model, ...],
    *,
    label: str,
    key: str,
) -> tuple[_Model, ...]:
    identifiers = [getattr(item, key) for item in value]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"{label.capitalize()} identifiers must be unique.")
    return tuple(sorted(value, key=lambda item: getattr(item, key)))


def _ordered_unique_values(
    value: tuple[_Model, ...],
    *,
    label: str,
) -> tuple[_Model, ...]:
    unique: list[_Model] = []
    for item in value:
        if any(item == existing for existing in unique):
            raise ValueError(f"{label.capitalize()} values must be unique.")
        unique.append(item)
    return tuple(sorted(unique, key=lambda item: item.fingerprint))


class MolecularMemberReference(CanonicalModel):
    """Logical structural member reference that does not load topology bytes."""

    member_kind: Literal["atom", "residue", "chain", "component"]
    member_identifier: str

    @field_validator("member_identifier")
    @classmethod
    def validate_member_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="molecular member identifier")


class MolecularStructure(CanonicalModel):
    """Artifact-backed coordinate and topology metadata for one structure."""

    structure_identifier: str
    system_identifier: str
    coordinate_unit: Literal["angstrom", "bohr", "nanometer"]
    structure_artifact: ArtifactReference
    topology_artifact: ArtifactReference | None = None
    atom_count: int = Field(gt=0)
    bond_count: int = Field(ge=0)
    residue_count: int = Field(ge=0)
    chain_count: int = Field(ge=0)
    model_count: int = Field(gt=0)
    frame_count: int = Field(gt=0)

    @field_validator("structure_identifier", "system_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)


class MolecularSystem(CanonicalModel):
    """Complete molecular system with one or more artifact-backed structures."""

    system_identifier: str
    label: str | None = Field(default=None, max_length=_LABEL_MAXIMUM_LENGTH)
    structure_identifiers: tuple[str, ...] = Field(min_length=1)
    default_structure_identifier: str

    @field_validator("system_identifier", "default_structure_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str | None) -> str | None:
        return _validate_label(value)

    @field_validator("structure_identifiers")
    @classmethod
    def validate_structure_identifiers(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="structure identifier")

    @model_validator(mode="after")
    def validate_default_structure(self) -> Self:
        if self.default_structure_identifier not in self.structure_identifiers:
            raise ValueError(
                "The default structure must be included in the molecular system."
            )
        return self


class MolecularComponent(CanonicalModel):
    """Open-typed structural component within one molecular structure."""

    component_identifier: str
    structure_identifier: str
    component_type: str
    label: str | None = Field(default=None, max_length=_LABEL_MAXIMUM_LENGTH)
    members: tuple[MolecularMemberReference, ...] = Field(min_length=1)

    @field_validator(
        "component_identifier",
        "structure_identifier",
        "component_type",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str | None) -> str | None:
        return _validate_label(value)

    @field_validator("members")
    @classmethod
    def validate_members(
        cls,
        value: tuple[MolecularMemberReference, ...],
    ) -> tuple[MolecularMemberReference, ...]:
        return _ordered_unique_values(value, label="component member")


class MolecularSelection(CanonicalModel):
    """Logical structural selection, distinct from an electronic active space."""

    selection_identifier: str
    structure_identifier: str
    label: str | None = Field(default=None, max_length=_LABEL_MAXIMUM_LENGTH)
    selection_source: str
    members: tuple[MolecularMemberReference, ...] = Field(min_length=1)

    @field_validator(
        "selection_identifier",
        "structure_identifier",
        "selection_source",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str | None) -> str | None:
        return _validate_label(value)

    @field_validator("members")
    @classmethod
    def validate_members(
        cls,
        value: tuple[MolecularMemberReference, ...],
    ) -> tuple[MolecularMemberReference, ...]:
        return _ordered_unique_values(value, label="selection member")


class MolecularRegion(CanonicalModel):
    """Named structural region composed from a selection and optional parents."""

    region_identifier: str
    structure_identifier: str
    region_type: str
    selection_identifier: str
    parent_region_identifiers: tuple[str, ...] = ()

    @field_validator(
        "region_identifier",
        "structure_identifier",
        "region_type",
        "selection_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("parent_region_identifiers")
    @classmethod
    def validate_parent_identifiers(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="parent region identifier")

    @model_validator(mode="after")
    def reject_direct_self_parent(self) -> Self:
        if self.region_identifier in self.parent_region_identifiers:
            raise ValueError("A molecular region cannot be its own parent.")
        return self


class MolecularSceneManifest(CanonicalModel):
    """Artifact-aware scene declaration over project structural subsets."""

    scene_identifier: str
    schema_version: CapabilityVersion
    project_identifier: str
    structure_identifiers: tuple[str, ...] = Field(min_length=1)
    primary_structure_identifier: str
    selection_identifiers: tuple[str, ...] = ()
    region_identifiers: tuple[str, ...] = ()
    artifact_references: tuple[ArtifactReference, ...] = ()

    @field_validator(
        "scene_identifier",
        "project_identifier",
        "primary_structure_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "structure_identifiers",
        "selection_identifiers",
        "region_identifiers",
    )
    @classmethod
    def validate_identifier_collections(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="scene reference identifier")

    @field_validator("artifact_references")
    @classmethod
    def validate_artifact_references(
        cls,
        value: tuple[ArtifactReference, ...],
    ) -> tuple[ArtifactReference, ...]:
        return _ordered_unique_values(value, label="scene artifact reference")

    @model_validator(mode="after")
    def validate_primary_structure(self) -> Self:
        if self.primary_structure_identifier not in self.structure_identifiers:
            raise ValueError(
                "The primary structure must be included in the molecular scene."
            )
        return self


class MolecularProject(CanonicalModel):
    """Complete molecular project with structural subsets and artifact lineage."""

    project_identifier: str
    schema_version: CapabilityVersion
    systems: tuple[MolecularSystem, ...] = Field(min_length=1)
    structures: tuple[MolecularStructure, ...] = Field(min_length=1)
    components: tuple[MolecularComponent, ...] = ()
    selections: tuple[MolecularSelection, ...] = ()
    regions: tuple[MolecularRegion, ...] = ()
    scenes: tuple[MolecularSceneManifest, ...] = ()
    provenance: CreationProvenance
    lineage: ArtifactLineageGraph = Field(default_factory=ArtifactLineageGraph)

    @field_validator("project_identifier")
    @classmethod
    def validate_project_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="molecular project identifier")

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator("systems")
    @classmethod
    def validate_systems(
        cls,
        value: tuple[MolecularSystem, ...],
    ) -> tuple[MolecularSystem, ...]:
        return _ordered_models(
            value,
            label="molecular system",
            key="system_identifier",
        )

    @field_validator("structures")
    @classmethod
    def validate_structures(
        cls,
        value: tuple[MolecularStructure, ...],
    ) -> tuple[MolecularStructure, ...]:
        return _ordered_models(
            value,
            label="molecular structure",
            key="structure_identifier",
        )

    @field_validator("components")
    @classmethod
    def validate_components(
        cls,
        value: tuple[MolecularComponent, ...],
    ) -> tuple[MolecularComponent, ...]:
        return _ordered_models(
            value,
            label="molecular component",
            key="component_identifier",
        )

    @field_validator("selections")
    @classmethod
    def validate_selections(
        cls,
        value: tuple[MolecularSelection, ...],
    ) -> tuple[MolecularSelection, ...]:
        return _ordered_models(
            value,
            label="molecular selection",
            key="selection_identifier",
        )

    @field_validator("regions")
    @classmethod
    def validate_regions(
        cls,
        value: tuple[MolecularRegion, ...],
    ) -> tuple[MolecularRegion, ...]:
        return _ordered_models(
            value,
            label="molecular region",
            key="region_identifier",
        )

    @field_validator("scenes")
    @classmethod
    def validate_scenes(
        cls,
        value: tuple[MolecularSceneManifest, ...],
    ) -> tuple[MolecularSceneManifest, ...]:
        return _ordered_models(
            value,
            label="molecular scene",
            key="scene_identifier",
        )

    @model_validator(mode="after")
    def validate_project_references(self) -> Self:
        systems = {item.system_identifier: item for item in self.systems}
        structures = {
            item.structure_identifier: item for item in self.structures
        }
        components = {
            item.component_identifier: item for item in self.components
        }
        selections = {
            item.selection_identifier: item for item in self.selections
        }
        regions = {item.region_identifier: item for item in self.regions}

        for structure in self.structures:
            if structure.system_identifier not in systems:
                raise ValueError(
                    "Every molecular structure must reference an existing system."
                )

        for system in self.systems:
            for structure_identifier in system.structure_identifiers:
                structure = structures.get(structure_identifier)
                if structure is None:
                    raise ValueError(
                        "Every system structure identifier must resolve."
                    )
                if structure.system_identifier != system.system_identifier:
                    raise ValueError(
                        "System structures must declare the same system identifier."
                    )

        for component in components.values():
            if component.structure_identifier not in structures:
                raise ValueError(
                    "Every molecular component must reference an existing structure."
                )

        for selection in selections.values():
            if selection.structure_identifier not in structures:
                raise ValueError(
                    "Every molecular selection must reference an existing structure."
                )

        for region in regions.values():
            if region.structure_identifier not in structures:
                raise ValueError(
                    "Every molecular region must reference an existing structure."
                )
            selection = selections.get(region.selection_identifier)
            if selection is None:
                raise ValueError(
                    "Every molecular region selection must resolve."
                )
            if selection.structure_identifier != region.structure_identifier:
                raise ValueError(
                    "A region and its selection must belong to the same structure."
                )
            for parent_identifier in region.parent_region_identifiers:
                parent = regions.get(parent_identifier)
                if parent is None:
                    raise ValueError("Every parent molecular region must resolve.")
                if parent.structure_identifier != region.structure_identifier:
                    raise ValueError(
                        "Parent and child regions must belong to the same structure."
                    )

        self._validate_region_parent_graph(regions)

        for scene in self.scenes:
            if scene.project_identifier != self.project_identifier:
                raise ValueError(
                    "Every scene must reference its containing molecular project."
                )
            scene_structures = set(scene.structure_identifiers)
            if not scene_structures.issubset(structures):
                raise ValueError("Every scene structure must resolve.")
            for selection_identifier in scene.selection_identifiers:
                selection = selections.get(selection_identifier)
                if selection is None:
                    raise ValueError("Every scene selection must resolve.")
                if selection.structure_identifier not in scene_structures:
                    raise ValueError(
                        "Scene selections must belong to a scene structure."
                    )
            for region_identifier in scene.region_identifiers:
                region = regions.get(region_identifier)
                if region is None:
                    raise ValueError("Every scene region must resolve.")
                if region.structure_identifier not in scene_structures:
                    raise ValueError(
                        "Scene regions must belong to a scene structure."
                    )

        return self

    @staticmethod
    def _validate_region_parent_graph(
        regions: dict[str, MolecularRegion],
    ) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(region_identifier: str) -> None:
            if region_identifier in visiting:
                raise ValueError("Molecular region parent graph cannot contain cycles.")
            if region_identifier in visited:
                return
            visiting.add(region_identifier)
            for parent_identifier in regions[
                region_identifier
            ].parent_region_identifiers:
                visit(parent_identifier)
            visiting.remove(region_identifier)
            visited.add(region_identifier)

        for region_identifier in regions:
            visit(region_identifier)
