"""Engine-neutral classical molecular simulation artifacts for Product Phase 5.3."""

from __future__ import annotations

import math
import re
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import CanonicalModel
from cgr.science.canonical import validate_identifier, validate_sha256

_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_ELEMENT_SYMBOL = re.compile(r"^[A-Za-z]{1,3}$")
_FORCE_FIELD_FILE = re.compile(r"^[A-Za-z0-9_.+/-]+\.xml$")


SimulationEnvironmentType = Literal["vacuum", "solvent", "membrane"]
SimulationNonbondedMethod = Literal[
    "no_cutoff",
    "cutoff_nonperiodic",
    "cutoff_periodic",
    "ewald",
    "pme",
    "ljpme",
]
SimulationConstraintMode = Literal[
    "none", "hydrogen_bonds", "all_bonds", "hydrogen_angles"
]
SimulationEnsemble = Literal["nve", "nvt", "npt"]
SimulationIntegrator = Literal["verlet", "langevin_middle"]
SimulationVelocityInitialization = Literal["maxwell_boltzmann", "input_snapshot"]


def _validate_schema_version(value: CapabilityVersion) -> CapabilityVersion:
    if value != _SCHEMA_VERSION:
        raise ValueError(
            "Classical molecular simulation schema version must be exactly 1.0.0."
        )
    return value


def _finite(value: float, *, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    return value


def _nonnegative(value: float, *, label: str) -> float:
    value = _finite(value, label=label)
    if value < 0:
        raise ValueError(f"{label} cannot be negative.")
    return value


def _positive(value: float, *, label: str) -> float:
    value = _finite(value, label=label)
    if value <= 0:
        raise ValueError(f"{label} must be positive.")
    return value


class MolecularVector3(CanonicalModel):
    """A finite three-dimensional vector with units declared by its parent."""

    x: float
    y: float
    z: float

    @field_validator("x", "y", "z")
    @classmethod
    def validate_component(cls, value: float) -> float:
        return _finite(value, label="Molecular vector component")


class MolecularPeriodicBox(CanonicalModel):
    """Three periodic box vectors in nanometres."""

    vector_a: MolecularVector3
    vector_b: MolecularVector3
    vector_c: MolecularVector3
    unit: Literal["nanometer"] = "nanometer"

    @model_validator(mode="after")
    def validate_nonzero_volume(self) -> Self:
        a = self.vector_a
        b = self.vector_b
        c = self.vector_c
        volume = (
            a.x * (b.y * c.z - b.z * c.y)
            - a.y * (b.x * c.z - b.z * c.x)
            + a.z * (b.x * c.y - b.y * c.x)
        )
        if not math.isfinite(volume) or abs(volume) <= 1e-12:
            raise ValueError("Periodic box vectors must span a nonzero finite volume.")
        return self


class MolecularSimulationAtom(CanonicalModel):
    """One atom in a simulation-ready topology."""

    particle_index: int = Field(ge=0)
    particle_kind: Literal["atom", "extra_particle"]
    atomic_number: int | None = Field(default=None, gt=0)
    element_symbol: str | None = None
    atom_name: str
    residue_index: int = Field(ge=0)
    residue_name: str
    residue_identifier: str
    chain_index: int = Field(ge=0)
    chain_identifier: str
    source_atom_index: int | None = Field(default=None, ge=0)
    formal_charge: int | None = None

    @field_validator("element_symbol")
    @classmethod
    def normalize_element_symbol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not _ELEMENT_SYMBOL.fullmatch(normalized):
            raise ValueError("Element symbol must contain one to three letters.")
        return normalized[0].upper() + normalized[1:].lower()

    @field_validator("atom_name", "residue_name")
    @classmethod
    def validate_labels(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not normalized
            or len(normalized) > 128
            or any(ord(character) < 32 for character in normalized)
        ):
            raise ValueError("Simulation topology labels must be nonempty and bounded.")
        return normalized

    @field_validator("residue_identifier", "chain_identifier")
    @classmethod
    def validate_topology_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="simulation topology identifier")

    @model_validator(mode="after")
    def validate_particle_identity(self) -> Self:
        has_element = self.atomic_number is not None or self.element_symbol is not None
        if self.particle_kind == "atom":
            if self.atomic_number is None or self.element_symbol is None:
                raise ValueError(
                    "Atomic simulation particles require element identity."
                )
        elif has_element:
            raise ValueError("Extra simulation particles cannot declare an element.")
        if self.source_atom_index is not None and self.particle_kind != "atom":
            raise ValueError("Extra simulation particles cannot map to source atoms.")
        if self.formal_charge is not None and self.particle_kind != "atom":
            raise ValueError("Extra simulation particles cannot declare formal charge.")
        return self


