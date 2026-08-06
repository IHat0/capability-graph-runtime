"""Engine-neutral electronic-structure artifacts for Pulsate Phase 4."""

from __future__ import annotations

import math
import re
from functools import reduce
from operator import mul
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import CanonicalModel
from cgr.science.canonical import validate_identifier

_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_ELEMENT_SYMBOL = re.compile(r"^[A-Za-z]{1,3}$")
_BASIS_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+*()_,./-]{0,127}$")
_MAXIMUM_TENSOR_VALUES = 2_000_000


def _validate_schema_version(value: CapabilityVersion) -> CapabilityVersion:
    if value != _SCHEMA_VERSION:
        raise ValueError("Electronic-structure schema version must be exactly 1.0.0.")
    return value


def _finite(value: float, *, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    return float(value)


def _basis_name(value: str) -> str:
    normalized = value.strip()
    if not _BASIS_NAME.fullmatch(normalized):
        raise ValueError("Basis-set names must be bounded portable identifiers.")
    return normalized


class ElectronicAtom(CanonicalModel):
    """One atom in exact source order with Cartesian coordinates in angstrom."""

    atom_index: int = Field(ge=0)
    atomic_number: int = Field(gt=0)
    element_symbol: str
    x_angstrom: float
    y_angstrom: float
    z_angstrom: float
    source_atom_index: int | None = Field(default=None, ge=0)

    @field_validator("element_symbol")
    @classmethod
    def normalize_element_symbol(cls, value: str) -> str:
        normalized = value.strip()
        if not _ELEMENT_SYMBOL.fullmatch(normalized):
            raise ValueError("Element symbol must contain one to three letters.")
        return normalized[0].upper() + normalized[1:].lower()

    @field_validator("x_angstrom", "y_angstrom", "z_angstrom")
    @classmethod
    def validate_coordinate(cls, value: float) -> float:
        return _finite(value, label="Electronic-structure coordinate")


class ElectronicMolecule(CanonicalModel):
    """Generic molecular geometry and exact charge/spin identity."""

    schema_version: CapabilityVersion
    molecule_identifier: str
    source_artifact_identifier: str
    source_geometry_identifier: str
    coordinate_unit: Literal["angstrom"] = "angstrom"
    atoms: tuple[ElectronicAtom, ...] = Field(min_length=1)
    molecular_charge: int
    source_formal_charge: int | None = None
    spin: int = Field(ge=0)
    electron_count: int = Field(gt=0)
    alpha_electron_count: int = Field(ge=0)
    beta_electron_count: int = Field(ge=0)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "molecule_identifier",
        "source_artifact_identifier",
        "source_geometry_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="electronic molecule identifier")

    @field_validator("atoms")
    @classmethod
    def order_atoms(
        cls, value: tuple[ElectronicAtom, ...]
    ) -> tuple[ElectronicAtom, ...]:
        indices = [atom.atom_index for atom in value]
        if sorted(indices) != list(range(len(value))):
            raise ValueError(
                "Electronic molecule atom indices must be contiguous from zero."
            )
        source_indices = [
            atom.source_atom_index
            for atom in value
            if atom.source_atom_index is not None
        ]
        if len(source_indices) != len(set(source_indices)):
            raise ValueError("Electronic molecule source atom indices must be unique.")
        return tuple(sorted(value, key=lambda atom: atom.atom_index))

    @model_validator(mode="after")
    def validate_electron_identity(self) -> Self:
        expected_electrons = (
            sum(atom.atomic_number for atom in self.atoms) - self.molecular_charge
        )
        if expected_electrons <= 0 or self.electron_count != expected_electrons:
            raise ValueError(
                "Electronic molecule electron count does not match atoms and charge."
            )
        if self.spin > self.electron_count or (self.electron_count - self.spin) % 2:
            raise ValueError(
                "Electronic molecule spin is incompatible with its electron count."
            )
        expected_alpha = (self.electron_count + self.spin) // 2
        expected_beta = self.electron_count - expected_alpha
        if (
            self.alpha_electron_count != expected_alpha
            or self.beta_electron_count != expected_beta
        ):
            raise ValueError(
                "Electronic molecule alpha/beta populations are inconsistent."
            )
        return self


ReferenceMethod = Literal["rhf", "uhf", "rohf"]


class ElectronicStructureConfiguration(CanonicalModel):
    """Explicit basis, reference method and SCF controls."""

    schema_version: CapabilityVersion
    configuration_identifier: str
    molecule_identifier: str
    basis_set: str
    reference_method: ReferenceMethod
    convergence_tolerance: float
    maximum_iterations: int = Field(gt=0, le=100_000)
    direct_scf: bool
    density_fitting: bool
    symmetry: bool
    initial_guess: Literal["minao", "atom", "hcore", "1e"]

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator("configuration_identifier", "molecule_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="electronic configuration identifier")

    @field_validator("basis_set")
    @classmethod
    def validate_basis_set(cls, value: str) -> str:
        return _basis_name(value)

    @field_validator("convergence_tolerance")
    @classmethod
    def validate_convergence_tolerance(cls, value: float) -> float:
        value = _finite(value, label="SCF convergence tolerance")
        if not 1e-15 <= value <= 1e-2:
            raise ValueError(
                "SCF convergence tolerance is outside the supported range."
            )
        return value


class ElectronicTensor(CanonicalModel):
    """Bounded flattened tensor with explicit shape, unit and index convention."""

    shape: tuple[int, ...] = Field(min_length=1, max_length=4)
    values: tuple[float, ...]
    unit: Literal["dimensionless", "hartree", "electron"]
    index_convention: str

    @field_validator("shape")
    @classmethod
    def validate_shape(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(dimension <= 0 for dimension in value):
            raise ValueError("Electronic tensor dimensions must be positive.")
        size = reduce(mul, value, 1)
        if size > _MAXIMUM_TENSOR_VALUES:
            raise ValueError(
                "Electronic tensor exceeds the public artifact size bound."
            )
        return value

    @field_validator("values")
    @classmethod
    def validate_values(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        return tuple(_finite(item, label="Electronic tensor value") for item in value)

    @field_validator("index_convention")
    @classmethod
    def validate_index_convention(cls, value: str) -> str:
        return validate_identifier(value, label="electronic tensor convention")

    @model_validator(mode="after")
    def validate_size(self) -> Self:
        if reduce(mul, self.shape, 1) != len(self.values):
            raise ValueError(
                "Electronic tensor shape does not match its flattened values."
            )
        return self


class ElectronicHartreeFockResult(CanonicalModel):
    """Generic converged-or-failed Hartree-Fock reference evidence."""

    schema_version: CapabilityVersion
    result_identifier: str
    molecule_identifier: str
    configuration_identifier: str
    reference_method: ReferenceMethod
    converged: bool
    iterations: int = Field(ge=0)
    electron_count: int = Field(gt=0)
    alpha_electron_count: int = Field(ge=0)
    beta_electron_count: int = Field(ge=0)
    atomic_orbital_count: int = Field(gt=0)
    spatial_orbital_count: int = Field(gt=0)
    electronic_energy_hartree: float
    nuclear_repulsion_energy_hartree: float
    total_energy_hartree: float
    orbital_energies_alpha_hartree: tuple[float, ...] = Field(min_length=1)
    orbital_energies_beta_hartree: tuple[float, ...] = Field(min_length=1)
    orbital_occupations_alpha: tuple[float, ...] = Field(min_length=1)
    orbital_occupations_beta: tuple[float, ...] = Field(min_length=1)
    mo_coefficients_alpha: ElectronicTensor
    mo_coefficients_beta: ElectronicTensor
    overlap_matrix_ao: ElectronicTensor
    core_hamiltonian_ao: ElectronicTensor
    density_matrix_alpha_ao: ElectronicTensor
    density_matrix_beta_ao: ElectronicTensor

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "result_identifier",
        "molecule_identifier",
        "configuration_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="Hartree-Fock identifier")

    @field_validator(
        "electronic_energy_hartree",
        "nuclear_repulsion_energy_hartree",
        "total_energy_hartree",
    )
    @classmethod
    def validate_energies(cls, value: float) -> float:
        return _finite(value, label="Hartree-Fock energy")

    @field_validator(
        "orbital_energies_alpha_hartree",
        "orbital_energies_beta_hartree",
        "orbital_occupations_alpha",
        "orbital_occupations_beta",
    )
    @classmethod
    def validate_orbital_values(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        return tuple(
            _finite(item, label="Hartree-Fock orbital value") for item in value
        )

    @model_validator(mode="after")
    def validate_result_shape(self) -> Self:
        count = self.spatial_orbital_count
        if any(
            len(values) != count
            for values in (
                self.orbital_energies_alpha_hartree,
                self.orbital_energies_beta_hartree,
                self.orbital_occupations_alpha,
                self.orbital_occupations_beta,
            )
        ):
            raise ValueError("Hartree-Fock orbital vectors must match orbital count.")
        coefficient_shape = (self.atomic_orbital_count, count)
        if (
            self.mo_coefficients_alpha.shape != coefficient_shape
            or self.mo_coefficients_beta.shape != coefficient_shape
        ):
            raise ValueError("Hartree-Fock coefficient matrices have invalid shapes.")
        ao_shape = (self.atomic_orbital_count, self.atomic_orbital_count)
        for tensor in (
            self.overlap_matrix_ao,
            self.core_hamiltonian_ao,
            self.density_matrix_alpha_ao,
            self.density_matrix_beta_ao,
        ):
            if tensor.shape != ao_shape:
                raise ValueError("Hartree-Fock AO matrices have invalid shapes.")
        if not math.isclose(
            self.electronic_energy_hartree + self.nuclear_repulsion_energy_hartree,
            self.total_energy_hartree,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "Hartree-Fock energy components do not sum to total energy."
            )
        if not math.isclose(
            sum(self.orbital_occupations_alpha),
            self.alpha_electron_count,
            abs_tol=1e-7,
        ) or not math.isclose(
            sum(self.orbital_occupations_beta),
            self.beta_electron_count,
            abs_tol=1e-7,
        ):
            raise ValueError(
                "Hartree-Fock occupations do not match electron populations."
            )
        return self


class ElectronicOrbital(CanonicalModel):
    """One spatial orbital projected from a Hartree-Fock result."""

    orbital_index: int = Field(ge=0)
    spin_channel: Literal["alpha", "beta"]
    energy_hartree: float
    occupation: float
    coefficients: tuple[float, ...] = Field(min_length=1)

    @field_validator("energy_hartree", "occupation")
    @classmethod
    def validate_scalars(cls, value: float) -> float:
        return _finite(value, label="Orbital scalar")

    @field_validator("coefficients")
    @classmethod
    def validate_coefficients(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        return tuple(_finite(item, label="Orbital coefficient") for item in value)


class ElectronicOrbitalSet(CanonicalModel):
    """Generic alpha and beta orbital records in stable order."""

    schema_version: CapabilityVersion
    orbital_set_identifier: str
    hartree_fock_result_identifier: str
    basis_set: str
    atomic_orbital_count: int = Field(gt=0)
    spatial_orbital_count: int = Field(gt=0)
    alpha_orbitals: tuple[ElectronicOrbital, ...] = Field(min_length=1)
    beta_orbitals: tuple[ElectronicOrbital, ...] = Field(min_length=1)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator("orbital_set_identifier", "hartree_fock_result_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="orbital-set identifier")

    @field_validator("basis_set")
    @classmethod
    def validate_basis_set(cls, value: str) -> str:
        return _basis_name(value)

    @model_validator(mode="after")
    def validate_orbitals(self) -> Self:
        for expected_channel, orbitals in (
            ("alpha", self.alpha_orbitals),
            ("beta", self.beta_orbitals),
        ):
            indices = [orbital.orbital_index for orbital in orbitals]
            if indices != list(range(self.spatial_orbital_count)):
                raise ValueError("Orbital indices must be contiguous and ordered.")
            if any(orbital.spin_channel != expected_channel for orbital in orbitals):
                raise ValueError("Orbital spin channels are inconsistent.")
            if any(
                len(orbital.coefficients) != self.atomic_orbital_count
                for orbital in orbitals
            ):
                raise ValueError("Orbital coefficient length does not match AO count.")
        return self


class ElectronicReferenceCalculation(CanonicalModel):
    """Classical reference energy derived from an explicit electronic problem."""

    schema_version: CapabilityVersion
    reference_identifier: str
    molecule_identifier: str
    configuration_identifier: str
    hartree_fock_result_identifier: str
    method: Literal["hartree_fock", "mp2"]
    converged: bool
    reference_energy_hartree: float
    correlation_energy_hartree: float
    total_energy_hartree: float

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "reference_identifier",
        "molecule_identifier",
        "configuration_identifier",
        "hartree_fock_result_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="reference-calculation identifier")

    @field_validator(
        "reference_energy_hartree",
        "correlation_energy_hartree",
        "total_energy_hartree",
    )
    @classmethod
    def validate_energy(cls, value: float) -> float:
        return _finite(value, label="Reference energy")

    @model_validator(mode="after")
    def validate_energy_sum(self) -> Self:
        if not math.isclose(
            self.reference_energy_hartree + self.correlation_energy_hartree,
            self.total_energy_hartree,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError("Reference energy components do not sum to total energy.")
        if self.method == "hartree_fock" and self.correlation_energy_hartree != 0:
            raise ValueError(
                "Hartree-Fock reference calculations have zero correlation energy."
            )
        return self


class ElectronicActiveSpace(CanonicalModel):
    """Spin-restricted active-space Hamiltonian in generic integral form."""

    schema_version: CapabilityVersion
    active_space_identifier: str
    molecule_identifier: str
    hartree_fock_result_identifier: str
    active_electron_count: int = Field(gt=0)
    alpha_electron_count: int = Field(ge=0)
    beta_electron_count: int = Field(ge=0)
    active_spatial_orbital_count: int = Field(gt=0, le=32)
    active_spin_orbital_count: int = Field(gt=0, le=64)
    active_orbital_indices: tuple[int, ...] = Field(min_length=1)
    frozen_core_orbital_indices: tuple[int, ...] = ()
    constant_energy_hartree: float
    one_body_integrals: ElectronicTensor
    two_body_integrals: ElectronicTensor
    integral_convention: Literal["chemist_pqrs"] = "chemist_pqrs"

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "active_space_identifier",
        "molecule_identifier",
        "hartree_fock_result_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="active-space identifier")

    @field_validator("constant_energy_hartree")
    @classmethod
    def validate_constant_energy(cls, value: float) -> float:
        return _finite(value, label="Active-space constant energy")

    @field_validator("active_orbital_indices", "frozen_core_orbital_indices")
    @classmethod
    def validate_orbital_indices(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(index < 0 for index in value) or len(value) != len(set(value)):
            raise ValueError(
                "Active-space orbital indices must be unique and nonnegative."
            )
        return tuple(value)

    @model_validator(mode="after")
    def validate_active_space(self) -> Self:
        n = self.active_spatial_orbital_count
        if len(self.active_orbital_indices) != n:
            raise ValueError("Active orbital count must match explicit indices.")
        if set(self.active_orbital_indices) & set(self.frozen_core_orbital_indices):
            raise ValueError("Active and frozen-core orbital indices must be disjoint.")
        if self.active_spin_orbital_count != 2 * n:
            raise ValueError("Active spin-orbital count must be twice spatial count.")
        if (
            self.alpha_electron_count + self.beta_electron_count
            != self.active_electron_count
        ):
            raise ValueError(
                "Active alpha/beta populations must sum to active electrons."
            )
        if self.active_electron_count > self.active_spin_orbital_count:
            raise ValueError("Active electron count exceeds active-space capacity.")
        if self.one_body_integrals.shape != (n, n):
            raise ValueError("Active-space one-body tensor has the wrong shape.")
        if self.two_body_integrals.shape != (n, n, n, n):
            raise ValueError("Active-space two-body tensor has the wrong shape.")
        return self


class QMMMEmbeddingSite(CanonicalModel):
    """One environment site prepared for later QM/MM coupling."""

    site_index: int = Field(ge=0)
    source_particle_index: int = Field(ge=0)
    element_symbol: str | None = None
    x_angstrom: float
    y_angstrom: float
    z_angstrom: float
    charge_e: float | None = None
    charge_source: Literal["formal_charge", "unassigned"]

    @field_validator("element_symbol")
    @classmethod
    def normalize_element_symbol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not _ELEMENT_SYMBOL.fullmatch(normalized):
            raise ValueError("Embedding-site element symbol is invalid.")
        return normalized[0].upper() + normalized[1:].lower()

    @field_validator("x_angstrom", "y_angstrom", "z_angstrom")
    @classmethod
    def validate_coordinate(cls, value: float) -> float:
        return _finite(value, label="QM/MM embedding coordinate")

    @field_validator("charge_e")
    @classmethod
    def validate_charge(cls, value: float | None) -> float | None:
        return None if value is None else _finite(value, label="QM/MM embedding charge")

    @model_validator(mode="after")
    def validate_charge_source(self) -> Self:
        if (self.charge_e is None) != (self.charge_source == "unassigned"):
            raise ValueError("Embedding charge source must match charge availability.")
        return self


class QMMMEmbeddingFoundation(CanonicalModel):
    """Provenance-rich MM environment sites; not yet a coupled QM/MM calculation."""

    schema_version: CapabilityVersion
    embedding_identifier: str
    molecule_identifier: str
    environment_identifier: str
    qm_atom_count: int = Field(gt=0)
    excluded_solute_particle_count: int = Field(gt=0)
    embedding_sites: tuple[QMMMEmbeddingSite, ...]
    assigned_charge_count: int = Field(ge=0)
    unassigned_charge_count: int = Field(ge=0)
    electrostatic_embedding_ready: bool
    boundary_policy: Literal["none"] = "none"
    charge_model: Literal["environment_formal_charge_foundation"] = (
        "environment_formal_charge_foundation"
    )

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "embedding_identifier",
        "molecule_identifier",
        "environment_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="QM/MM embedding identifier")

    @model_validator(mode="after")
    def validate_embedding(self) -> Self:
        indices = [site.site_index for site in self.embedding_sites]
        if indices != list(range(len(self.embedding_sites))):
            raise ValueError("QM/MM embedding site indices must be contiguous.")
        assigned = sum(site.charge_e is not None for site in self.embedding_sites)
        unassigned = len(self.embedding_sites) - assigned
        if (
            assigned != self.assigned_charge_count
            or unassigned != self.unassigned_charge_count
        ):
            raise ValueError("QM/MM embedding charge counts are inconsistent.")
        if self.electrostatic_embedding_ready != (unassigned == 0):
            raise ValueError(
                "QM/MM embedding readiness must reflect charge assignment."
            )
        return self
