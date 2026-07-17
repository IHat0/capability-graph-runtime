"""Stable scientific identities alongside complete run-specific evidence."""

from __future__ import annotations

import math
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.science import CanonicalModel, ScientificVerificationResult
from cgr.science.canonical import (
    BoundedMetadata,
    validate_bounded_metadata,
    validate_identifier,
    validate_sha256,
)

from .results import EnergyResult, VQEResult

RESULT_IDENTITY_SCHEMA = "cgr.scientific-result-identity/1.0.0"
RESULT_ARTIFACT_SCHEMA = "cgr.quantum-result-artifact/2.0.0"
OUTCOME_IDENTITY_SCHEMA = "cgr.authorized-scientific-outcome/1.0.0"


class EnvironmentCompatibilityIdentity(CanonicalModel):
    """Stable numerical/runtime compatibility projection, without host occurrence data."""

    schema_version: str = "cgr.environment-compatibility/1.0.0"
    python_version: str
    python_implementation: str
    os_family: str
    architecture: str
    direct_package_versions: dict[str, str]
    transitive_package_versions: dict[str, str]
    dependency_lock_sha256: str
    container_image_identifier: str
    blas_information: dict[str, str]
    thread_limits: dict[str, str]
    deterministic_seed_policy: str
    network_disabled: bool

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != "cgr.environment-compatibility/1.0.0":
            raise ValueError("Unsupported environment compatibility schema.")
        return value

    @field_validator("dependency_lock_sha256")
    @classmethod
    def valid_lock_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("os_family", "architecture", "deterministic_seed_policy")
    @classmethod
    def valid_identifiers(cls, value: str) -> str:
        return validate_identifier(value)


