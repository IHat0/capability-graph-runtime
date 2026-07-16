"""Serializable molecular structure metadata and view contracts."""

from __future__ import annotations

from typing import Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion

from .artifacts import ArtifactReference
from .canonical import CanonicalModel, validate_identifier


class MolecularStructure(CanonicalModel):
    """Metadata for exact molecular structure bytes without parsing them."""

    structure_artifact: ArtifactReference
    structure_role: str
    structure_format: str
    coordinate_unit: str
    atom_count: int = Field(gt=0)
    residue_count: int | None = Field(default=None, ge=0)
    molecular_charge: int | None = None
    spin_multiplicity: int | None = Field(default=None, gt=0)
    source_database: str | None = None
    source_identifier: str | None = None
    parent_structure_artifact: ArtifactReference | None = None
    preparation_status: str

    @field_validator("structure_role", "coordinate_unit", "preparation_status")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("structure_format")
    @classmethod
    def validate_structure_format(cls, value: str) -> str:
        return validate_identifier(value, label="structure format")

    @field_validator("source_database", "source_identifier")
    @classmethod
    def validate_optional_identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @model_validator(mode="after")
    def validate_artifact_type(self) -> Self:
        if self.structure_artifact.artifact_type != "molecular_structure":
            raise ValueError("Molecular structures require a molecular_structure artifact.")
        if (
            self.parent_structure_artifact is not None
            and self.parent_structure_artifact.artifact_type != "molecular_structure"
        ):
            raise ValueError("Parent structures require a molecular_structure artifact.")
        return self


