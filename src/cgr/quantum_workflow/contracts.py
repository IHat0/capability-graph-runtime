"""Engine-neutral artifacts for Pulsate Phase 5 quantum workflows."""

from __future__ import annotations

import math
import re
from math import comb
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import CanonicalModel
from cgr.science.canonical import validate_identifier, validate_sha256

_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_FERMIONIC_TOKEN = re.compile(r"^[+-]_[0-9]+$")
_PAULI_LABEL = re.compile(r"^[IXYZ]+$")
_MAXIMUM_OPERATOR_TERMS = 2_000_000
_MAXIMUM_EXACT_QUBITS = 12


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
