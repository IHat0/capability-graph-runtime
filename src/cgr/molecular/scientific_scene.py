"""Stable molecular UI state for scientist-facing Phase 8 workflows."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import ArtifactReference, CanonicalModel
from cgr.science.canonical import validate_identifier

_VERSION = CapabilityVersion(major=1, minor=0, patch=0)


class ScientificSceneSelection(CanonicalModel):
    selection_identifier: str
    role: Literal[
        "protein", "ligand", "pocket", "selected_residues", "metal",
        "qm_region", "mm_region", "link_atoms", "reaction_coordinate",
        "docking_pose",
    ]
    structure_identifier: str
    atom_identifiers: tuple[str, ...] = ()
    residue_identifiers: tuple[str, ...] = ()

    @field_validator("selection_identifier", "structure_identifier")
    @classmethod
    def identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("atom_identifiers", "residue_identifiers")
    @classmethod
    def member_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(validate_identifier(item) for item in value)
        if len(normalized) != len(set(normalized)):
            raise ValueError("Scientific scene selection members must be unique.")
        return normalized

    @model_validator(mode="after")
    def members_required(self) -> Self:
        if not self.atom_identifiers and not self.residue_identifiers:
            raise ValueError("Scientific scene selections require resolved members.")
        return self


class ScientificSceneInteraction(CanonicalModel):
    interaction_identifier: str
    interaction_type: Literal[
        "covalent", "hydrogen_bond", "salt_bridge", "metal_coordination",
        "hydrophobic_contact", "pi_stacking", "reaction_bond",
    ]
    member_identifiers: tuple[str, ...] = Field(min_length=2, max_length=8)
    distance_angstrom: float | None = Field(default=None, gt=0)
    evidence_artifact_identifier: str

    @field_validator("interaction_identifier", "evidence_artifact_identifier")
    @classmethod
    def identifiers(cls, value: str) -> str:
        return validate_identifier(value)


class ScientificScenePose(CanonicalModel):
    pose_identifier: str
    candidate_identifier: str
    rank: int = Field(gt=0)
    vina_score_kcal_per_mol: float
    pose_artifact_identifier: str
    atom_mapping_embedded: bool

    @field_validator("pose_identifier", "candidate_identifier", "pose_artifact_identifier")
    @classmethod
    def identifiers(cls, value: str) -> str:
        return validate_identifier(value)


class ScientificSceneCandidateLineage(CanonicalModel):
    candidate_identifier: str
    parent_candidate_identifiers: tuple[str, ...] = ()
    generation: int = Field(ge=0)
    selected: bool = False
    status: Literal["generated", "invalid", "evaluated", "verified", "ranked"]

    @field_validator("candidate_identifier", "parent_candidate_identifiers")
    @classmethod
    def identifiers(cls, value):
        if isinstance(value, tuple):
            return tuple(validate_identifier(item) for item in value)
        return validate_identifier(value)


class MolecularScientificSceneState(CanonicalModel):
    """Backend contract consumable by Three.js or a molecular viewer."""

    schema_version: CapabilityVersion = _VERSION
    scene_identifier: str
    execution_identifier: str
    task_type: str
    structure_identifiers: tuple[str, ...] = Field(min_length=1)
    selections: tuple[ScientificSceneSelection, ...] = ()
    interactions: tuple[ScientificSceneInteraction, ...] = ()
    docking_poses: tuple[ScientificScenePose, ...] = ()
    candidate_lineage: tuple[ScientificSceneCandidateLineage, ...] = ()
    computational_state: Literal[
        "preparing", "running", "verifying", "replanning", "succeeded", "failed"
    ]
    active_capability: str | None = None
    result_state: Literal["pending", "verified", "inconclusive", "failed"]
    artifact_references: tuple[ArtifactReference, ...] = ()

    @field_validator("schema_version")
    @classmethod
    def version(cls, value: CapabilityVersion) -> CapabilityVersion:
        if value != _VERSION:
            raise ValueError("Scientific scene schema version must be 1.0.0.")
        return value

    @field_validator(
        "scene_identifier", "execution_identifier", "task_type", "active_capability"
    )
    @classmethod
    def identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @model_validator(mode="after")
    def identities_are_coherent(self) -> Self:
        selection_ids = [item.selection_identifier for item in self.selections]
        pose_ids = [item.pose_identifier for item in self.docking_poses]
        candidate_ids = [item.candidate_identifier for item in self.candidate_lineage]
        artifact_ids = [item.artifact_identifier for item in self.artifact_references]
        for values in (selection_ids, pose_ids, candidate_ids, artifact_ids):
            if len(values) != len(set(values)):
                raise ValueError("Scientific scene identities must be unique by category.")
        structures = set(self.structure_identifiers)
        if any(item.structure_identifier not in structures for item in self.selections):
            raise ValueError("Scientific scene selections must resolve a structure.")
        candidates = set(candidate_ids)
        if any(
            parent not in candidates
            for item in self.candidate_lineage
            for parent in item.parent_candidate_identifiers
        ):
            raise ValueError("Scientific scene candidate parents must resolve.")
        return self
