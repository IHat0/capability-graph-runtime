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
    unit: Literal[
        "dimensionless",
        "hartree",
        "electron",
        "hartree_per_bohr",
        "hartree_per_bohr2",
    ]
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


class ElectronicOrbitalSelectionScore(CanonicalModel):
    """Evidence for one orbital selected by an automatic active-space policy."""

    orbital_index: int = Field(ge=0)
    occupation: float = Field(ge=0, le=2)
    target_projection_score: float = Field(ge=0)
    alpha_occupation: float | None = Field(default=None, ge=0, le=1)
    beta_occupation: float | None = Field(default=None, ge=0, le=1)
    selection_reasons: tuple[str, ...] = Field(default=(), max_length=16)

    @field_validator("occupation", "target_projection_score")
    @classmethod
    def validate_values(cls, value: float) -> float:
        return _finite(value, label="Active-space selection value")

    @field_validator("alpha_occupation", "beta_occupation")
    @classmethod
    def validate_spin_occupation(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _finite(value, label="Active-space spin occupation")

    @field_validator("selection_reasons")
    @classmethod
    def validate_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(
            validate_identifier(reason, label="active-space selection reason")
            for reason in value
        )
        if len(normalized) != len(set(normalized)):
            raise ValueError("Active-space selection reasons must be unique.")
        return normalized


class ElectronicActiveSpaceSelection(CanonicalModel):
    """Auditable automatic orbital selection for active-space construction."""

    schema_version: CapabilityVersion
    selection_identifier: str
    molecule_identifier: str
    hartree_fock_result_identifier: str
    selection_method: Literal[
        "frontier",
        "target_ao_projection",
        "reaction_center_projection",
        "metal_ligand_projection",
    ]
    reference_method: ReferenceMethod = "rhf"
    orbital_basis: Literal[
        "canonical_rhf",
        "canonical_rohf",
        "unrestricted_natural_orbital",
    ] = "canonical_rhf"
    active_electron_count: int = Field(gt=0, le=64)
    active_spatial_orbital_count: int = Field(gt=0, le=32)
    active_orbital_indices: tuple[int, ...] = Field(min_length=1)
    target_atom_indices: tuple[int, ...] = ()
    target_bond_atom_pairs: tuple[tuple[int, int], ...] = ()
    target_ao_labels: tuple[str, ...] = ()
    projection_threshold: float = Field(default=0.0, ge=0, le=1)
    orbital_scores: tuple[ElectronicOrbitalSelectionScore, ...] = Field(
        min_length=1
    )

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "selection_identifier",
        "molecule_identifier",
        "hartree_fock_result_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(
            value,
            label="active-space selection identifier",
        )

    @field_validator("active_orbital_indices", "target_atom_indices")
    @classmethod
    def validate_indices(
        cls,
        value: tuple[int, ...],
    ) -> tuple[int, ...]:
        if any(index < 0 for index in value):
            raise ValueError(
                "Active-space selection indices cannot be negative."
            )
        if len(value) != len(set(value)):
            raise ValueError(
                "Active-space selection indices must be unique."
            )
        return tuple(sorted(value))

    @field_validator("target_bond_atom_pairs")
    @classmethod
    def validate_bond_pairs(
        cls,
        value: tuple[tuple[int, int], ...],
    ) -> tuple[tuple[int, int], ...]:
        normalized: list[tuple[int, int]] = []
        for pair in value:
            if len(pair) != 2 or pair[0] < 0 or pair[1] < 0 or pair[0] == pair[1]:
                raise ValueError("Reaction-centre bonds require two distinct atoms.")
            normalized.append(tuple(sorted(pair)))
        if len(normalized) != len(set(normalized)):
            raise ValueError("Reaction-centre bond targets must be unique.")
        return tuple(sorted(normalized))

    @field_validator("target_ao_labels")
    @classmethod
    def validate_ao_labels(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(label.strip() for label in value)
        if any(not label or len(label) > 80 for label in normalized):
            raise ValueError("Target AO labels must be nonempty and bounded.")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Target AO labels must be unique.")
        return normalized

    @field_validator("projection_threshold")
    @classmethod
    def validate_projection_threshold(cls, value: float) -> float:
        return _finite(value, label="Active-space projection threshold")

    @field_validator("orbital_scores")
    @classmethod
    def validate_scores(
        cls,
        value: tuple[ElectronicOrbitalSelectionScore, ...],
    ) -> tuple[ElectronicOrbitalSelectionScore, ...]:
        indices = [score.orbital_index for score in value]
        if len(indices) != len(set(indices)):
            raise ValueError(
                "Active-space orbital scores must have unique indices."
            )
        return tuple(
            sorted(value, key=lambda item: item.orbital_index)
        )

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        if (
            len(self.active_orbital_indices)
            != self.active_spatial_orbital_count
        ):
            raise ValueError(
                "Selected orbital count does not match active-space size."
            )

        if tuple(
            score.orbital_index for score in self.orbital_scores
        ) != self.active_orbital_indices:
            raise ValueError(
                "Selection evidence must cover exactly the selected orbitals."
            )

        resolved_electrons = sum(score.occupation for score in self.orbital_scores)
        if self.orbital_basis != "unrestricted_natural_orbital":
            if not math.isclose(
                resolved_electrons,
                self.active_electron_count,
                rel_tol=0,
                abs_tol=1e-7,
            ):
                raise ValueError(
                    "Selected occupations do not match active-electron count."
                )
        elif abs(resolved_electrons - self.active_electron_count) > 0.25:
            raise ValueError(
                "UNO population is inconsistent with the assigned active electrons."
            )

        if (
            self.selection_method
            in {
                "target_ao_projection",
                "reaction_center_projection",
                "metal_ligand_projection",
            }
            and not self.target_atom_indices
        ):
            raise ValueError(
                "Target-AO selection requires target atoms."
            )

        if (
            self.selection_method == "frontier"
            and (
                self.target_atom_indices
                or self.target_bond_atom_pairs
                or self.target_ao_labels
            )
        ):
            raise ValueError(
                "Frontier selection cannot declare target atoms."
            )

        if (
            self.selection_method == "reaction_center_projection"
            and not self.target_bond_atom_pairs
        ):
            raise ValueError("Reaction-centre selection requires target bonds.")

        if (
            self.selection_method == "metal_ligand_projection"
            and not self.target_ao_labels
        ):
            raise ValueError("Metal/ligand selection requires target AO labels.")

        expected_basis = {
            "rhf": "canonical_rhf",
            "rohf": "canonical_rohf",
            "uhf": "unrestricted_natural_orbital",
        }[self.reference_method]
        if self.orbital_basis != expected_basis:
            raise ValueError("Active-space orbital basis disagrees with the reference.")

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
    """One real force-field point charge used for QM electrostatic embedding."""

    site_index: int = Field(ge=0)
    source_particle_index: int = Field(ge=0)
    element_symbol: str | None = None
    x_angstrom: float
    y_angstrom: float
    z_angstrom: float
    charge_e: float
    charge_source: Literal[
        "openmm_nonbonded_force",
        "openmm_boundary_charge_shift",
    ] = (
        "openmm_nonbonded_force"
    )

    @field_validator("element_symbol")
    @classmethod
    def normalize_element_symbol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not _ELEMENT_SYMBOL.fullmatch(normalized):
            raise ValueError("Embedding-site element symbol is invalid.")
        return normalized[0].upper() + normalized[1:].lower()

    @field_validator(
        "x_angstrom",
        "y_angstrom",
        "z_angstrom",
        "charge_e",
    )
    @classmethod
    def validate_scalar(cls, value: float) -> float:
        return _finite(value, label="QM/MM embedding value")


class QMMMBoundaryChargeAdjustment(CanonicalModel):
    """One explicit MM point-charge change made by a covalent boundary scheme."""

    source_particle_index: int = Field(ge=0)
    original_charge_e: float
    adjusted_charge_e: float
    charge_delta_e: float
    boundary_mm_particle_indices: tuple[int, ...] = Field(min_length=1)
    role: Literal["boundary_charge_removed", "neighbor_charge_recipient"]

    @field_validator("original_charge_e", "adjusted_charge_e", "charge_delta_e")
    @classmethod
    def validate_charge(cls, value: float) -> float:
        return _finite(value, label="QM/MM boundary charge")

    @field_validator("boundary_mm_particle_indices")
    @classmethod
    def validate_boundary_indices(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Boundary charge sources must be unique.")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def validate_delta(self) -> Self:
        if not math.isclose(
            self.original_charge_e + self.charge_delta_e,
            self.adjusted_charge_e,
            rel_tol=0.0,
            abs_tol=1e-10,
        ):
            raise ValueError("Boundary charge adjustment delta is inconsistent.")
        if (
            self.role == "boundary_charge_removed"
            and not math.isclose(self.adjusted_charge_e, 0.0, abs_tol=1e-12)
        ):
            raise ValueError("A removed boundary charge must be exactly zero.")
        return self


class ElectronicImplicitSolventResult(CanonicalModel):
    """Self-consistent continuum-solvent electronic-energy evidence."""

    schema_version: CapabilityVersion
    result_identifier: str
    molecule_identifier: str
    configuration_identifier: str
    reference_method: ReferenceMethod
    solvent_name: str = Field(min_length=1, max_length=80)
    solvent_model: Literal["IEF-PCM", "C-PCM", "SS(V)PE", "COSMO"]
    dielectric_constant: float = Field(gt=1.0, le=1000.0)
    equilibrium_solvation: bool = True
    converged: Literal[True] = True
    vacuum_total_energy_hartree: float
    solvated_total_energy_hartree: float
    electronic_solvation_contribution_hartree: float
    energy_semantics: Literal["solvated_electronic_energy"] = (
        "solvated_electronic_energy"
    )
    thermal_correction_included: Literal[False] = False
    gibbs_free_energy: Literal[False] = False

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
        return validate_identifier(value, label="implicit-solvent identifier")

    @field_validator("solvent_name")
    @classmethod
    def validate_solvent_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Solvent name cannot be blank.")
        return normalized

    @field_validator(
        "dielectric_constant",
        "vacuum_total_energy_hartree",
        "solvated_total_energy_hartree",
        "electronic_solvation_contribution_hartree",
    )
    @classmethod
    def validate_scalars(cls, value: float) -> float:
        return _finite(value, label="implicit-solvent scalar")

    @model_validator(mode="after")
    def validate_energy_difference(self) -> Self:
        if not math.isclose(
            self.solvated_total_energy_hartree
            - self.vacuum_total_energy_hartree,
            self.electronic_solvation_contribution_hartree,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError("Implicit-solvent energy difference is inconsistent.")
        return self


class QMMMEmbeddingFoundation(CanonicalModel):
    """Real OpenMM point-charge environment prepared for PySCF embedding."""

    schema_version: CapabilityVersion
    embedding_identifier: str
    molecule_identifier: str
    qm_region_preparation_identifier: str
    environment_identifier: str
    simulation_system_identifier: str
    qm_source_particle_count: int = Field(gt=0)
    embedding_sites: tuple[QMMMEmbeddingSite, ...] = Field(min_length=1)
    embedding_total_charge_e: float
    unmodified_embedding_total_charge_e: float | None = None
    boundary_charge_adjustments: tuple[QMMMBoundaryChargeAdjustment, ...] = ()
    electrostatic_embedding_ready: Literal[True] = True
    boundary_policy: Literal["no_covalent_boundary", "charge_shift"] = (
        "no_covalent_boundary"
    )
    charge_model: Literal["openmm_nonbonded_force"] = (
        "openmm_nonbonded_force"
    )

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "embedding_identifier",
        "molecule_identifier",
        "qm_region_preparation_identifier",
        "environment_identifier",
        "simulation_system_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(
            value,
            label="QM/MM embedding identifier",
        )

    @field_validator(
        "embedding_total_charge_e",
        "unmodified_embedding_total_charge_e",
    )
    @classmethod
    def validate_embedding_total_charge(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _finite(
            value,
            label="QM/MM embedding total charge",
        )

    @model_validator(mode="after")
    def validate_embedding(self) -> Self:
        indices = [
            site.site_index
            for site in self.embedding_sites
        ]

        if indices != list(range(len(self.embedding_sites))):
            raise ValueError(
                "QM/MM embedding site indices must be contiguous."
            )

        source_indices = [
            site.source_particle_index
            for site in self.embedding_sites
        ]

        if len(source_indices) != len(set(source_indices)):
            raise ValueError(
                "QM/MM embedding source-particle indices must be unique."
            )

        charge_sum = sum(
            site.charge_e
            for site in self.embedding_sites
        )

        if not math.isclose(
            charge_sum,
            self.embedding_total_charge_e,
            rel_tol=0.0,
            abs_tol=1e-8,
        ):
            raise ValueError(
                "QM/MM embedding total charge must equal "
                "the site-charge sum."
            )

        unmodified_total = (
            self.embedding_total_charge_e
            if self.unmodifiable_total_is_absent
            else self.unmodified_embedding_total_charge_e
        )
        if not math.isclose(
            self.embedding_total_charge_e,
            unmodified_total,
            rel_tol=0.0,
            abs_tol=1e-8,
        ):
            raise ValueError("Boundary charge shifting must conserve MM charge.")

        adjusted_indices = [
            item.source_particle_index for item in self.boundary_charge_adjustments
        ]
        if len(adjusted_indices) != len(set(adjusted_indices)):
            raise ValueError("Boundary charge adjustments must be particle-unique.")
        site_by_source = {
            site.source_particle_index: site for site in self.embedding_sites
        }
        for adjustment in self.boundary_charge_adjustments:
            site = site_by_source.get(adjustment.source_particle_index)
            if site is None or not math.isclose(
                site.charge_e,
                adjustment.adjusted_charge_e,
                rel_tol=0.0,
                abs_tol=1e-10,
            ):
                raise ValueError(
                    "Boundary charge adjustments must match embedding sites."
                )

        if self.boundary_policy == "charge_shift":
            if not self.boundary_charge_adjustments:
                raise ValueError("Charge-shift boundaries require adjustment evidence.")
        elif self.boundary_charge_adjustments:
            raise ValueError("Boundary-free embedding cannot contain charge shifts.")

        return self

    @property
    def unmodifiable_total_is_absent(self) -> bool:
        """Return whether a legacy boundary-free artifact omitted the raw total."""

        return self.unmodified_embedding_total_charge_e is None

class ElectronicQMMMHartreeFockResult(CanonicalModel):
    """Hartree-Fock evidence from PySCF static MM point-charge embedding."""

    schema_version: CapabilityVersion
    result_identifier: str
    molecule_identifier: str
    configuration_identifier: str
    embedding_identifier: str
    reference_method: ReferenceMethod

    converged: Literal[True] = True
    iterations: int = Field(ge=0)

    electron_count: int = Field(gt=0)
    alpha_electron_count: int = Field(ge=0)
    beta_electron_count: int = Field(ge=0)

    atomic_orbital_count: int = Field(gt=0)
    spatial_orbital_count: int = Field(gt=0)

    embedding_site_count: int = Field(gt=0)
    embedding_total_charge_e: float

    qm_nuclear_repulsion_energy_hartree: float
    qm_mm_nuclear_interaction_energy_hartree: float
    embedded_electronic_energy_hartree: float
    embedded_total_energy_hartree: float

    orbital_energies_alpha_hartree: tuple[float, ...] = Field(
        min_length=1
    )
    orbital_energies_beta_hartree: tuple[float, ...] = Field(
        min_length=1
    )
    orbital_occupations_alpha: tuple[float, ...] = Field(
        min_length=1
    )
    orbital_occupations_beta: tuple[float, ...] = Field(
        min_length=1
    )

    mo_coefficients_alpha: ElectronicTensor
    mo_coefficients_beta: ElectronicTensor
    overlap_matrix_ao: ElectronicTensor
    embedded_core_hamiltonian_ao: ElectronicTensor
    density_matrix_alpha_ao: ElectronicTensor
    density_matrix_beta_ao: ElectronicTensor

    energy_model: Literal[
        "pyscf_point_charge_electrostatic_embedding"
    ] = "pyscf_point_charge_electrostatic_embedding"

    mm_internal_energy_included: Literal[False] = False
    mm_mm_electrostatics_included: Literal[False] = False
    qm_mm_vdw_included: Literal[False] = False

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "result_identifier",
        "molecule_identifier",
        "configuration_identifier",
        "embedding_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(
            value,
            label="QM/MM Hartree-Fock identifier",
        )

    @field_validator(
        "embedding_total_charge_e",
        "qm_nuclear_repulsion_energy_hartree",
        "qm_mm_nuclear_interaction_energy_hartree",
        "embedded_electronic_energy_hartree",
        "embedded_total_energy_hartree",
    )
    @classmethod
    def validate_energy_scalars(cls, value: float) -> float:
        return _finite(
            value,
            label="QM/MM Hartree-Fock scalar",
        )

    @field_validator(
        "orbital_energies_alpha_hartree",
        "orbital_energies_beta_hartree",
        "orbital_occupations_alpha",
        "orbital_occupations_beta",
    )
    @classmethod
    def validate_orbital_values(
        cls,
        value: tuple[float, ...],
    ) -> tuple[float, ...]:
        return tuple(
            _finite(
                item,
                label="QM/MM Hartree-Fock orbital value",
            )
            for item in value
        )

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if (
            self.alpha_electron_count
            + self.beta_electron_count
            != self.electron_count
        ):
            raise ValueError(
                "QM/MM Hartree-Fock spin populations must sum "
                "to the electron count."
            )

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
            raise ValueError(
                "QM/MM Hartree-Fock orbital vectors must match "
                "the spatial-orbital count."
            )

        coefficient_shape = (
            self.atomic_orbital_count,
            self.spatial_orbital_count,
        )

        if (
            self.mo_coefficients_alpha.shape
            != coefficient_shape
            or self.mo_coefficients_beta.shape
            != coefficient_shape
        ):
            raise ValueError(
                "QM/MM Hartree-Fock coefficient matrices "
                "have invalid shapes."
            )

        ao_shape = (
            self.atomic_orbital_count,
            self.atomic_orbital_count,
        )

        for tensor in (
            self.overlap_matrix_ao,
            self.embedded_core_hamiltonian_ao,
            self.density_matrix_alpha_ao,
            self.density_matrix_beta_ao,
        ):
            if tensor.shape != ao_shape:
                raise ValueError(
                    "QM/MM Hartree-Fock AO matrices have invalid shapes."
                )

        reconstructed_total = (
            self.embedded_electronic_energy_hartree
            + self.qm_nuclear_repulsion_energy_hartree
            + self.qm_mm_nuclear_interaction_energy_hartree
        )

        if not math.isclose(
            reconstructed_total,
            self.embedded_total_energy_hartree,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "QM/MM Hartree-Fock energy components do not "
                "sum to the embedded total energy."
            )

        if not math.isclose(
            sum(self.orbital_occupations_alpha),
            self.alpha_electron_count,
            abs_tol=1e-7,
        ):
            raise ValueError(
                "QM/MM alpha occupations do not match "
                "the alpha-electron count."
            )

        if not math.isclose(
            sum(self.orbital_occupations_beta),
            self.beta_electron_count,
            abs_tol=1e-7,
        ):
            raise ValueError(
                "QM/MM beta occupations do not match "
                "the beta-electron count."
            )

        return self
