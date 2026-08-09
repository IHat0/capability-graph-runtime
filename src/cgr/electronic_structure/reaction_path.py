"""Canonical transition-state, frequency, and reaction-path evidence."""

from __future__ import annotations

import math
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import CanonicalModel
from cgr.science.canonical import sha256_fingerprint, validate_identifier
from cgr.scientific_verification import CalculationEvidence, TransitionStateRequest

from .contracts import ElectronicMolecule, ElectronicTensor

_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)


def _finite(value: float, *, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    return float(value)


def _schema_version(value: CapabilityVersion) -> CapabilityVersion:
    if value != _SCHEMA_VERSION:
        raise ValueError("Reaction-path schema version must be exactly 1.0.0.")
    return value


class ElectronicGradientResult(CanonicalModel):
    """Analytical nuclear gradient for one converged electronic state."""

    schema_version: CapabilityVersion
    result_identifier: str
    molecule_identifier: str
    configuration_identifier: str
    energy_hartree: float
    gradient_hartree_per_bohr: ElectronicTensor
    rms_gradient_hartree_per_bohr: float = Field(ge=0)
    maximum_gradient_hartree_per_bohr: float = Field(ge=0)
    stationary_threshold_hartree_per_bohr: float = Field(gt=0)
    stationary: bool

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        return _schema_version(value)

    @field_validator("result_identifier", "molecule_identifier", "configuration_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="electronic gradient identifier")

    @field_validator(
        "energy_hartree",
        "rms_gradient_hartree_per_bohr",
        "maximum_gradient_hartree_per_bohr",
        "stationary_threshold_hartree_per_bohr",
    )
    @classmethod
    def validate_scalars(cls, value: float) -> float:
        return _finite(value, label="electronic gradient scalar")

    @model_validator(mode="after")
    def validate_shape_and_status(self) -> Self:
        if len(self.gradient_hartree_per_bohr.shape) != 2:
            raise ValueError("Electronic gradients require an atom-by-Cartesian tensor.")
        if self.gradient_hartree_per_bohr.shape[1] != 3:
            raise ValueError("Electronic gradients require three Cartesian components.")
        expected = self.maximum_gradient_hartree_per_bohr <= (
            self.stationary_threshold_hartree_per_bohr
        )
        if self.stationary != expected:
            raise ValueError("Stationary status disagrees with the gradient threshold.")
        return self


class ElectronicFrequencyMode(CanonicalModel):
    """One projected harmonic normal mode."""

    mode_index: int = Field(ge=0)
    wavenumber_cm_inverse: float
    reduced_mass_amu: float = Field(gt=0)
    imaginary: bool
    significant_imaginary: bool
    normalized_displacements: ElectronicTensor
    reaction_coordinate_participation: float | None = Field(default=None, ge=0, le=1)

    @field_validator("wavenumber_cm_inverse", "reduced_mass_amu")
    @classmethod
    def validate_scalars(cls, value: float) -> float:
        return _finite(value, label="frequency-mode scalar")

    @field_validator("reaction_coordinate_participation")
    @classmethod
    def validate_participation(cls, value: float | None) -> float | None:
        return None if value is None else _finite(value, label="mode participation")

    @model_validator(mode="after")
    def validate_mode(self) -> Self:
        if self.normalized_displacements.shape[-1:] != (3,):
            raise ValueError("Normal-mode displacements require Cartesian triples.")
        if self.imaginary != (self.wavenumber_cm_inverse < 0):
            raise ValueError("Imaginary-mode flag disagrees with its wavenumber.")
        if self.significant_imaginary and (
            not self.imaginary or self.wavenumber_cm_inverse > -20.0
        ):
            raise ValueError("A significant imaginary mode must be at most -20 cm^-1.")
        return self


class ElectronicFrequencyAnalysis(CanonicalModel):
    """Analytical Hessian and projected harmonic-frequency characterization."""

    schema_version: CapabilityVersion
    analysis_identifier: str
    molecule_identifier: str
    configuration_identifier: str
    gradient_result_identifier: str
    hessian_hartree_per_bohr2: ElectronicTensor
    modes: tuple[ElectronicFrequencyMode, ...] = Field(min_length=1)
    significant_imaginary_threshold_cm_inverse: float = Field(default=20.0, gt=0)
    significant_imaginary_mode_count: int = Field(ge=0)
    exactly_one_significant_imaginary_mode: bool
    thermal_corrections_calculated: Literal[False] = False
    characterization_surface: Literal["electronic_cluster", "hybrid_qmmm"] = (
        "electronic_cluster"
    )
    hessian_method: Literal[
        "analytical_electronic_hessian",
        "finite_difference_hybrid_gradients",
    ] = "analytical_electronic_hessian"
    finite_difference_displacement_bohr: float | None = Field(default=None, gt=0)
    hybrid_gradient_evaluation_count: int = Field(default=0, ge=0)
    full_particle_count: int | None = Field(default=None, gt=0)
    movable_particle_indices: tuple[int, ...] = ()
    frozen_particle_indices: tuple[int, ...] = ()

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        return _schema_version(value)

    @field_validator(
        "analysis_identifier",
        "molecule_identifier",
        "configuration_identifier",
        "gradient_result_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="frequency-analysis identifier")

    @field_validator("significant_imaginary_threshold_cm_inverse")
    @classmethod
    def validate_threshold(cls, value: float) -> float:
        return _finite(value, label="imaginary-frequency threshold")

    @model_validator(mode="after")
    def validate_analysis(self) -> Self:
        indices = [mode.mode_index for mode in self.modes]
        if indices != list(range(len(self.modes))):
            raise ValueError("Frequency modes must be contiguous and ordered.")
        count = sum(mode.significant_imaginary for mode in self.modes)
        if count != self.significant_imaginary_mode_count:
            raise ValueError("Imaginary-mode count disagrees with mode evidence.")
        if self.exactly_one_significant_imaginary_mode != (count == 1):
            raise ValueError("Exactly-one-imaginary status is inconsistent.")
        if self.characterization_surface == "hybrid_qmmm":
            if (
                self.hessian_method != "finite_difference_hybrid_gradients"
                or self.finite_difference_displacement_bohr is None
                or self.hybrid_gradient_evaluation_count < 2
                or self.full_particle_count is None
                or not self.movable_particle_indices
            ):
                raise ValueError("Hybrid frequencies require finite-difference hybrid-gradient evidence.")
            covered = set(self.movable_particle_indices) | set(self.frozen_particle_indices)
            if covered != set(range(self.full_particle_count)):
                raise ValueError("Hybrid frequency regions must cover the full system.")
        return self


class ElectronicTransitionStateSearch(CanonicalModel):
    """geomeTRIC saddle-point search result, not yet a verified TS claim."""

    schema_version: CapabilityVersion
    search_identifier: str
    initial_molecule_identifier: str
    configuration_identifier: str
    optimizer: Literal["geometric"] = "geometric"
    optimizer_version: str
    converged: bool
    maximum_steps: int = Field(gt=0)
    intended_reaction_atom_pair: tuple[int, int]
    optimized_molecule: ElectronicMolecule
    final_energy_hartree: float
    final_rms_gradient_hartree_per_bohr: float = Field(ge=0)
    final_maximum_gradient_hartree_per_bohr: float = Field(ge=0)
    transition_state_verified: Literal[False] = False
    optimization_surface: Literal["electronic_cluster", "hybrid_qmmm"] = (
        "electronic_cluster"
    )
    hybrid_formulation_identifier: str | None = None
    qm_region_identifier: str | None = None
    environment_identifier: str | None = None
    simulation_system_identifier: str | None = None
    boundary_treatment: str | None = None
    optimized_full_geometry_angstrom: ElectronicTensor | None = None
    final_full_gradient_hartree_per_bohr: ElectronicTensor | None = None
    full_particle_count: int | None = Field(default=None, gt=0)
    movable_particle_indices: tuple[int, ...] = ()
    restrained_particle_indices: tuple[int, ...] = ()
    frozen_particle_indices: tuple[int, ...] = ()
    region_selection_method: str | None = None
    restraint_force_constant_hartree_per_bohr2: float | None = Field(
        default=None, ge=0
    )
    hybrid_gradient_evaluation_count: int = Field(default=0, ge=0)
    hybrid_scf_recovery_count: int = Field(default=0, ge=0)
    energy_history_hartree: tuple[float, ...] = ()
    gradient_norm_history_hartree_per_bohr: tuple[float, ...] = ()
    optimization_iteration_count: int = Field(default=0, ge=0)
    coordinate_converged: bool | None = None
    termination_reason: str | None = None

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        return _schema_version(value)

    @field_validator("search_identifier", "initial_molecule_identifier", "configuration_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="transition-state search identifier")

    @field_validator(
        "final_energy_hartree",
        "final_rms_gradient_hartree_per_bohr",
        "final_maximum_gradient_hartree_per_bohr",
    )
    @classmethod
    def validate_scalars(cls, value: float) -> float:
        return _finite(value, label="transition-state search scalar")

    @field_validator("intended_reaction_atom_pair")
    @classmethod
    def validate_atom_pair(cls, value: tuple[int, int]) -> tuple[int, int]:
        if len(value) != 2 or value[0] < 0 or value[1] < 0 or value[0] == value[1]:
            raise ValueError("A transition-state coordinate needs two distinct atoms.")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def validate_hybrid_surface(self) -> Self:
        if self.optimization_surface != "hybrid_qmmm":
            return self
        required = (
            self.hybrid_formulation_identifier,
            self.qm_region_identifier,
            self.environment_identifier,
            self.simulation_system_identifier,
            self.boundary_treatment,
            self.optimized_full_geometry_angstrom,
            self.final_full_gradient_hartree_per_bohr,
            self.full_particle_count,
            self.region_selection_method,
            self.coordinate_converged,
            self.termination_reason,
        )
        if any(value is None for value in required):
            raise ValueError("Hybrid TS searches require complete surface and region evidence.")
        if self.hybrid_gradient_evaluation_count <= 1:
            raise ValueError("Hybrid TS optimization must evaluate the hybrid gradient iteratively.")
        if len(self.energy_history_hartree) != self.hybrid_gradient_evaluation_count:
            raise ValueError("Hybrid TS energy history must cover every potential evaluation.")
        if len(self.gradient_norm_history_hartree_per_bohr) != self.hybrid_gradient_evaluation_count:
            raise ValueError("Hybrid TS gradient history must cover every potential evaluation.")
        regions = (
            set(self.movable_particle_indices),
            set(self.restrained_particle_indices),
            set(self.frozen_particle_indices),
        )
        if any(left & right for index, left in enumerate(regions) for right in regions[index + 1:]):
            raise ValueError("Hybrid TS optimization regions must be disjoint.")
        if set().union(*regions) != set(range(self.full_particle_count or 0)):
            raise ValueError("Hybrid TS optimization regions must cover every real particle.")
        if not self.movable_particle_indices:
            raise ValueError("Hybrid TS optimization requires movable reaction particles.")
        if self.optimized_full_geometry_angstrom.shape != (self.full_particle_count, 3):
            raise ValueError("Hybrid TS full geometry has inconsistent dimensions.")
        if self.final_full_gradient_hartree_per_bohr.shape != (self.full_particle_count, 3):
            raise ValueError("Hybrid TS full gradient has inconsistent dimensions.")
        return self


class ElectronicReactionPathPoint(CanonicalModel):
    """One point on a forward or reverse mass-weighted descent path."""

    direction: Literal["forward", "reverse"]
    step_index: int = Field(ge=0)
    molecule: ElectronicMolecule
    energy_hartree: float
    rms_gradient_hartree_per_bohr: float = Field(ge=0)
    full_geometry_angstrom: ElectronicTensor | None = None
    hybrid_energy_hartree: float | None = None

    @field_validator("energy_hartree", "rms_gradient_hartree_per_bohr")
    @classmethod
    def validate_scalars(cls, value: float) -> float:
        return _finite(value, label="reaction-path scalar")


class ElectronicReactionPathResult(CanonicalModel):
    """Bounded forward/reverse path confirmation from a characterized saddle."""

    schema_version: CapabilityVersion
    path_identifier: str
    transition_state_search_identifier: str
    frequency_analysis_identifier: str
    method: Literal[
        "mass_weighted_steepest_descent",
        "hybrid_qmmm_mass_weighted_steepest_descent",
    ]
    step_size_bohr: float = Field(gt=0)
    points: tuple[ElectronicReactionPathPoint, ...] = Field(min_length=4)
    forward_energy_decreased: bool
    reverse_energy_decreased: bool
    distinct_endpoints: bool
    path_confirmation_passed: bool
    path_surface: Literal["electronic_cluster", "hybrid_qmmm"] = "electronic_cluster"
    hybrid_gradient_evaluation_count: int = Field(default=0, ge=0)
    transition_state_hybrid_energy_hartree: float | None = None
    forward_endpoint_hybrid_energy_hartree: float | None = None
    reverse_endpoint_hybrid_energy_hartree: float | None = None

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        return _schema_version(value)

    @field_validator(
        "path_identifier",
        "transition_state_search_identifier",
        "frequency_analysis_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="reaction-path identifier")

    @field_validator("step_size_bohr")
    @classmethod
    def validate_step(cls, value: float) -> float:
        return _finite(value, label="reaction-path step")

    @model_validator(mode="after")
    def validate_confirmation(self) -> Self:
        expected = (
            self.forward_energy_decreased
            and self.reverse_energy_decreased
            and self.distinct_endpoints
        )
        if self.path_confirmation_passed != expected:
            raise ValueError("Reaction-path confirmation status is inconsistent.")
        for direction in ("forward", "reverse"):
            steps = [point.step_index for point in self.points if point.direction == direction]
            if steps != list(range(len(steps))):
                raise ValueError("Reaction-path steps must be contiguous per direction.")
        if self.path_surface == "hybrid_qmmm":
            if (
                self.method != "hybrid_qmmm_mass_weighted_steepest_descent"
                or self.hybrid_gradient_evaluation_count < len(self.points)
                or self.transition_state_hybrid_energy_hartree is None
                or self.forward_endpoint_hybrid_energy_hartree is None
                or self.reverse_endpoint_hybrid_energy_hartree is None
                or any(point.hybrid_energy_hartree is None for point in self.points)
                or any(point.full_geometry_angstrom is None for point in self.points)
            ):
                raise ValueError("Hybrid reaction paths require full hybrid energy/geometry evidence.")
        return self


def transition_state_verification_request(
    *,
    search: ElectronicTransitionStateSearch,
    frequency: ElectronicFrequencyAnalysis,
    reaction_path: ElectronicReactionPathResult,
    maximum_gradient_norm_hartree_per_bohr: float,
) -> TransitionStateRequest:
    """Bridge native TS evidence into the existing seven-dimension verifier."""

    if (
        frequency.molecule_identifier != search.optimized_molecule.molecule_identifier
        or reaction_path.transition_state_search_identifier != search.search_identifier
        or reaction_path.frequency_analysis_identifier != frequency.analysis_identifier
    ):
        raise ValueError("Transition-state verification evidence identities disagree.")
    if maximum_gradient_norm_hartree_per_bohr <= 0.0:
        raise ValueError("Transition-state gradient tolerance must be positive.")
    evidence_sha = sha256_fingerprint({
        "search": search.fingerprint,
        "frequency": frequency.fingerprint,
        "reaction_path": reaction_path.fingerprint,
    })
    calculation = CalculationEvidence(
        calculation_identifier=f"calculation-{search.search_identifier}",
        artifact_sha256=evidence_sha,
        molecule_identifier=search.optimized_molecule.molecule_identifier,
        geometry_sha256=search.optimized_molecule.fingerprint,
        method_identifier=f"method-{search.configuration_identifier}",
        method_sha256=sha256_fingerprint(search.configuration_identifier),
        execution_completed=True,
        execution_successful=search.converged,
        identity_valid=True,
        numerically_converged=search.converged,
        values_finite=True,
        authorized=True,
        total_energy_hartree=search.final_energy_hartree,
        metadata={
            "frequency_analysis_identifier": frequency.analysis_identifier,
            "reaction_path_identifier": reaction_path.path_identifier,
        },
    )
    return TransitionStateRequest(
        request_identifier=f"verification-{search.search_identifier}",
        subject_identifier=search.search_identifier,
        calculations=(calculation,),
        imaginary_frequencies_cm_inverse=tuple(
            mode.wavenumber_cm_inverse for mode in frequency.modes if mode.imaginary
        ),
        gradient_norm_hartree_per_bohr=search.final_rms_gradient_hartree_per_bohr,
        maximum_gradient_norm_hartree_per_bohr=maximum_gradient_norm_hartree_per_bohr,
        minimum_imaginary_frequency_magnitude_cm_inverse=(
            frequency.significant_imaginary_threshold_cm_inverse
        ),
        connects_reactant_and_product=reaction_path.path_confirmation_passed,
        authorization_required=True,
        metadata={
            "transition_state_search_identifier": search.search_identifier,
            "path_confirmation_passed": reaction_path.path_confirmation_passed,
        },
    )