class ScientificResultIdentity(CanonicalModel):
    """Semantic identity of one scientific answer, excluding its execution occurrence."""

    schema_version: str = RESULT_IDENTITY_SCHEMA
    result_kind: str
    experiment_sha256: str
    molecular_structure_sha256: str
    electronic_problem_sha256: str
    active_space_sha256: str
    fermionic_hamiltonian_sha256: str
    qubit_hamiltonian_sha256: str
    solver_identifier: str
    solver_version: str
    solver_configuration_sha256: str
    environment_compatibility_sha256: str
    energy_unit: Literal["hartree"] = "hartree"
    electronic_energy: float
    nuclear_repulsion_energy: float
    total_energy: float
    particle_count: float | None = None
    number_of_spatial_orbitals: int = Field(gt=0)
    number_of_spin_orbitals: int = Field(gt=0)
    number_of_qubits: int = Field(gt=0)
    converged: bool
    auxiliary_scientific_values: BoundedMetadata = Field(default_factory=dict)
    particle_sector_filtering_policy: str | None = None
    estimator_type: str | None = None
    initial_point_sha256: str | None = None
    optimized_parameters_sha256: str | None = None
    optimization_trace_sha256: str | None = None
    ansatz_sha256: str | None = None
    ansatz_identifier: str | None = None
    initial_state_identifier: str | None = None
    mapper_identifier: str
    verification_policy_sha256: str

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != RESULT_IDENTITY_SCHEMA:
            raise ValueError("Unsupported scientific-result identity schema.")
        return value

    @field_validator("result_kind", "solver_identifier", "solver_version", "mapper_identifier")
    @classmethod
    def valid_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator(
        "particle_sector_filtering_policy",
        "estimator_type",
        "ansatz_identifier",
        "initial_state_identifier",
    )
    @classmethod
    def valid_optional_identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @field_validator(
        "experiment_sha256",
        "molecular_structure_sha256",
        "electronic_problem_sha256",
        "active_space_sha256",
        "fermionic_hamiltonian_sha256",
        "qubit_hamiltonian_sha256",
        "solver_configuration_sha256",
        "environment_compatibility_sha256",
        "initial_point_sha256",
        "optimized_parameters_sha256",
        "optimization_trace_sha256",
        "ansatz_sha256",
        "verification_policy_sha256",
    )
    @classmethod
    def valid_hashes(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("electronic_energy", "nuclear_repulsion_energy", "total_energy", "particle_count")
    @classmethod
    def finite_values(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("Scientific identity values must be finite.")
        return value

    @field_validator("auxiliary_scientific_values")
    @classmethod
    def valid_auxiliary_values(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)

    @model_validator(mode="after")
    def validate_result_kind_fields(self) -> Self:
        if self.result_kind == "exact_ground_state":
            if self.particle_sector_filtering_policy is None:
                raise ValueError("Exact identities require a particle-sector filtering policy.")
        elif self.result_kind == "vqe_ground_state":
            required = (
                self.estimator_type,
                self.initial_point_sha256,
                self.optimized_parameters_sha256,
                self.optimization_trace_sha256,
                self.ansatz_sha256,
                self.ansatz_identifier,
                self.initial_state_identifier,
            )
            if any(value is None for value in required):
                raise ValueError("VQE identities require estimator, ansatz, point, and trace identities.")
        return self


class ScientificResultArtifact(CanonicalModel):
    """Hardened result wrapper retaining the complete timed execution payload."""

    schema_version: str = RESULT_ARTIFACT_SCHEMA
    scientific_identity: ScientificResultIdentity
    scientific_result_sha256: str
    execution_result: VQEResult | EnergyResult
    execution_metadata: BoundedMetadata = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != RESULT_ARTIFACT_SCHEMA:
            raise ValueError("Unsupported hardened result artifact schema.")
        return value

    @field_validator("scientific_result_sha256")
    @classmethod
    def valid_result_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("execution_metadata")
    @classmethod
    def valid_execution_metadata(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)

    @model_validator(mode="after")
    def recompute_identity(self) -> Self:
        identity = self.scientific_identity
        result = self.execution_result
        if self.scientific_result_sha256 != identity.fingerprint:
            raise ValueError("Scientific-result SHA-256 does not match its canonical projection.")
        if identity.result_kind == "vqe_ground_state" and not isinstance(result, VQEResult):
            raise ValueError("VQE identity requires a VQEResult execution payload.")
        comparisons = (
            (identity.solver_identifier, result.solver_identifier),
            (identity.solver_version, result.solver_version),
            (identity.qubit_hamiltonian_sha256, result.hamiltonian_sha256),
            (identity.electronic_energy, result.electronic_energy_hartree),
            (identity.nuclear_repulsion_energy, result.nuclear_repulsion_energy_hartree),
            (identity.total_energy, result.total_energy_hartree),
            (identity.particle_count, result.particle_count),
            (identity.number_of_spatial_orbitals, result.number_of_spatial_orbitals),
            (identity.number_of_spin_orbitals, result.number_of_spin_orbitals),
            (identity.number_of_qubits, result.number_of_qubits),
        )
        if any(left != right for left, right in comparisons):
            raise ValueError("Scientific-result projection disagrees with execution values.")
        if isinstance(result, VQEResult):
            if identity.optimized_parameters_sha256 != result.optimized_parameters_sha256:
                raise ValueError("Optimized-parameter identity mismatch.")
            if identity.converged != result.converged:
                raise ValueError("VQE convergence identity mismatch.")
        elif identity.converged != result.completed:
            raise ValueError("Exact completion identity mismatch.")
        return self


class ResultArtifactInspection(CanonicalModel):
    """Explicit compatibility result for historical v1 flat result payloads."""

    hardened: bool
    legacy: bool
    reason: str | None = None
    artifact: ScientificResultArtifact | None = None


def inspect_result_artifact(value: dict[str, Any]) -> ResultArtifactInspection:
    if value.get("schema_version") != RESULT_ARTIFACT_SCHEMA:
        return ResultArtifactInspection(
            hardened=False,
            legacy=True,
            reason="legacy_result_missing_recomputed_scientific_identity",
        )
    artifact = ScientificResultArtifact.model_validate(value)
    return ResultArtifactInspection(hardened=True, legacy=False, artifact=artifact)


class VerifierOutcomeIdentity(CanonicalModel):
    verifier_identifier: str
    outcome: str
    blocking_finding_codes: tuple[str, ...] = ()

    @field_validator("verifier_identifier", "outcome")
    @classmethod
    def valid_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("blocking_finding_codes")
    @classmethod
    def order_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(validate_identifier(item) for item in value)))


