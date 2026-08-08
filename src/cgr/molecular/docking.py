"""Engine-neutral protein-ligand docking evidence."""

from __future__ import annotations

import math
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import CanonicalModel
from cgr.science.canonical import validate_identifier

_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)


class MolecularDockingPocket(CanonicalModel):
    pocket_identifier: str
    definition_method: Literal["explicit_center_box", "reference_ligand_box"]
    center_angstrom: tuple[float, float, float]
    size_angstrom: tuple[float, float, float]
    selected_residue_identifiers: tuple[str, ...] = ()

    @field_validator("pocket_identifier")
    @classmethod
    def identifier(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("center_angstrom", "size_angstrom")
    @classmethod
    def vector(cls, value: tuple[float, float, float]) -> tuple[float, float, float]:
        if any(not math.isfinite(item) for item in value):
            raise ValueError("Docking pocket vectors must be finite.")
        return tuple(float(item) for item in value)

    @model_validator(mode="after")
    def positive_box(self) -> Self:
        if any(item <= 0.0 or item > 126.0 for item in self.size_angstrom):
            raise ValueError("Docking box dimensions must be in (0, 126] Angstrom.")
        return self


class MolecularDockingPose(CanonicalModel):
    pose_identifier: str
    rank: int = Field(gt=0)
    vina_score_kilocalorie_per_mole: float
    intermolecular_score_kilocalorie_per_mole: float
    intramolecular_score_kilocalorie_per_mole: float
    torsional_score_kilocalorie_per_mole: float
    ligand_atom_serials: tuple[int, ...] = Field(min_length=1)
    coordinates_angstrom: tuple[tuple[float, float, float], ...] = Field(min_length=1)

    @field_validator("pose_identifier")
    @classmethod
    def identifier(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator(
        "vina_score_kilocalorie_per_mole",
        "intermolecular_score_kilocalorie_per_mole",
        "intramolecular_score_kilocalorie_per_mole",
        "torsional_score_kilocalorie_per_mole",
    )
    @classmethod
    def score(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Docking scores must be finite.")
        return float(value)

    @model_validator(mode="after")
    def aligned_atoms(self) -> Self:
        if len(self.ligand_atom_serials) != len(self.coordinates_angstrom):
            raise ValueError("Docking atom identities and coordinates must align.")
        if len(set(self.ligand_atom_serials)) != len(self.ligand_atom_serials):
            raise ValueError("Docking ligand atom serials must be unique.")
        if any(not all(math.isfinite(x) for x in xyz) for xyz in self.coordinates_angstrom):
            raise ValueError("Docking pose coordinates must be finite.")
        return self


class MolecularDockingResult(CanonicalModel):
    schema_version: CapabilityVersion
    result_identifier: str
    receptor_artifact_identifier: str
    ligand_artifact_identifier: str
    candidate_identifier: str
    engine_identifier: Literal["autodock_vina"] = "autodock_vina"
    engine_version: str
    scoring_function: Literal["vina"] = "vina"
    random_seed: int = Field(gt=0)
    exhaustiveness: int = Field(gt=0)
    pocket: MolecularDockingPocket
    poses: tuple[MolecularDockingPose, ...] = Field(min_length=1)
    score_semantics: Literal[
        "autodock_vina_empirical_pose_score_not_binding_free_energy"
    ] = "autodock_vina_empirical_pose_score_not_binding_free_energy"
    binding_free_energy_calculated: Literal[False] = False
    receptor_preparation_method: str
    ligand_preparation_method: str

    @field_validator("schema_version")
    @classmethod
    def version(cls, value: CapabilityVersion) -> CapabilityVersion:
        if value != _SCHEMA_VERSION:
            raise ValueError("Docking schema version must be 1.0.0.")
        return value

    @field_validator(
        "result_identifier", "receptor_artifact_identifier", "ligand_artifact_identifier",
        "candidate_identifier", "receptor_preparation_method", "ligand_preparation_method",
    )
    @classmethod
    def identifier(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("poses")
    @classmethod
    def ranked(cls, value: tuple[MolecularDockingPose, ...]) -> tuple[MolecularDockingPose, ...]:
        if [pose.rank for pose in value] != list(range(1, len(value) + 1)):
            raise ValueError("Docking poses must have contiguous ranks from one.")
        if list(value) != sorted(value, key=lambda pose: pose.vina_score_kilocalorie_per_mole):
            raise ValueError("Docking poses must be sorted by Vina score.")
        return value