class MolecularSimulationBond(CanonicalModel):
    """One topology bond with canonical endpoint ordering."""

    atom_index_a: int = Field(ge=0)
    atom_index_b: int = Field(ge=0)
    order: int | None = Field(default=None, ge=1, le=3)

    @model_validator(mode="before")
    @classmethod
    def canonicalize_endpoints(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        atom_a = value.get("atom_index_a")
        atom_b = value.get("atom_index_b")
        if isinstance(atom_a, int) and isinstance(atom_b, int) and atom_b < atom_a:
            normalized = dict(value)
            normalized["atom_index_a"] = atom_b
            normalized["atom_index_b"] = atom_a
            return normalized
        return value

    @model_validator(mode="after")
    def reject_self_bond(self) -> Self:
        if self.atom_index_a == self.atom_index_b:
            raise ValueError("A simulation bond cannot connect one atom to itself.")
        return self


class MolecularForceFieldSelection(CanonicalModel):
    """Explicit force-field files validated by a scientific engine."""

    schema_version: CapabilityVersion
    selection_identifier: str
    engine_identifier: str
    engine_distribution_version: str = Field(min_length=1, max_length=128)
    engine_build_version: str = Field(min_length=1, max_length=128)
    force_field_files: tuple[str, ...] = Field(min_length=1, max_length=16)
    water_model: str | None = None
    selection_method: Literal["explicit_packaged_xml"] = "explicit_packaged_xml"

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator("selection_identifier", "engine_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="force-field identifier")

    @field_validator("engine_distribution_version", "engine_build_version")
    @classmethod
    def normalize_versions(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Force-field engine versions cannot be blank.")
        return normalized

    @field_validator("force_field_files")
    @classmethod
    def validate_force_field_files(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for item in value:
            item = item.strip().replace("\\", "/")
            if (
                not item
                or item.startswith("/")
                or ":" in item
                or ".." in item.split("/")
                or not _FORCE_FIELD_FILE.fullmatch(item)
            ):
                raise ValueError(
                    "Force-field files must be safe packaged relative XML identifiers."
                )
            normalized.append(item)
        if len(normalized) != len(set(normalized)):
            raise ValueError("Force-field file identifiers must be unique.")
        return tuple(normalized)

    @field_validator("water_model")
    @classmethod
    def validate_water_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_identifier(value, label="water model")


class MolecularEnvironment(CanonicalModel):
    """Prepared vacuum, solvent or membrane environment and exact coordinates."""

    schema_version: CapabilityVersion
    environment_identifier: str
    environment_type: SimulationEnvironmentType
    source_conformer_set_identifier: str
    source_conformer_index: int = Field(ge=0)
    force_field_selection_identifier: str
    coordinate_unit: Literal["nanometer"] = "nanometer"
    atoms: tuple[MolecularSimulationAtom, ...] = Field(min_length=1)
    bonds: tuple[MolecularSimulationBond, ...] = ()
    positions: tuple[MolecularVector3, ...] = Field(min_length=1)
    source_solute_atom_count: int = Field(gt=0)
    periodic_box: MolecularPeriodicBox | None = None
    water_model: str | None = None
    ionic_strength_molar: float | None = None
    positive_ion: str | None = None
    negative_ion: str | None = None
    neutralized: bool | None = None
    solvent_padding_nm: float | None = None
    solvent_box_shape: Literal["cube", "dodecahedron", "octahedron"] | None = None
    lipid_type: str | None = None
    membrane_minimum_padding_nm: float | None = None
    membrane_center_z_nm: float | None = None
    preparation_platform: str | None = None

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "environment_identifier",
        "source_conformer_set_identifier",
        "force_field_selection_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="molecular environment identifier")

    @field_validator("water_model", "lipid_type")
    @classmethod
    def validate_optional_identifiers(cls, value: str | None) -> str | None:
        return (
            validate_identifier(value, label="molecular environment option")
            if value is not None
            else None
        )

    @field_validator("positive_ion", "negative_ion")
    @classmethod
    def validate_ion_names(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or len(normalized) > 32:
            raise ValueError("Molecular ion names must be nonempty and bounded.")
        if any(character.isspace() for character in normalized):
            raise ValueError("Molecular ion names cannot contain whitespace.")
        return normalized

    @field_validator("ionic_strength_molar")
    @classmethod
    def validate_ionic_strength(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _nonnegative(value, label="Ionic strength")

    @field_validator("solvent_padding_nm", "membrane_minimum_padding_nm")
    @classmethod
    def validate_padding(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _positive(value, label="Environment padding")

    @field_validator("membrane_center_z_nm")
    @classmethod
    def validate_membrane_center(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _finite(value, label="Membrane center")

    @field_validator("preparation_platform")
    @classmethod
    def validate_preparation_platform(cls, value: str | None) -> str | None:
        return (
            validate_identifier(value, label="environment preparation platform")
            if value is not None
            else None
        )

    @field_validator("atoms")
    @classmethod
    def order_atoms(
        cls,
        value: tuple[MolecularSimulationAtom, ...],
    ) -> tuple[MolecularSimulationAtom, ...]:
        indices = [atom.particle_index for atom in value]
        if sorted(indices) != list(range(len(value))):
            raise ValueError(
                "Simulation particle indices must be contiguous from zero."
            )
        source_indices = tuple(
            atom.source_atom_index
            for atom in value
            if atom.source_atom_index is not None
        )
        if len(source_indices) != len(set(source_indices)):
            raise ValueError("Simulation source atom indices must be unique.")
        return tuple(sorted(value, key=lambda atom: atom.particle_index))

    @field_validator("bonds")
    @classmethod
    def order_bonds(
        cls,
        value: tuple[MolecularSimulationBond, ...],
    ) -> tuple[MolecularSimulationBond, ...]:
        endpoints = [(bond.atom_index_a, bond.atom_index_b) for bond in value]
        if len(endpoints) != len(set(endpoints)):
            raise ValueError("Simulation topology bonds must have unique endpoints.")
        return tuple(
            sorted(value, key=lambda bond: (bond.atom_index_a, bond.atom_index_b))
        )

    @model_validator(mode="after")
    def validate_environment(self) -> Self:
        atom_count = len(self.atoms)
        if len(self.positions) != atom_count:
            raise ValueError("Environment positions must contain one vector per atom.")
        if self.source_solute_atom_count > atom_count:
            raise ValueError("Environment solute atom count cannot exceed total atoms.")
        if any(
            bond.atom_index_a >= atom_count or bond.atom_index_b >= atom_count
            for bond in self.bonds
        ):
            raise ValueError("Every environment bond endpoint must resolve.")

        environment_fields = (
            self.water_model,
            self.ionic_strength_molar,
            self.positive_ion,
            self.negative_ion,
            self.neutralized,
            self.solvent_padding_nm,
            self.solvent_box_shape,
            self.lipid_type,
            self.membrane_minimum_padding_nm,
            self.membrane_center_z_nm,
            self.preparation_platform,
        )
        if self.environment_type == "vacuum":
            if any(value is not None for value in environment_fields):
                raise ValueError(
                    "Vacuum environments cannot carry solvent, ion or membrane options."
                )
            if self.periodic_box is not None:
                raise ValueError(
                    "Vacuum environments cannot carry a periodic box in v1."
                )
            return self

        if self.periodic_box is None:
            raise ValueError(
                "Solvent and membrane environments require a periodic box."
            )
        if (
            self.water_model is None
            or self.ionic_strength_molar is None
            or self.positive_ion is None
            or self.negative_ion is None
            or self.neutralized is None
        ):
            raise ValueError(
                "Solvent and membrane environments require explicit water and ion options."
            )
        if self.environment_type == "solvent":
            if self.lipid_type is not None:
                raise ValueError(
                    "Solvent-only environments cannot declare a lipid type."
                )
            if (
                self.solvent_padding_nm is None
                or self.solvent_box_shape is None
                or self.membrane_minimum_padding_nm is not None
                or self.membrane_center_z_nm is not None
                or self.preparation_platform is not None
            ):
                raise ValueError(
                    "Solvent environments require explicit padding and box shape only."
                )
            return self
        if (
            self.lipid_type is None
            or self.membrane_minimum_padding_nm is None
            or self.membrane_center_z_nm is None
            or self.preparation_platform is None
            or self.solvent_padding_nm is not None
            or self.solvent_box_shape is not None
        ):
            raise ValueError(
                "Membrane environments require explicit lipid, geometry and platform controls."
            )
        return self


class MolecularSystemConstructionSettings(CanonicalModel):
    """Explicit settings used to parameterize a classical simulation system."""

    nonbonded_method: SimulationNonbondedMethod
    nonbonded_cutoff_nm: float | None = None
    switch_distance_nm: float | None = None
    constraints: SimulationConstraintMode
    rigid_water: bool
    remove_center_of_mass_motion: bool
    hydrogen_mass_amu: float | None = None
    ewald_error_tolerance: float | None = None

    @field_validator("nonbonded_cutoff_nm", "switch_distance_nm", "hydrogen_mass_amu")
    @classmethod
    def validate_optional_positive(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _positive(value, label="Simulation system setting")

    @field_validator("ewald_error_tolerance")
    @classmethod
    def validate_ewald_tolerance(cls, value: float | None) -> float | None:
        if value is None:
            return None
        value = _positive(value, label="Ewald error tolerance")
        if value >= 1:
            raise ValueError("Ewald error tolerance must be less than one.")
        return value

    @model_validator(mode="after")
    def validate_nonbonded_settings(self) -> Self:
        cutoff_methods = {
            "cutoff_nonperiodic",
            "cutoff_periodic",
            "ewald",
            "pme",
            "ljpme",
        }
        if self.nonbonded_method in cutoff_methods:
            if self.nonbonded_cutoff_nm is None:
                raise ValueError(
                    "The selected nonbonded method requires an explicit cutoff."
                )
        elif self.nonbonded_cutoff_nm is not None:
            raise ValueError("No-cutoff systems cannot declare a nonbonded cutoff.")
        if self.switch_distance_nm is not None:
            if self.nonbonded_cutoff_nm is None:
                raise ValueError("Switch distance requires a nonbonded cutoff.")
            if self.switch_distance_nm >= self.nonbonded_cutoff_nm:
                raise ValueError("Switch distance must be below the nonbonded cutoff.")
        ewald_methods = {"ewald", "pme", "ljpme"}
        if self.nonbonded_method in ewald_methods:
            if self.ewald_error_tolerance is None:
                raise ValueError(
                    "Ewald, PME and LJPME systems require an explicit error tolerance."
                )
        elif self.ewald_error_tolerance is not None:
            raise ValueError(
                "Non-Ewald systems cannot carry an unused Ewald error tolerance."
            )
        return self


class MolecularSimulationSystem(CanonicalModel):
    """Generic public manifest for a privately stored engine system state."""

    schema_version: CapabilityVersion
    system_identifier: str
    environment_identifier: str
    force_field_selection_identifier: str
    particle_count: int = Field(gt=0)
    massive_particle_count: int = Field(gt=0)
    constraint_count: int = Field(ge=0)
    degrees_of_freedom: int = Field(gt=0)
    force_kinds: tuple[str, ...] = Field(min_length=1)
    total_mass_amu: float
    periodic: bool
    settings: MolecularSystemConstructionSettings
    engine_identifier: str
    engine_distribution_version: str = Field(min_length=1, max_length=128)
    engine_build_version: str = Field(min_length=1, max_length=128)
    private_state_sha256: str
    private_state_format: Literal["openmm_system_xml"] = "openmm_system_xml"

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "system_identifier",
        "environment_identifier",
        "force_field_selection_identifier",
        "engine_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="simulation system identifier")

    @field_validator("force_kinds")
    @classmethod
    def order_force_kinds(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(
            validate_identifier(item, label="simulation force kind") for item in value
        )
        return tuple(sorted(set(normalized)))

    @field_validator("total_mass_amu")
    @classmethod
    def validate_total_mass(cls, value: float) -> float:
        return _positive(value, label="Simulation system total mass")

    @field_validator("private_state_sha256")
    @classmethod
    def validate_private_state_sha256(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def validate_periodicity(self) -> Self:
        periodic_methods = {"cutoff_periodic", "ewald", "pme", "ljpme"}
        expects_periodic = self.settings.nonbonded_method in periodic_methods
        if self.periodic != expects_periodic:
            raise ValueError(
                "Simulation system periodicity must agree with its nonbonded method."
            )
        if self.massive_particle_count > self.particle_count:
            raise ValueError(
                "Massive simulation particle count cannot exceed total particles."
            )
        expected_degrees = 3 * self.massive_particle_count - self.constraint_count
        if self.settings.remove_center_of_mass_motion:
            expected_degrees -= 3
        if expected_degrees <= 0 or self.degrees_of_freedom != expected_degrees:
            raise ValueError(
                "Simulation degrees of freedom must match masses, constraints and center-of-mass removal."
            )
        return self


class MolecularVelocity(CanonicalModel):
    """One particle velocity in nanometres per picosecond."""

    atom_index: int = Field(ge=0)
    x: float
    y: float
    z: float
    unit: Literal["nanometer_per_picosecond"] = "nanometer_per_picosecond"

    @field_validator("x", "y", "z")
    @classmethod
    def validate_component(cls, value: float) -> float:
        return _finite(value, label="Molecular velocity component")


class MolecularSnapshot(CanonicalModel):
    """One exact simulation state projected to engine-neutral quantities."""

    schema_version: CapabilityVersion
    snapshot_identifier: str
    system_identifier: str
    frame_index: int = Field(ge=0)
    step: int = Field(ge=0)
    time_ps: float
    particle_count: int = Field(gt=0)
    positions: tuple[MolecularVector3, ...] = Field(min_length=1)
    velocities: tuple[MolecularVelocity, ...] = ()
    periodic_box: MolecularPeriodicBox | None = None
    potential_energy_kj_per_mol: float
    kinetic_energy_kj_per_mol: float
    instantaneous_temperature_kelvin: float | None = None
    source_trajectory_identifier: str | None = None

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "snapshot_identifier",
        "system_identifier",
        "source_trajectory_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return (
            validate_identifier(value, label="simulation snapshot identifier")
            if value is not None
            else None
        )

    @field_validator(
        "time_ps",
        "potential_energy_kj_per_mol",
        "kinetic_energy_kj_per_mol",
    )
    @classmethod
    def validate_finite_values(cls, value: float) -> float:
        return _finite(value, label="Simulation snapshot value")

    @field_validator("instantaneous_temperature_kelvin")
    @classmethod
    def validate_temperature(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _nonnegative(value, label="Instantaneous temperature")

    @field_validator("velocities")
    @classmethod
    def order_velocities(
        cls,
        value: tuple[MolecularVelocity, ...],
    ) -> tuple[MolecularVelocity, ...]:
        indices = [velocity.atom_index for velocity in value]
        if indices and sorted(indices) != list(range(len(value))):
            raise ValueError("Snapshot velocity indices must be contiguous from zero.")
        return tuple(sorted(value, key=lambda velocity: velocity.atom_index))

    @model_validator(mode="after")
    def validate_snapshot_shape(self) -> Self:
        if len(self.positions) != self.particle_count:
            raise ValueError("Snapshot positions must contain one vector per particle.")
        if self.velocities and len(self.velocities) != self.particle_count:
            raise ValueError(
                "Snapshot velocities must contain one vector per particle."
            )
        if self.time_ps < 0:
            raise ValueError("Snapshot time cannot be negative.")
        if self.kinetic_energy_kj_per_mol < 0:
            raise ValueError("Snapshot kinetic energy cannot be negative.")
        return self


class MolecularMinimizationSettings(CanonicalModel):
    """Explicit controls supplied to one local energy minimization."""

    platform: str
    platform_properties: tuple[str, ...] = ()
    integration_timestep_fs: float
    tolerance_kj_per_mol_nm: float
    maximum_iterations: int = Field(ge=0, le=10_000_000)

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, value: str) -> str:
        return validate_identifier(value, label="OpenMM platform")

    @field_validator("platform_properties")
    @classmethod
    def order_platform_properties(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for item in value:
            item = item.strip()
            if not item or "=" not in item or len(item) > 256:
                raise ValueError(
                    "Platform properties must use bounded nonempty name=value entries."
                )
            name, property_value = item.split("=", 1)
            validate_identifier(name, label="platform property name")
            if not property_value.strip():
                raise ValueError("Platform property values cannot be blank.")
            normalized.append(f"{name}={property_value.strip()}")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Platform properties must be unique.")
        return tuple(sorted(normalized))

    @field_validator("integration_timestep_fs", "tolerance_kj_per_mol_nm")
    @classmethod
    def validate_positive_values(cls, value: float) -> float:
        return _positive(value, label="Energy minimization setting")


class MolecularMinimizationResult(CanonicalModel):
    """Auditable record of a completed local energy minimization."""

    schema_version: CapabilityVersion
    minimization_identifier: str
    system_identifier: str
    environment_identifier: str
    settings: MolecularMinimizationSettings
    snapshot_identifier: str
    initial_potential_energy_kj_per_mol: float
    final_potential_energy_kj_per_mol: float
    energy_change_kj_per_mol: float

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "minimization_identifier",
        "system_identifier",
        "environment_identifier",
        "snapshot_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="energy minimization identifier")

    @field_validator(
        "initial_potential_energy_kj_per_mol",
        "final_potential_energy_kj_per_mol",
        "energy_change_kj_per_mol",
    )
    @classmethod
    def validate_energies(cls, value: float) -> float:
        return _finite(value, label="Energy minimization value")

    @model_validator(mode="after")
    def validate_energy_change(self) -> Self:
        expected = (
            self.final_potential_energy_kj_per_mol
            - self.initial_potential_energy_kj_per_mol
        )
        if not math.isclose(
            self.energy_change_kj_per_mol,
            expected,
            rel_tol=1e-10,
            abs_tol=1e-8,
        ):
            raise ValueError(
                "Minimization energy change must equal final minus initial."
            )
        return self


class MolecularDynamicsSettings(CanonicalModel):
    """All scientifically meaningful controls for one molecular dynamics run."""

    ensemble: SimulationEnsemble
    integrator: SimulationIntegrator
    timestep_fs: float
    steps: int = Field(gt=0, le=10_000_000)
    initial_velocity_source: SimulationVelocityInitialization
    initial_temperature_kelvin: float | None = None
    target_temperature_kelvin: float | None = None
    friction_per_ps: float | None = None
    pressure_bar: float | None = None
    barostat_interval_steps: int | None = Field(default=None, gt=0)
    random_seed: int | None = Field(default=None, ge=1, le=2_147_483_647)
    platform: str
    platform_properties: tuple[str, ...] = ()

    @field_validator("timestep_fs")
    @classmethod
    def validate_positive_values(cls, value: float) -> float:
        return _positive(value, label="Molecular dynamics setting")

    @field_validator(
        "initial_temperature_kelvin",
        "target_temperature_kelvin",
        "friction_per_ps",
        "pressure_bar",
    )
    @classmethod
    def validate_optional_positive_values(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _positive(value, label="Molecular dynamics setting")

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, value: str) -> str:
        return validate_identifier(value, label="OpenMM platform")

    @field_validator("platform_properties")
    @classmethod
    def order_platform_properties(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for item in value:
            item = item.strip()
            if not item or "=" not in item or len(item) > 256:
                raise ValueError(
                    "Platform properties must use bounded nonempty name=value entries."
                )
            name, property_value = item.split("=", 1)
            validate_identifier(name, label="platform property name")
            if not property_value.strip():
                raise ValueError("Platform property values cannot be blank.")
            normalized.append(f"{name}={property_value.strip()}")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Platform properties must be unique.")
        return tuple(sorted(normalized))

    @model_validator(mode="after")
    def validate_ensemble_controls(self) -> Self:
        if self.initial_velocity_source == "maxwell_boltzmann":
            if self.initial_temperature_kelvin is None or self.random_seed is None:
                raise ValueError(
                    "Maxwell-Boltzmann velocity initialization requires temperature and random seed."
                )
        elif self.initial_temperature_kelvin is not None:
            raise ValueError(
                "Input-snapshot velocities cannot carry an unused initialization temperature."
            )

        if self.ensemble == "nve":
            if self.integrator != "verlet":
                raise ValueError("NVE dynamics requires the Verlet integrator.")
            if any(
                value is not None
                for value in (
                    self.target_temperature_kelvin,
                    self.friction_per_ps,
                    self.pressure_bar,
                    self.barostat_interval_steps,
                )
            ):
                raise ValueError(
                    "NVE dynamics cannot carry thermostat or barostat controls."
                )
            if (
                self.initial_velocity_source == "input_snapshot"
                and self.random_seed is not None
            ):
                raise ValueError(
                    "Deterministic NVE continuation from input velocities cannot carry an unused random seed."
                )
            return self

        if self.integrator != "langevin_middle":
            raise ValueError(
                "NVT and NPT dynamics require Langevin-middle integration."
            )
        if self.target_temperature_kelvin is None or self.friction_per_ps is None:
            raise ValueError("NVT and NPT dynamics require temperature and friction.")
        if self.random_seed is None:
            raise ValueError(
                "NVT and NPT dynamics require an explicit nonzero random seed."
            )
        if self.ensemble == "nvt":
            if (
                self.pressure_bar is not None
                or self.barostat_interval_steps is not None
            ):
                raise ValueError("NVT dynamics cannot carry barostat controls.")
            return self

        if self.pressure_bar is None or self.barostat_interval_steps is None:
            raise ValueError("NPT dynamics requires pressure and barostat interval.")
        return self


class MolecularDynamicsRun(CanonicalModel):
    """Bounded record of a completed molecular dynamics segment."""

    schema_version: CapabilityVersion
    run_identifier: str
    system_identifier: str
    settings: MolecularDynamicsSettings
    initial_snapshot_identifier: str | None = None
    final_snapshot_identifier: str
    completed_steps: int = Field(gt=0)
    simulated_time_ps: float

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "run_identifier",
        "system_identifier",
        "initial_snapshot_identifier",
        "final_snapshot_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return (
            validate_identifier(value, label="molecular dynamics identifier")
            if value is not None
            else None
        )

    @field_validator("simulated_time_ps")
    @classmethod
    def validate_simulated_time(cls, value: float) -> float:
        return _positive(value, label="Simulated time")

    @model_validator(mode="after")
    def validate_completion(self) -> Self:
        if self.completed_steps != self.settings.steps:
            raise ValueError("Completed steps must equal the requested MD steps.")
        expected_time = self.settings.steps * self.settings.timestep_fs / 1000.0
        if not math.isclose(
            self.simulated_time_ps, expected_time, rel_tol=1e-12, abs_tol=1e-9
        ):
            raise ValueError("Simulated time must agree with steps and timestep.")
        return self


class MolecularTrajectory(CanonicalModel):
    """Ordered snapshots from one explicitly configured molecular dynamics run."""

    schema_version: CapabilityVersion
    trajectory_identifier: str
    system_identifier: str
    settings: MolecularDynamicsSettings
    report_interval_steps: int = Field(gt=0)
    snapshots: tuple[MolecularSnapshot, ...] = Field(min_length=2, max_length=10_001)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator("trajectory_identifier", "system_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="molecular trajectory identifier")

    @field_validator("snapshots")
    @classmethod
    def order_snapshots(
        cls,
        value: tuple[MolecularSnapshot, ...],
    ) -> tuple[MolecularSnapshot, ...]:
        identifiers = [snapshot.snapshot_identifier for snapshot in value]
        frame_indices = [snapshot.frame_index for snapshot in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Trajectory snapshot identifiers must be unique.")
        if sorted(frame_indices) != list(range(len(value))):
            raise ValueError("Trajectory frame indices must be contiguous from zero.")
        ordered = tuple(sorted(value, key=lambda snapshot: snapshot.frame_index))
        steps = [snapshot.step for snapshot in ordered]
        if any(next_step <= step for step, next_step in zip(steps, steps[1:])):
            raise ValueError("Trajectory snapshot steps must increase strictly.")
        return ordered

    @model_validator(mode="after")
    def validate_trajectory(self) -> Self:
        if self.snapshots[0].step != 0:
            raise ValueError("Trajectory recording must include the initial step zero.")
        if self.snapshots[-1].step != self.settings.steps:
            raise ValueError(
                "Trajectory recording must include the requested final step."
            )
        if any(
            snapshot.system_identifier != self.system_identifier
            for snapshot in self.snapshots
        ):
            raise ValueError(
                "Every trajectory snapshot must reference the same system."
            )
        particle_counts = {snapshot.particle_count for snapshot in self.snapshots}
        if len(particle_counts) != 1:
            raise ValueError("Trajectory particle count cannot change between frames.")
        return self


class MolecularEnsembleSummary(CanonicalModel):
    """Deterministic aggregate statistics for a molecular trajectory."""

    schema_version: CapabilityVersion
    ensemble_identifier: str
    trajectory_identifier: str
    system_identifier: str
    ensemble: SimulationEnsemble
    frame_count: int = Field(gt=1)
    first_step: int = Field(ge=0)
    last_step: int = Field(gt=0)
    mean_potential_energy_kj_per_mol: float
    standard_deviation_potential_energy_kj_per_mol: float
    mean_kinetic_energy_kj_per_mol: float
    standard_deviation_kinetic_energy_kj_per_mol: float
    mean_temperature_kelvin: float | None = None
    standard_deviation_temperature_kelvin: float | None = None

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator(
        "ensemble_identifier",
        "trajectory_identifier",
        "system_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="molecular ensemble identifier")

    @field_validator(
        "mean_potential_energy_kj_per_mol",
        "standard_deviation_potential_energy_kj_per_mol",
        "mean_kinetic_energy_kj_per_mol",
        "standard_deviation_kinetic_energy_kj_per_mol",
        "mean_temperature_kelvin",
        "standard_deviation_temperature_kelvin",
    )
    @classmethod
    def validate_statistics(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _finite(value, label="Molecular ensemble statistic")

    @model_validator(mode="after")
    def validate_statistics_consistency(self) -> Self:
        if self.last_step <= self.first_step:
            raise ValueError("Ensemble summary step range must increase.")
        if self.standard_deviation_potential_energy_kj_per_mol < 0:
            raise ValueError("Potential-energy standard deviation cannot be negative.")
        if self.standard_deviation_kinetic_energy_kj_per_mol < 0:
            raise ValueError("Kinetic-energy standard deviation cannot be negative.")
        if (self.mean_temperature_kelvin is None) != (
            self.standard_deviation_temperature_kelvin is None
        ):
            raise ValueError(
                "Temperature mean and deviation must be declared together."
            )
        if (
            self.mean_temperature_kelvin is not None
            and self.mean_temperature_kelvin < 0
        ):
            raise ValueError("Mean temperature cannot be negative.")
        if (
            self.standard_deviation_temperature_kelvin is not None
            and self.standard_deviation_temperature_kelvin < 0
        ):
            raise ValueError("Temperature standard deviation cannot be negative.")
        return self
