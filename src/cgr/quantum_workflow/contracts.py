"""Engine-neutral artifacts for Pulsate Phase 5 quantum workflows."""

from __future__ import annotations

import math
import re
from math import comb
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import CanonicalModel, sha256_fingerprint
from cgr.science.canonical import validate_identifier, validate_sha256

_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_FERMIONIC_TOKEN = re.compile(r"^[+-]_[0-9]+$")
_PAULI_LABEL = re.compile(r"^[IXYZ]+$")
_MAXIMUM_OPERATOR_TERMS = 2_000_000
_MAXIMUM_EXACT_QUBITS = 12
_MAXIMUM_VQE_PARAMETERS = 100_000
_MAXIMUM_OPTIMIZATION_EVALUATIONS = 100_000
_MAXIMUM_STATEVECTOR_QUBITS = 16


def _validate_schema_version(value: CapabilityVersion) -> CapabilityVersion:
    if value != _SCHEMA_VERSION:
        raise ValueError("Quantum-workflow schema version must be exactly 1.0.0.")
    return value


def _finite(value: float, *, label: str) -> float:
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{label} must be finite.")
    return normalized


def _canonical_hex(value: str, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must use hexadecimal floating-point text.")
    try:
        number = float.fromhex(value)
    except ValueError as exc:
        raise ValueError(
            f"{label} is not valid hexadecimal floating-point text."
        ) from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite.")
    if number == 0.0:
        number = 0.0
    return number.hex()


class QuantumComplexCoefficient(CanonicalModel):
    """One finite complex coefficient encoded without decimal-rounding ambiguity."""

    real_hex: str
    imaginary_hex: str

    @field_validator("real_hex")
    @classmethod
    def validate_real(cls, value: str) -> str:
        return _canonical_hex(value, label="Coefficient real component")

    @field_validator("imaginary_hex")
    @classmethod
    def validate_imaginary(cls, value: str) -> str:
        return _canonical_hex(value, label="Coefficient imaginary component")

    @property
    def value(self) -> complex:
        return complex(float.fromhex(self.real_hex), float.fromhex(self.imaginary_hex))

    @classmethod
    def from_value(cls, value: complex | float) -> QuantumComplexCoefficient:
        number = complex(value)
        real = 0.0 if number.real == 0.0 else float(number.real)
        imaginary = 0.0 if number.imag == 0.0 else float(number.imag)
        return cls(real_hex=real.hex(), imaginary_hex=imaginary.hex())


class FermionicHamiltonianTerm(CanonicalModel):
    """One normal-ordered Qiskit Nature fermionic term."""

    label: str = Field(max_length=4096)
    coefficient: QuantumComplexCoefficient

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Fermionic labels must be text.")
        if value == "":
            return value
        tokens = value.split()
        if not tokens or any(not _FERMIONIC_TOKEN.fullmatch(token) for token in tokens):
            raise ValueError("Fermionic labels must contain signed mode-index tokens.")
        return " ".join(tokens)


class PauliHamiltonianTerm(CanonicalModel):
    """One Pauli-string term in Qiskit's most-significant-qubit-left order."""

    label: str = Field(min_length=1, max_length=4096)
    coefficient: QuantumComplexCoefficient

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not _PAULI_LABEL.fullmatch(normalized):
            raise ValueError("Pauli labels may contain only I, X, Y and Z.")
        return normalized


def _ordered_fermionic_terms(
    value: tuple[FermionicHamiltonianTerm, ...],
) -> tuple[FermionicHamiltonianTerm, ...]:
    if not value:
        raise ValueError("A second-quantized Hamiltonian requires at least one term.")
    if len(value) > _MAXIMUM_OPERATOR_TERMS:
        raise ValueError("The second-quantized Hamiltonian exceeds the term bound.")
    labels = [term.label for term in value]
    if len(labels) != len(set(labels)):
        raise ValueError("Fermionic Hamiltonian labels must be unique.")
    return tuple(sorted(value, key=lambda term: term.label))


def _ordered_pauli_terms(
    value: tuple[PauliHamiltonianTerm, ...],
) -> tuple[PauliHamiltonianTerm, ...]:
    if not value:
        raise ValueError("A mapped Hamiltonian requires at least one Pauli term.")
    if len(value) > _MAXIMUM_OPERATOR_TERMS:
        raise ValueError("The mapped Hamiltonian exceeds the term bound.")
    labels = [term.label for term in value]
    if len(labels) != len(set(labels)):
        raise ValueError("Mapped Pauli labels must be unique.")
    return tuple(sorted(value, key=lambda term: term.label))


class SecondQuantizedHamiltonian(CanonicalModel):
    """Generic active-space fermionic Hamiltonian with an explicit energy constant."""

    schema_version: CapabilityVersion
    hamiltonian_identifier: str
    active_space_identifier: str
    active_space_sha256: str
    molecule_identifier: str
    active_electron_count: int = Field(gt=0)
    alpha_electron_count: int = Field(ge=0)
    beta_electron_count: int = Field(ge=0)
    active_spatial_orbital_count: int = Field(gt=0, le=32)
    active_spin_orbital_count: int = Field(gt=0, le=64)
    constant_energy_hartree: float
    register_length: int = Field(gt=0, le=64)
    coefficient_encoding: Literal["ieee754_binary64_hex"] = "ieee754_binary64_hex"
    integral_convention: Literal["chemist_pqrs"] = "chemist_pqrs"
    construction_method: Literal["active_space_integrals_second_quantization"] = (
        "active_space_integrals_second_quantization"
    )
    integral_symmetry_tolerance: float = Field(gt=0)
    terms: tuple[FermionicHamiltonianTerm, ...] = Field(min_length=1)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "hamiltonian_identifier",
        "active_space_identifier",
        "molecule_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="quantum Hamiltonian identifier")

    @field_validator("active_space_sha256")
    @classmethod
    def validate_active_space_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("constant_energy_hartree", "integral_symmetry_tolerance")
    @classmethod
    def validate_finite_scalars(cls, value: float) -> float:
        return _finite(value, label="Quantum Hamiltonian scalar")

    @field_validator("terms")
    @classmethod
    def validate_terms(
        cls, value: tuple[FermionicHamiltonianTerm, ...]
    ) -> tuple[FermionicHamiltonianTerm, ...]:
        return _ordered_fermionic_terms(value)

    @model_validator(mode="after")
    def validate_problem_identity(self) -> Self:
        if self.active_spin_orbital_count != 2 * self.active_spatial_orbital_count:
            raise ValueError("Active spin-orbital count must be twice spatial count.")
        if self.register_length != self.active_spin_orbital_count:
            raise ValueError(
                "Fermionic register length must match active spin orbitals."
            )
        if (
            self.alpha_electron_count + self.beta_electron_count
            != self.active_electron_count
        ):
            raise ValueError(
                "Active alpha/beta populations must sum to active electrons."
            )
        if self.active_electron_count > self.active_spin_orbital_count:
            raise ValueError("Active electron count exceeds active-space capacity.")
        return self


class MappedQubitHamiltonian(CanonicalModel):
    """Generic Pauli Hamiltonian produced from one exact fermionic artifact."""

    schema_version: CapabilityVersion
    mapping_identifier: str
    second_quantized_hamiltonian_identifier: str
    second_quantized_hamiltonian_sha256: str
    active_space_identifier: str
    active_space_sha256: str
    molecule_identifier: str
    mapper: Literal["jordan_wigner"]
    active_electron_count: int = Field(gt=0)
    alpha_electron_count: int = Field(ge=0)
    beta_electron_count: int = Field(ge=0)
    active_spatial_orbital_count: int = Field(gt=0, le=32)
    active_spin_orbital_count: int = Field(gt=0, le=64)
    number_of_qubits: int = Field(gt=0, le=64)
    constant_energy_hartree: float
    coefficient_encoding: Literal["ieee754_binary64_hex"] = "ieee754_binary64_hex"
    pauli_label_order: Literal["most_significant_qubit_left"] = (
        "most_significant_qubit_left"
    )
    two_qubit_reduction_applied: Literal[False] = False
    hermiticity_tolerance: float = Field(gt=0)
    maximum_antihermitian_coefficient: float = Field(ge=0)
    terms: tuple[PauliHamiltonianTerm, ...] = Field(min_length=1)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "mapping_identifier",
        "second_quantized_hamiltonian_identifier",
        "active_space_identifier",
        "molecule_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="mapped Hamiltonian identifier")

    @field_validator(
        "second_quantized_hamiltonian_sha256",
        "active_space_sha256",
    )
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator(
        "constant_energy_hartree",
        "hermiticity_tolerance",
        "maximum_antihermitian_coefficient",
    )
    @classmethod
    def validate_finite_scalars(cls, value: float) -> float:
        return _finite(value, label="Mapped Hamiltonian scalar")

    @field_validator("terms")
    @classmethod
    def validate_terms(
        cls, value: tuple[PauliHamiltonianTerm, ...]
    ) -> tuple[PauliHamiltonianTerm, ...]:
        return _ordered_pauli_terms(value)

    @model_validator(mode="after")
    def validate_mapping_identity(self) -> Self:
        if self.active_spin_orbital_count != 2 * self.active_spatial_orbital_count:
            raise ValueError("Active spin-orbital count must be twice spatial count.")
        if self.number_of_qubits != self.active_spin_orbital_count:
            raise ValueError(
                "Jordan-Wigner mapping without reduction requires one qubit "
                "per spin orbital."
            )
        if (
            self.alpha_electron_count + self.beta_electron_count
            != self.active_electron_count
        ):
            raise ValueError(
                "Mapped alpha/beta populations must sum to active electrons."
            )
        if any(len(term.label) != self.number_of_qubits for term in self.terms):
            raise ValueError("Every Pauli label must match the mapped qubit count.")
        if self.maximum_antihermitian_coefficient > self.hermiticity_tolerance:
            raise ValueError("Mapped Hamiltonian exceeds its Hermiticity tolerance.")
        return self


class ExactDiagonalizationResult(CanonicalModel):
    """Particle-sector exact ground-state energy for one mapped active space."""

    schema_version: CapabilityVersion
    result_identifier: str
    mapped_hamiltonian_identifier: str
    mapped_hamiltonian_sha256: str
    active_space_identifier: str
    active_space_sha256: str
    solver_identifier: Literal["numpy_eigh_particle_sector"]
    solver_version: str
    mapper: Literal["jordan_wigner"]
    number_of_qubits: int = Field(gt=0, le=_MAXIMUM_EXACT_QUBITS)
    active_spatial_orbital_count: int = Field(gt=0)
    alpha_electron_count: int = Field(ge=0)
    beta_electron_count: int = Field(ge=0)
    active_electron_count: int = Field(gt=0)
    full_hilbert_space_dimension: int = Field(gt=0)
    particle_sector_dimension: int = Field(gt=0)
    particle_sector_filter_applied: Literal[True] = True
    particle_number_tolerance: float = Field(gt=0)
    observed_alpha_electron_count: float
    observed_beta_electron_count: float
    raw_active_space_eigenvalue_hartree: float
    constant_energy_hartree: float
    total_energy_hartree: float
    maximum_eigenpair_residual_hartree: float = Field(ge=0)
    particle_sector_leakage_hartree: float = Field(ge=0)
    completed: Literal[True] = True

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "result_identifier",
        "mapped_hamiltonian_identifier",
        "active_space_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="exact-diagonalization identifier")

    @field_validator("mapped_hamiltonian_sha256", "active_space_sha256")
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("solver_version")
    @classmethod
    def validate_solver_version(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 128:
            raise ValueError("Exact solver version must be bounded nonblank text.")
        return normalized

    @field_validator(
        "particle_number_tolerance",
        "observed_alpha_electron_count",
        "observed_beta_electron_count",
        "raw_active_space_eigenvalue_hartree",
        "constant_energy_hartree",
        "total_energy_hartree",
        "maximum_eigenpair_residual_hartree",
        "particle_sector_leakage_hartree",
    )
    @classmethod
    def validate_finite_scalars(cls, value: float) -> float:
        return _finite(value, label="Exact-diagonalization scalar")

    @model_validator(mode="after")
    def validate_exact_result(self) -> Self:
        if self.number_of_qubits != 2 * self.active_spatial_orbital_count:
            raise ValueError("Exact-result qubit count must match spin orbitals.")
        if (
            self.alpha_electron_count + self.beta_electron_count
            != self.active_electron_count
        ):
            raise ValueError("Exact-result particle populations are inconsistent.")
        expected_full = 1 << self.number_of_qubits
        if self.full_hilbert_space_dimension != expected_full:
            raise ValueError(
                "Exact-result full Hilbert-space dimension is inconsistent."
            )
        expected_sector = comb(
            self.active_spatial_orbital_count, self.alpha_electron_count
        ) * comb(self.active_spatial_orbital_count, self.beta_electron_count)
        if self.particle_sector_dimension != expected_sector:
            raise ValueError("Exact-result particle-sector dimension is inconsistent.")
        if not math.isclose(
            self.observed_alpha_electron_count,
            self.alpha_electron_count,
            abs_tol=self.particle_number_tolerance,
        ) or not math.isclose(
            self.observed_beta_electron_count,
            self.beta_electron_count,
            abs_tol=self.particle_number_tolerance,
        ):
            raise ValueError(
                "Exact-result observed particle populations are inconsistent."
            )
        if not math.isclose(
            self.raw_active_space_eigenvalue_hartree + self.constant_energy_hartree,
            self.total_energy_hartree,
            rel_tol=1e-10,
            abs_tol=1e-10,
        ):
            raise ValueError(
                "Exact-result energy components do not sum to total energy."
            )
        return self


class QuantumRealParameter(CanonicalModel):
    """One indexed finite real variational parameter in exact binary64 text."""

    index: int = Field(ge=0, le=_MAXIMUM_VQE_PARAMETERS)
    value_hex: str

    @field_validator("value_hex")
    @classmethod
    def validate_value(cls, value: str) -> str:
        return _canonical_hex(value, label="Variational parameter")

    @property
    def value(self) -> float:
        return float.fromhex(self.value_hex)

    @classmethod
    def from_value(cls, index: int, value: float) -> QuantumRealParameter:
        number = _finite(value, label="Variational parameter")
        if number == 0.0:
            number = 0.0
        return cls(index=index, value_hex=number.hex())


def _ordered_parameters(
    value: tuple[QuantumRealParameter, ...],
) -> tuple[QuantumRealParameter, ...]:
    if not value:
        raise ValueError("A variational parameter vector cannot be empty.")
    if len(value) > _MAXIMUM_VQE_PARAMETERS:
        raise ValueError("The variational parameter vector exceeds its bound.")
    ordered = tuple(sorted(value, key=lambda item: item.index))
    if tuple(item.index for item in ordered) != tuple(range(len(ordered))):
        raise ValueError("Variational parameter indices must be contiguous from zero.")
    return ordered


def _parameter_sha256(value: tuple[QuantumRealParameter, ...]) -> str:
    return sha256_fingerprint([item.value_hex for item in value])


class VariationalAnsatz(CanonicalModel):
    """Engine-neutral specification of a bounded particle-conserving ansatz."""

    schema_version: CapabilityVersion
    ansatz_identifier: str
    mapped_hamiltonian_identifier: str
    mapped_hamiltonian_sha256: str
    active_space_identifier: str
    active_space_sha256: str
    molecule_identifier: str
    mapper: Literal["jordan_wigner"]
    number_of_qubits: int = Field(gt=0, le=64)
    active_spatial_orbital_count: int = Field(gt=0, le=32)
    active_electron_count: int = Field(gt=0)
    alpha_electron_count: int = Field(ge=0)
    beta_electron_count: int = Field(ge=0)
    ansatz: Literal["uccsd"]
    initial_state: Literal["hartree_fock"]
    repetitions: Literal[1] = 1
    generalized: Literal[False] = False
    preserve_spin: Literal[True] = True
    include_imaginary: Literal[False] = False
    construction_method: Literal[
        "particle_conserving_unitary_coupled_cluster_singles_doubles"
    ] = "particle_conserving_unitary_coupled_cluster_singles_doubles"
    initial_point_policy: Literal["all_zeros"]
    number_of_parameters: int = Field(gt=0, le=_MAXIMUM_VQE_PARAMETERS)
    initial_parameters: tuple[QuantumRealParameter, ...] = Field(min_length=1)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "ansatz_identifier",
        "mapped_hamiltonian_identifier",
        "active_space_identifier",
        "molecule_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="variational ansatz identifier")

    @field_validator("mapped_hamiltonian_sha256", "active_space_sha256")
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("initial_parameters")
    @classmethod
    def validate_parameters(
        cls, value: tuple[QuantumRealParameter, ...]
    ) -> tuple[QuantumRealParameter, ...]:
        return _ordered_parameters(value)

    @model_validator(mode="after")
    def validate_ansatz_identity(self) -> Self:
        if self.number_of_qubits != 2 * self.active_spatial_orbital_count:
            raise ValueError("Ansatz qubit count must match active spin orbitals.")
        if (
            self.alpha_electron_count + self.beta_electron_count
            != self.active_electron_count
        ):
            raise ValueError("Ansatz particle populations are inconsistent.")
        if self.active_electron_count > self.number_of_qubits:
            raise ValueError("Ansatz electron count exceeds qubit capacity.")
        if len(self.initial_parameters) != self.number_of_parameters:
            raise ValueError("Ansatz parameter count differs from its initial point.")
        if any(parameter.value != 0.0 for parameter in self.initial_parameters):
            raise ValueError("The all-zero initial-point policy requires zero values.")
        return self


class OptimizationEvaluation(CanonicalModel):
    """One immutable energy evaluation from the variational optimizer."""

    evaluation: int = Field(gt=0, le=_MAXIMUM_OPTIMIZATION_EVALUATIONS)
    raw_active_space_energy_hartree: float
    parameter_sha256: str

    @field_validator("raw_active_space_energy_hartree")
    @classmethod
    def validate_energy(cls, value: float) -> float:
        return _finite(value, label="Optimization energy")

    @field_validator("parameter_sha256")
    @classmethod
    def validate_parameter_hash(cls, value: str) -> str:
        return validate_sha256(value)


def _ordered_evaluations(
    value: tuple[OptimizationEvaluation, ...],
) -> tuple[OptimizationEvaluation, ...]:
    if not value:
        raise ValueError("A completed VQE result requires an optimization trace.")
    if len(value) > _MAXIMUM_OPTIMIZATION_EVALUATIONS:
        raise ValueError("The optimization trace exceeds its bound.")
    identifiers = tuple(item.evaluation for item in value)
    if any(
        current <= previous for previous, current in zip(identifiers, identifiers[1:])
    ):
        raise ValueError("Optimization evaluations must be strictly increasing.")
    return value


class VariationalGroundStateResult(CanonicalModel):
    """Completed deterministic statevector VQE result without reference-energy access."""

    schema_version: CapabilityVersion
    result_identifier: str
    mapped_hamiltonian_identifier: str
    mapped_hamiltonian_sha256: str
    ansatz_identifier: str
    ansatz_sha256: str
    active_space_identifier: str
    active_space_sha256: str
    algorithm_identifier: Literal["variational_quantum_eigensolver"]
    estimator_identifier: Literal["exact_statevector_expectation"]
    optimizer_identifier: Literal["slsqp"]
    optimizer_version: str
    optimizer_status: str
    maximum_iterations: int = Field(gt=0)
    convergence_threshold: float = Field(gt=0)
    random_seed: int = Field(ge=0)
    number_of_qubits: int = Field(gt=0, le=64)
    number_of_parameters: int = Field(gt=0, le=_MAXIMUM_VQE_PARAMETERS)
    optimizer_evaluations: int = Field(gt=0, le=_MAXIMUM_OPTIMIZATION_EVALUATIONS)
    initial_point_sha256: str
    optimized_parameters: tuple[QuantumRealParameter, ...] = Field(min_length=1)
    optimized_parameters_sha256: str
    raw_active_space_energy_hartree: float
    constant_energy_hartree: float
    total_energy_hartree: float
    reference_energy_used: Literal[False] = False
    converged: Literal[True] = True
    trace: tuple[OptimizationEvaluation, ...] = Field(min_length=1)
    completed: Literal[True] = True

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "result_identifier",
        "mapped_hamiltonian_identifier",
        "ansatz_identifier",
        "active_space_identifier",
        "optimizer_status",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="variational result identifier")

    @field_validator(
        "mapped_hamiltonian_sha256",
        "ansatz_sha256",
        "active_space_sha256",
        "initial_point_sha256",
        "optimized_parameters_sha256",
    )
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("optimizer_version")
    @classmethod
    def validate_optimizer_version(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 128:
            raise ValueError("Optimizer version must be bounded nonblank text.")
        return normalized

    @field_validator(
        "convergence_threshold",
        "raw_active_space_energy_hartree",
        "constant_energy_hartree",
        "total_energy_hartree",
    )
    @classmethod
    def validate_finite_scalars(cls, value: float) -> float:
        return _finite(value, label="Variational result scalar")

    @field_validator("optimized_parameters")
    @classmethod
    def validate_parameters(
        cls, value: tuple[QuantumRealParameter, ...]
    ) -> tuple[QuantumRealParameter, ...]:
        return _ordered_parameters(value)

    @field_validator("trace")
    @classmethod
    def validate_trace(
        cls, value: tuple[OptimizationEvaluation, ...]
    ) -> tuple[OptimizationEvaluation, ...]:
        return _ordered_evaluations(value)

    @model_validator(mode="after")
    def validate_vqe_result(self) -> Self:
        if len(self.optimized_parameters) != self.number_of_parameters:
            raise ValueError("VQE optimized parameter count is inconsistent.")
        if _parameter_sha256(self.optimized_parameters) != (
            self.optimized_parameters_sha256
        ):
            raise ValueError("VQE optimized parameter hash is inconsistent.")
        if self.optimizer_evaluations < len(self.trace):
            raise ValueError(
                "VQE optimizer evaluations cannot be shorter than its trace."
            )
        if not math.isclose(
            self.raw_active_space_energy_hartree + self.constant_energy_hartree,
            self.total_energy_hartree,
            rel_tol=1e-10,
            abs_tol=1e-10,
        ):
            raise ValueError("VQE energy components do not sum to total energy.")
        return self


class StatevectorSimulationResult(CanonicalModel):
    """Canonical optimized statevector with particle and energy checks."""

    schema_version: CapabilityVersion
    result_identifier: str
    mapped_hamiltonian_identifier: str
    mapped_hamiltonian_sha256: str
    ansatz_identifier: str
    ansatz_sha256: str
    vqe_result_identifier: str
    vqe_result_sha256: str
    active_space_identifier: str
    active_space_sha256: str
    simulator_identifier: Literal["exact_statevector"]
    number_of_qubits: int = Field(gt=0, le=_MAXIMUM_STATEVECTOR_QUBITS)
    active_spatial_orbital_count: int = Field(gt=0)
    active_electron_count: int = Field(gt=0)
    alpha_electron_count: int = Field(ge=0)
    beta_electron_count: int = Field(ge=0)
    state_dimension: int = Field(gt=0)
    amplitude_order: Literal["computational_basis_index_ascending"] = (
        "computational_basis_index_ascending"
    )
    qubit_zero_significance: Literal["least_significant"] = "least_significant"
    global_phase_canonicalized: Literal[True] = True
    global_phase_anchor_index: int = Field(ge=0)
    amplitudes: tuple[QuantumComplexCoefficient, ...] = Field(min_length=1)
    normalization_tolerance: float = Field(gt=0)
    normalization_residual: float = Field(ge=0)
    particle_sector_tolerance: float = Field(gt=0)
    particle_sector_probability: float = Field(ge=0, le=1)
    particle_sector_leakage_probability: float = Field(ge=0, le=1)
    observed_alpha_electron_count: float
    observed_beta_electron_count: float
    raw_active_space_expectation_hartree: float
    constant_energy_hartree: float
    total_energy_hartree: float
    vqe_total_energy_hartree: float
    vqe_energy_difference_hartree: float = Field(ge=0)
    energy_consistency_tolerance: float = Field(gt=0)
    completed: Literal[True] = True

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "result_identifier",
        "mapped_hamiltonian_identifier",
        "ansatz_identifier",
        "vqe_result_identifier",
        "active_space_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="statevector result identifier")

    @field_validator(
        "mapped_hamiltonian_sha256",
        "ansatz_sha256",
        "vqe_result_sha256",
        "active_space_sha256",
    )
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator(
        "normalization_tolerance",
        "normalization_residual",
        "particle_sector_tolerance",
        "particle_sector_probability",
        "particle_sector_leakage_probability",
        "observed_alpha_electron_count",
        "observed_beta_electron_count",
        "raw_active_space_expectation_hartree",
        "constant_energy_hartree",
        "total_energy_hartree",
        "vqe_total_energy_hartree",
        "vqe_energy_difference_hartree",
        "energy_consistency_tolerance",
    )
    @classmethod
    def validate_finite_scalars(cls, value: float) -> float:
        return _finite(value, label="Statevector result scalar")

    @model_validator(mode="after")
    def validate_statevector_result(self) -> Self:
        if self.number_of_qubits != 2 * self.active_spatial_orbital_count:
            raise ValueError("Statevector qubit count must match active spin orbitals.")
        if (
            self.alpha_electron_count + self.beta_electron_count
            != self.active_electron_count
        ):
            raise ValueError("Statevector particle populations are inconsistent.")
        expected_dimension = 1 << self.number_of_qubits
        if self.state_dimension != expected_dimension:
            raise ValueError("Statevector dimension is inconsistent with its qubits.")
        if len(self.amplitudes) != self.state_dimension:
            raise ValueError("Statevector amplitude count is inconsistent.")
        if self.global_phase_anchor_index >= self.state_dimension:
            raise ValueError("Statevector global-phase anchor is out of range.")
        anchor = self.amplitudes[self.global_phase_anchor_index].value
        if anchor.real <= 0 or abs(anchor.imag) > self.normalization_tolerance:
            raise ValueError("Statevector global-phase anchor is not positive real.")
        if self.normalization_residual > self.normalization_tolerance:
            raise ValueError("Statevector normalization exceeds its tolerance.")
        if not math.isclose(
            self.particle_sector_probability + self.particle_sector_leakage_probability,
            1.0,
            rel_tol=1e-10,
            abs_tol=self.particle_sector_tolerance,
        ):
            raise ValueError(
                "Statevector particle-sector probabilities are inconsistent."
            )
        if self.particle_sector_leakage_probability > self.particle_sector_tolerance:
            raise ValueError("Statevector leaks outside the declared particle sector.")
        if not math.isclose(
            self.observed_alpha_electron_count,
            self.alpha_electron_count,
            abs_tol=self.particle_sector_tolerance,
        ) or not math.isclose(
            self.observed_beta_electron_count,
            self.beta_electron_count,
            abs_tol=self.particle_sector_tolerance,
        ):
            raise ValueError(
                "Statevector observed particle populations are inconsistent."
            )
        if not math.isclose(
            self.raw_active_space_expectation_hartree + self.constant_energy_hartree,
            self.total_energy_hartree,
            rel_tol=1e-10,
            abs_tol=1e-10,
        ):
            raise ValueError(
                "Statevector energy components do not sum to total energy."
            )
        if not math.isclose(
            self.vqe_energy_difference_hartree,
            abs(self.total_energy_hartree - self.vqe_total_energy_hartree),
            rel_tol=1e-10,
            abs_tol=1e-12,
        ):
            raise ValueError("Statevector VQE energy difference is inconsistent.")
        if self.vqe_energy_difference_hartree > self.energy_consistency_tolerance:
            raise ValueError("Statevector energy differs from the VQE result.")
        return self