class AuthorizedScientificOutcome(CanonicalModel):
    """Stable identity of the complete scientific conclusion, independent of a run."""

    schema_version: str = OUTCOME_IDENTITY_SCHEMA
    experiment_sha256: str
    molecular_structure_sha256: str
    electronic_problem_sha256: str
    active_space_sha256: str
    fermionic_hamiltonian_sha256: str
    qubit_hamiltonian_sha256: str
    exact_scientific_result_sha256: str
    vqe_scientific_result_sha256: str
    exact_total_energy: float
    vqe_total_energy: float
    absolute_difference: float = Field(ge=0)
    tolerance: float = Field(gt=0)
    units: Literal["hartree"] = "hartree"
    comparison_passed: bool
    verification_policy_sha256: str
    verifier_outcomes: tuple[VerifierOutcomeIdentity, ...]
    authorization_decision: bool
    environment_compatibility_sha256: str
    compatibility_warnings_sha256: str
    compatibility_status: Literal["clean", "warnings", "blocking"]

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != OUTCOME_IDENTITY_SCHEMA:
            raise ValueError("Unsupported scientific-outcome identity schema.")
        return value

    @field_validator(
        "experiment_sha256",
        "molecular_structure_sha256",
        "electronic_problem_sha256",
        "active_space_sha256",
        "fermionic_hamiltonian_sha256",
        "qubit_hamiltonian_sha256",
        "exact_scientific_result_sha256",
        "vqe_scientific_result_sha256",
        "verification_policy_sha256",
        "environment_compatibility_sha256",
        "compatibility_warnings_sha256",
    )
    @classmethod
    def valid_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("exact_total_energy", "vqe_total_energy", "absolute_difference", "tolerance")
    @classmethod
    def finite_values(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("Scientific outcome values must be finite.")
        return value

    @field_validator("verifier_outcomes")
    @classmethod
    def order_outcomes(
        cls, value: tuple[VerifierOutcomeIdentity, ...]
    ) -> tuple[VerifierOutcomeIdentity, ...]:
        identifiers = [item.verifier_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Scientific outcome verifier identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.verifier_identifier))

    @model_validator(mode="after")
    def agreement_and_authorization_are_consistent(self) -> Self:
        difference = abs(self.vqe_total_energy - self.exact_total_energy)
        if not math.isclose(difference, self.absolute_difference, abs_tol=1e-15):
            raise ValueError("Scientific outcome difference was not recomputed from energies.")
        if self.comparison_passed != (difference <= self.tolerance):
            raise ValueError("Scientific outcome comparison decision is inconsistent.")
        passed = all(item.outcome == "passed" for item in self.verifier_outcomes)
        expected_authorization = (
            self.comparison_passed and passed and self.compatibility_status != "blocking"
        )
        if self.authorization_decision != expected_authorization:
            raise ValueError("Scientific outcome authorization is inconsistent.")
        return self


def verifier_outcome_identities(
    results: tuple[ScientificVerificationResult, ...],
) -> tuple[VerifierOutcomeIdentity, ...]:
    return tuple(
        VerifierOutcomeIdentity(
            verifier_identifier=result.verifier_identifier,
            outcome=result.outcome.value,
            blocking_finding_codes=tuple(
                finding.code for finding in result.findings if finding.blocking
            ),
        )
        for result in results
    )
