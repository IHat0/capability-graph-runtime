"""Pure, immutable contracts for a declared quantum-chemistry experiment."""

from __future__ import annotations

import math
from typing import Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import CanonicalModel, ScientificExperiment
from cgr.science.canonical import validate_identifier, validate_sha256

_ELEMENTS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni "
    "Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I "
    "Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt "
    "Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr "
    "Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og"
).split()
_ATOMIC_NUMBERS = {symbol: number for number, symbol in enumerate(_ELEMENTS, 1)}
_UNITS = {"angstrom", "bohr"}


class CartesianAtom(CanonicalModel):
    """One ordered atom with a stable manifest identifier."""

    atom_identifier: str
    element: str
    coordinates: tuple[float, float, float]

    @field_validator("atom_identifier")
    @classmethod
    def valid_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="atom identifier")

    @field_validator("element")
    @classmethod
    def valid_element(cls, value: str) -> str:
        if value not in _ATOMIC_NUMBERS:
            raise ValueError(f"Unknown element symbol '{value}'.")
        return value

    @field_validator("coordinates")
    @classmethod
    def finite_coordinates(
        cls, value: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        if not all(math.isfinite(component) for component in value):
            raise ValueError("Atomic coordinates must be finite.")
        return value

    @property
    def nuclear_charge(self) -> int:
        return _ATOMIC_NUMBERS[self.element]


class MolecularSystem(CanonicalModel):
    """Exact ordered molecular identity used by the electronic-structure driver."""

    atoms: tuple[CartesianAtom, ...]
    coordinate_unit: str
    molecular_charge: int
    spin_multiplicity: int = Field(gt=0)
    declared_bond_distance: float = Field(gt=0)
    structure_artifact_identifier: str

    @field_validator("coordinate_unit")
    @classmethod
    def valid_unit(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in _UNITS:
            raise ValueError("Coordinate unit must be explicitly 'angstrom' or 'bohr'.")
        return normalized

    @field_validator("structure_artifact_identifier")
    @classmethod
    def valid_structure_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="structure artifact identifier")

    @model_validator(mode="after")
    def validate_physical_identity(self) -> Self:
        if len(self.atoms) != 2:
            raise ValueError("Quantum preflight v1 requires exactly two atoms.")
        identifiers = [atom.atom_identifier for atom in self.atoms]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Atom identifiers must be unique.")
        left, right = self.atoms
        distance = math.sqrt(
            sum((a - b) ** 2 for a, b in zip(left.coordinates, right.coordinates))
        )
        if not math.isclose(distance, self.declared_bond_distance, abs_tol=1e-12):
            raise ValueError("Derived bond distance does not match the declaration.")
        electrons = self.total_electron_count
        unpaired = self.spin_multiplicity - 1
        if unpaired > electrons or (electrons - unpaired) % 2:
            raise ValueError("Electron count and spin multiplicity have incompatible parity.")
        return self

    @property
    def total_electron_count(self) -> int:
        electrons = sum(atom.nuclear_charge for atom in self.atoms) - self.molecular_charge
        if electrons <= 0:
            raise ValueError("Molecular charge leaves no electrons.")
        return electrons

    @property
    def driver_spin(self) -> int:
        """Return PySCF/Qiskit Nature's 2S value."""
        return self.spin_multiplicity - 1


class ElectronicStructureModel(CanonicalModel):
    """Declared classical model and explicit active-space policy."""

    basis_set: str
    reference_method: str
    driver_identifier: str
    frozen_core: bool
    active_electron_count: int = Field(gt=0)
    active_spatial_orbital_count: int = Field(gt=0)
    active_orbital_indices: tuple[int, ...]
    hamiltonian_representation: str

    @field_validator(
        "basis_set", "reference_method", "driver_identifier", "hamiltonian_representation"
    )
    @classmethod
    def identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("active_orbital_indices")
    @classmethod
    def unique_orbitals(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value:
            raise ValueError("Explicit active-orbital indices are required.")
        if any(index < 0 for index in value):
            raise ValueError("Active-orbital indices must be non-negative.")
        if len(value) != len(set(value)):
            raise ValueError("Active-orbital indices must be unique.")
        return value

    @model_validator(mode="after")
    def active_orbital_count_matches(self) -> Self:
        if len(self.active_orbital_indices) != self.active_spatial_orbital_count:
            raise ValueError("Active-orbital count must match the explicit index list.")
        return self


class QuantumModel(CanonicalModel):
    """Deterministic statevector VQE configuration."""

    mapper: str
    ansatz: str
    initial_state: str
    optimizer: str
    optimizer_settings: dict[str, int | float | str | bool]
    initial_point_policy: str
    maximum_iterations: int = Field(gt=0)
    convergence_threshold: float = Field(gt=0)
    random_seed: int = Field(ge=0)
    simulator_type: str

    @field_validator(
        "mapper", "ansatz", "initial_state", "optimizer", "initial_point_policy", "simulator_type"
    )
    @classmethod
    def identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @model_validator(mode="after")
    def bounded_optimizer(self) -> Self:
        declared = self.optimizer_settings.get("maxiter")
        if declared != self.maximum_iterations:
            raise ValueError("Optimizer maxiter must match maximum_iterations.")
        return self


class QuantumVerificationPolicy(CanonicalModel):
    """Fail-closed scientific verification policy."""

    exact_solver: str
    required_energy_fields: tuple[str, ...]
    energy_difference_tolerance_hartree: float = Field(gt=0)
    hermiticity_tolerance: float = Field(gt=0)
    particle_number_tolerance: float = Field(ge=0)
    required_artifact_lineage: bool

    @field_validator("exact_solver")
    @classmethod
    def valid_solver(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("required_energy_fields")
    @classmethod
    def energy_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(set(validate_identifier(item) for item in value)))
        required = {"electronic_energy_hartree", "total_energy_hartree"}
        if not required.issubset(normalized):
            raise ValueError("Both electronic and total energy fields are required.")
        return normalized


class QuantumExecutionPolicy(CanonicalModel):
    """Bounded local runtime policy included in the experiment identity."""

    runtime_identifier: str
    maximum_duration_seconds: int = Field(gt=0)
    maximum_memory_mib: int = Field(gt=0)
    maximum_processes: int = Field(gt=0)
    maximum_result_bytes: int = Field(gt=0)
    maximum_log_bytes: int = Field(gt=0)
    network_disabled: bool
    cpu_limit: float = Field(gt=0)

    @field_validator("runtime_identifier")
    @classmethod
    def valid_runtime(cls, value: str) -> str:
        return validate_identifier(value)


class QuantumChemistryExperiment(CanonicalModel):
    """Versioned LiH experiment layered on CGR's ScientificExperiment contract."""

    experiment_identifier: str
    schema_version: CapabilityVersion
    parent_experiment: ScientificExperiment
    objective_type: str
    requested_observable: str
    molecular_system: MolecularSystem
    electronic_structure: ElectronicStructureModel
    quantum_model: QuantumModel
    verification_policy: QuantumVerificationPolicy
    execution_policy: QuantumExecutionPolicy

    @field_validator("experiment_identifier", "objective_type", "requested_observable")
    @classmethod
    def valid_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @model_validator(mode="after")
    def validate_scientific_policy(self) -> Self:
        if self.schema_version != CapabilityVersion(major=1, minor=0, patch=0):
            raise ValueError("Only quantum-chemistry experiment schema 1.0.0 is supported.")
        if self.objective_type != "molecular_ground_state":
            raise ValueError("Only molecular_ground_state is supported in preflight v1.")
        if self.requested_observable != "total_energy_hartree":
            raise ValueError("The requested observable must unambiguously be total energy.")
        if self.electronic_structure.reference_method != "restricted_hartree_fock":
            raise ValueError("Preflight v1 supports restricted Hartree-Fock only.")
        if self.molecular_system.spin_multiplicity != 1:
            raise ValueError("Restricted Hartree-Fock requires the declared closed-shell singlet.")
        if self.molecular_system.total_electron_count % 2:
            raise ValueError("Restricted Hartree-Fock requires an even electron count.")
        if (
            self.electronic_structure.active_electron_count
            > self.molecular_system.total_electron_count
        ):
            raise ValueError("Active electrons cannot exceed total electrons.")
        if self.electronic_structure.active_electron_count % 2:
            raise ValueError("The singlet active space requires an even electron count.")
        if not self.execution_policy.network_disabled:
            raise ValueError("Trusted quantum preflight execution must disable networking.")
        if not self.parent_experiment.execution_ready:
            raise ValueError("The parent scientific experiment is not execution-ready.")
        if self.parent_experiment.blocking_assumptions:
            raise ValueError("No unresolved blocking assumption may enter execution.")
        return self


class ManifestEnvelope(CanonicalModel):
    """Committed manifest plus an optional expected identity for tamper detection."""

    manifest_schema: str
    experiment: QuantumChemistryExperiment
    expected_experiment_sha256: str | None = None

    @field_validator("manifest_schema")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != "cgr.quantum-preflight-manifest/1.0.0":
            raise ValueError("Unsupported quantum preflight manifest schema.")
        return value

    @field_validator("expected_experiment_sha256")
    @classmethod
    def valid_expected_hash(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @model_validator(mode="after")
    def expected_identity_matches(self) -> Self:
        if (
            self.expected_experiment_sha256 is not None
            and self.expected_experiment_sha256 != self.experiment.fingerprint
        ):
            raise ValueError("Manifest experiment fingerprint is stale or substituted.")
        return self
