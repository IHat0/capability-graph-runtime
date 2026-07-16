"""Pure result records emitted by exact and independent VQE execution paths."""

from __future__ import annotations

import math
from typing import Self

from pydantic import Field, field_validator, model_validator

from cgr.science import CanonicalModel
from cgr.science.canonical import validate_identifier, validate_sha256


class EnergyResult(CanonicalModel):
    solver_identifier: str
    solver_version: str
    hamiltonian_sha256: str
    environment_sha256: str
    electronic_energy_hartree: float
    nuclear_repulsion_energy_hartree: float
    total_energy_hartree: float
    raw_eigenvalue_hartree: float
    particle_count: float | None = None
    particle_sector_filter_applied: bool | None = None
    completed: bool
    duration_seconds: float = Field(ge=0)

    @field_validator("solver_identifier", "solver_version")
    @classmethod
    def valid_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("hamiltonian_sha256", "environment_sha256")
    @classmethod
    def valid_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator(
        "electronic_energy_hartree",
        "nuclear_repulsion_energy_hartree",
        "total_energy_hartree",
        "raw_eigenvalue_hartree",
        "particle_count",
    )
    @classmethod
    def finite_values(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("Scientific result values must be finite.")
        return value

    @model_validator(mode="after")
    def energy_components_match(self) -> Self:
        total = self.electronic_energy_hartree + self.nuclear_repulsion_energy_hartree
        if not math.isclose(total, self.total_energy_hartree, abs_tol=1e-10):
            raise ValueError("Electronic and nuclear energy do not reproduce total energy.")
        return self

    def canonical_identity(self) -> dict[str, object]:
        """Durations are operational evidence, not computational-object identity."""
        data = self.model_dump(mode="json")
        data.pop("duration_seconds")
        return data


class OptimizationEvaluation(CanonicalModel):
    evaluation: int = Field(gt=0)
    energy_hartree: float
    parameter_fingerprint: str

    @field_validator("energy_hartree")
    @classmethod
    def finite_energy(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Optimization energies must be finite.")
        return value

    @field_validator("parameter_fingerprint")
    @classmethod
    def valid_parameter_hash(cls, value: str) -> str:
        return validate_sha256(value)


class VQEResult(EnergyResult):
    optimizer_identifier: str
    optimizer_status: str
    optimizer_evaluations: int = Field(gt=0)
    initial_point_sha256: str
    optimized_parameters_sha256: str
    ansatz_identifier: str
    initial_state_identifier: str
    converged: bool

    @field_validator(
        "optimizer_identifier", "optimizer_status", "ansatz_identifier", "initial_state_identifier"
    )
    @classmethod
    def valid_vqe_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("initial_point_sha256", "optimized_parameters_sha256")
    @classmethod
    def valid_parameter_hashes(cls, value: str) -> str:
        return validate_sha256(value)