class MolecularRepresentation(CanonicalModel):
    """Scientifically meaningful representation of one exact structure."""

    representation_identifier: str
    structure_artifact_identifier: str
    representation_type: str
    selection_identifier: str | None = None
    visible: bool = True

    @field_validator(
        "representation_identifier",
        "structure_artifact_identifier",
        "representation_type",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("selection_identifier")
    @classmethod
    def validate_selection_identifier(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None


class MolecularSelection(CanonicalModel):
    """Stable atom/residue selection bound to one structure artifact."""

    selection_identifier: str
    structure_artifact_identifier: str
    atom_indices: tuple[int, ...] = ()
    residue_identifiers: tuple[str, ...] = ()

    @field_validator("selection_identifier", "structure_artifact_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("atom_indices")
    @classmethod
    def validate_atom_indices(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(index < 0 for index in value):
            raise ValueError("Atom indices must be non-negative.")
        return tuple(sorted(set(value)))

    @field_validator("residue_identifiers")
    @classmethod
    def validate_residue_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(validate_identifier(item) for item in value)))

    @model_validator(mode="after")
    def require_structural_reference(self) -> Self:
        if not self.atom_indices and not self.residue_identifiers:
            raise ValueError("Molecular selections require atom or residue references.")
        return self


class MolecularAtomReference(CanonicalModel):
    """One stable atom reference used by a measurement."""

    structure_artifact_identifier: str
    atom_index: int = Field(ge=0)

    @field_validator("structure_artifact_identifier")
    @classmethod
    def validate_structure_identifier(cls, value: str) -> str:
        return validate_identifier(value)


class MolecularResidueReference(CanonicalModel):
    """One stable residue reference bound to an exact structure artifact."""

    structure_artifact_identifier: str
    residue_identifier: str

    @field_validator("structure_artifact_identifier", "residue_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)


class MolecularMeasurement(CanonicalModel):
    """A molecular measurement over explicit structural atom references."""

    measurement_identifier: str
    measurement_type: str
    atoms: tuple[MolecularAtomReference, ...]
    coordinate_unit: str
    calculated_value: float | None = None

    @field_validator("measurement_identifier", "measurement_type", "coordinate_unit")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @model_validator(mode="after")
    def validate_atom_count(self) -> Self:
        if self.measurement_type == "distance" and len(self.atoms) != 2:
            raise ValueError("Distance measurements require exactly two atom references.")
        if len(set(self.atoms)) != len(self.atoms):
            raise ValueError("Measurements cannot repeat an identical atom reference.")
        return self


class MolecularLabel(CanonicalModel):
    """Bounded label attached to a structural selection."""

    label_identifier: str
    selection_identifier: str
    text: str = Field(min_length=1, max_length=512)

    @field_validator("label_identifier", "selection_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)


class MolecularCameraState(CanonicalModel):
    """Optional deterministic camera state supplied by a viewer."""

    position: tuple[float, float, float]
    target: tuple[float, float, float]
    up: tuple[float, float, float]


class MolecularScene(CanonicalModel):
    """Serializable view bound to the exact structures used by computation."""

    scene_identifier: str
    schema_version: CapabilityVersion
    structures: tuple[ArtifactReference, ...]
    representations: tuple[MolecularRepresentation, ...] = ()
    selections: tuple[MolecularSelection, ...] = ()
    highlighted_atom_indices: tuple[MolecularAtomReference, ...] = ()
    highlighted_residues: tuple[MolecularResidueReference, ...] = ()
    quantum_region_selection_identifier: str | None = None
    measurements: tuple[MolecularMeasurement, ...] = ()
    labels: tuple[MolecularLabel, ...] = ()
    camera: MolecularCameraState | None = None

    @field_validator("scene_identifier")
    @classmethod
    def validate_scene_identifier(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("structures")
    @classmethod
    def order_structures(
        cls, value: tuple[ArtifactReference, ...]
    ) -> tuple[ArtifactReference, ...]:
        if not value:
            raise ValueError("A molecular scene requires at least one exact structure artifact.")
        identifiers = [item.artifact_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Scene structure artifact identifiers must be unique.")
        if any(item.artifact_type != "molecular_structure" for item in value):
            raise ValueError("Molecular scenes may only visualize molecular_structure artifacts.")
        return tuple(sorted(value, key=lambda item: item.artifact_identifier))

    @field_validator("representations")
    @classmethod
    def order_representations(
        cls, value: tuple[MolecularRepresentation, ...]
    ) -> tuple[MolecularRepresentation, ...]:
        identifiers = [item.representation_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Molecular representation identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.representation_identifier))

    @field_validator("selections")
    @classmethod
    def order_selections(
        cls, value: tuple[MolecularSelection, ...]
    ) -> tuple[MolecularSelection, ...]:
        identifiers = [item.selection_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Molecular selection identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.selection_identifier))

    @field_validator("highlighted_atom_indices")
    @classmethod
    def order_highlighted_atoms(
        cls, value: tuple[MolecularAtomReference, ...]
    ) -> tuple[MolecularAtomReference, ...]:
        return tuple(
            sorted(
                set(value),
                key=lambda item: (
                    item.structure_artifact_identifier,
                    item.atom_index,
                ),
            )
        )

    @field_validator("measurements")
    @classmethod
    def order_measurements(
        cls, value: tuple[MolecularMeasurement, ...]
    ) -> tuple[MolecularMeasurement, ...]:
        identifiers = [item.measurement_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Molecular measurement identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.measurement_identifier))

    @field_validator("labels")
    @classmethod
    def order_labels(cls, value: tuple[MolecularLabel, ...]) -> tuple[MolecularLabel, ...]:
        identifiers = [item.label_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Molecular label identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.label_identifier))

    @field_validator("highlighted_residues")
    @classmethod
    def order_highlighted_residues(
        cls, value: tuple[MolecularResidueReference, ...]
    ) -> tuple[MolecularResidueReference, ...]:
        return tuple(
            sorted(
                set(value),
                key=lambda item: (
                    item.structure_artifact_identifier,
                    item.residue_identifier,
                ),
            )
        )

    @field_validator("quantum_region_selection_identifier")
    @classmethod
    def validate_quantum_selection(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @model_validator(mode="after")
    def validate_exact_references(self) -> Self:
        structure_ids = {item.artifact_identifier for item in self.structures}
        selection_ids = {item.selection_identifier for item in self.selections}
        if any(
            item.structure_artifact_identifier not in structure_ids
            for item in self.representations
        ):
            raise ValueError("Every representation must reference a displayed structure artifact.")
        if any(
            item.structure_artifact_identifier not in structure_ids
            for item in self.selections
        ):
            raise ValueError("Every selection must reference a displayed structure artifact.")
        if any(
            item.structure_artifact_identifier not in structure_ids
            for item in self.highlighted_atom_indices
        ):
            raise ValueError("Highlighted atoms must reference displayed structure artifacts.")
        if any(
            item.structure_artifact_identifier not in structure_ids
            for item in self.highlighted_residues
        ):
            raise ValueError("Highlighted residues must reference displayed structure artifacts.")
        if any(
            atom.structure_artifact_identifier not in structure_ids
            for measurement in self.measurements
            for atom in measurement.atoms
        ):
            raise ValueError("Measurement atoms must reference displayed structure artifacts.")
        if any(
            item.selection_identifier not in selection_ids
            for item in self.representations
            if item.selection_identifier is not None
        ):
            raise ValueError("Representation selections must be declared in the scene.")
        if (
            self.quantum_region_selection_identifier is not None
            and self.quantum_region_selection_identifier not in selection_ids
        ):
            raise ValueError("The quantum region must reference a declared structural selection.")
        if any(label.selection_identifier not in selection_ids for label in self.labels):
            raise ValueError("Labels must reference declared structural selections.")
        return self
