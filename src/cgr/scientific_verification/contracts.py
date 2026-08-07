"""Engine-neutral contracts for Phase 6 scientific verification."""

from __future__ import annotations

import math
from enum import Enum
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import BoundedMetadata, CanonicalModel
from cgr.science.canonical import (
    validate_bounded_metadata,
    validate_identifier,
    validate_sha256,
)

_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)


class VerificationDimension(str, Enum):
    """Common verification dimensions required for every scientific objective."""

    EXECUTION_INTEGRITY = "execution_integrity"
    IDENTITY_INTEGRITY = "identity_integrity"
    NUMERICAL_INTEGRITY = "numerical_integrity"
    SCIENTIFIC_PLAUSIBILITY = "scientific_plausibility"
    CROSS_CALCULATION_COMPARABILITY = "cross_calculation_comparability"
    UNCERTAINTY = "uncertainty"
    AUTHORIZATION = "authorization"


VERIFICATION_DIMENSION_ORDER = (
    VerificationDimension.EXECUTION_INTEGRITY,
    VerificationDimension.IDENTITY_INTEGRITY,
    VerificationDimension.NUMERICAL_INTEGRITY,
    VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
    VerificationDimension.CROSS_CALCULATION_COMPARABILITY,
    VerificationDimension.UNCERTAINTY,
    VerificationDimension.AUTHORIZATION,
)


class VerificationOutcome(str, Enum):
    """Disposition for one dimension or an aggregate verification report."""

    PASSED = "passed"
    FAILED = "failed"


class ScientificVerificationSeverity(str, Enum):
    """Severity of one structured verification finding."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ObjectiveVerifierFamily(str, Enum):
    """Built-in Phase 6 objective-specific verifier families."""

    ENERGY_COMPARISON = "energy_comparison"
    POTENTIAL_ENERGY_CURVE = "potential_energy_curve"
    INTERACTION_ENERGY = "interaction_energy"
    TRANSITION_STATE = "transition_state"
    CONFORMER_COMPARISON = "conformer_comparison"
    METAL_CENTRE = "metal_centre"
    MOLECULAR_DYNAMICS = "molecular_dynamics"
    CANDIDATE_RANKING = "candidate_ranking"


def _finite(value: float, *, label: str) -> float:
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{label} must be finite.")
    return normalized


def _schema_version(value: CapabilityVersion) -> CapabilityVersion:
    if value != _SCHEMA_VERSION:
        raise ValueError(
            "Scientific-verification schema version must be exactly 1.0.0."
        )
    return value


class ScientificVerificationFinding(CanonicalModel):
    """One structured, dimension-bound scientific verification finding."""

    finding_identifier: str
    dimension: VerificationDimension
    severity: ScientificVerificationSeverity
    message: str = Field(min_length=1, max_length=4096)
    blocking: bool
    subject_identifier: str | None = None
    expected: str | int | float | bool | None = None
    observed: str | int | float | bool | None = None
    evidence_identifiers: tuple[str, ...] = ()

    @field_validator("finding_identifier", "subject_identifier")
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_identifier(value, label="verification finding identifier")

    @field_validator("expected", "observed")
    @classmethod
    def validate_scalar(cls, value: str | int | float | bool | None):
        if isinstance(value, float):
            return _finite(value, label="Verification finding scalar")
        return value

    @field_validator("evidence_identifiers")
    @classmethod
    def order_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            sorted(
                set(
                    validate_identifier(item, label="verification evidence identifier")
                    for item in value
                )
            )
        )

    @model_validator(mode="after")
    def validate_blocking_severity(self) -> Self:
        if self.blocking and self.severity is not ScientificVerificationSeverity.ERROR:
            raise ValueError("Blocking verification findings must have error severity.")
        return self


class DimensionVerificationResult(CanonicalModel):
    """Verification disposition for exactly one common scientific dimension."""

    dimension: VerificationDimension
    outcome: VerificationOutcome
    findings: tuple[ScientificVerificationFinding, ...] = ()
    summary: str = Field(min_length=1, max_length=4096)
    evidence_identifiers: tuple[str, ...] = ()
    metrics: BoundedMetadata = Field(default_factory=dict)

    @field_validator("findings")
    @classmethod
    def order_findings(
        cls, value: tuple[ScientificVerificationFinding, ...]
    ) -> tuple[ScientificVerificationFinding, ...]:
        unique = {item.fingerprint: item for item in value}
        return tuple(
            sorted(
                unique.values(),
                key=lambda item: (item.finding_identifier, item.fingerprint),
            )
        )

    @field_validator("evidence_identifiers")
    @classmethod
    def order_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            sorted(
                set(
                    validate_identifier(item, label="verification evidence identifier")
                    for item in value
                )
            )
        )

    @field_validator("metrics")
    @classmethod
    def validate_metrics(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)

    @model_validator(mode="after")
    def validate_dimension_result(self) -> Self:
        if any(item.dimension is not self.dimension for item in self.findings):
            raise ValueError(
                "Dimension results may contain findings from one dimension only."
            )
        blocking = any(item.blocking for item in self.findings)
        if blocking != (self.outcome is VerificationOutcome.FAILED):
            raise ValueError(
                "Dimension outcome must fail exactly when blocking findings are present."
            )
        return self


class ScientificVerificationReport(CanonicalModel):
    """Complete seven-dimension verification result for one scientific objective."""

    schema_version: CapabilityVersion = _SCHEMA_VERSION
    report_identifier: str
    verifier_identifier: str
    verifier_version: CapabilityVersion
    objective_family: str
    subject_identifier: str
    request_sha256: str
    dimension_results: tuple[DimensionVerificationResult, ...]
    overall_outcome: VerificationOutcome
    execution_integrity_passed: bool
    scientific_quality_passed: bool
    authorization_passed: bool
    completed: Literal[True] = True

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        return _schema_version(value)

    @field_validator(
        "report_identifier",
        "verifier_identifier",
        "subject_identifier",
        "objective_family",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="scientific verification identifier")

    @field_validator("request_sha256")
    @classmethod
    def validate_request_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        observed = tuple(item.dimension for item in self.dimension_results)
        if observed != VERIFICATION_DIMENSION_ORDER:
            raise ValueError(
                "Scientific verification reports require all seven dimensions in canonical order."
            )
        by_dimension = {item.dimension: item for item in self.dimension_results}
        failed = any(
            item.outcome is VerificationOutcome.FAILED
            for item in self.dimension_results
        )
        if failed != (self.overall_outcome is VerificationOutcome.FAILED):
            raise ValueError("Overall verification outcome is inconsistent.")

        execution = (
            by_dimension[VerificationDimension.EXECUTION_INTEGRITY].outcome
            is VerificationOutcome.PASSED
        )
        authorization = (
            by_dimension[VerificationDimension.AUTHORIZATION].outcome
            is VerificationOutcome.PASSED
        )
        scientific_dimensions = (
            VerificationDimension.IDENTITY_INTEGRITY,
            VerificationDimension.NUMERICAL_INTEGRITY,
            VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
            VerificationDimension.CROSS_CALCULATION_COMPARABILITY,
            VerificationDimension.UNCERTAINTY,
        )
        quality = all(
            by_dimension[dimension].outcome is VerificationOutcome.PASSED
            for dimension in scientific_dimensions
        )
        if self.execution_integrity_passed != execution:
            raise ValueError("Execution-integrity summary flag is inconsistent.")
        if self.authorization_passed != authorization:
            raise ValueError("Authorization summary flag is inconsistent.")
        if self.scientific_quality_passed != quality:
            raise ValueError("Scientific-quality summary flag is inconsistent.")
        return self


class CalculationEvidence(CanonicalModel):
    """Engine-neutral integrity, energy and uncertainty evidence for one calculation."""

    calculation_identifier: str
    artifact_sha256: str
    molecule_identifier: str
    geometry_sha256: str
    method_identifier: str
    method_sha256: str
    execution_completed: bool
    execution_successful: bool
    identity_valid: bool
    numerically_converged: bool
    values_finite: bool
    authorized: bool
    total_energy_hartree: float | None = None
    uncertainty_hartree: float | None = Field(default=None, ge=0)
    sample_count: int | None = Field(default=None, gt=0)
    metadata: BoundedMetadata = Field(default_factory=dict)

    @field_validator(
        "calculation_identifier",
        "molecule_identifier",
        "method_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="calculation evidence identifier")

    @field_validator("artifact_sha256", "geometry_sha256", "method_sha256")
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("total_energy_hartree", "uncertainty_hartree")
    @classmethod
    def validate_energy_scalar(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _finite(value, label="Calculation evidence energy")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)

    @model_validator(mode="after")
    def validate_execution_state(self) -> Self:
        if self.execution_successful and not self.execution_completed:
            raise ValueError("Successful calculations must be completed.")
        return self


class ScientificVerificationRequest(CanonicalModel):
    """Common request fields consumed by every objective verifier family."""

    schema_version: CapabilityVersion = _SCHEMA_VERSION
    request_identifier: str
    objective_family: str
    subject_identifier: str
    calculations: tuple[CalculationEvidence, ...] = Field(min_length=1)
    require_same_molecule: bool = True
    require_same_method: bool = True
    require_same_geometry: bool = False
    require_uncertainty: bool = False
    maximum_uncertainty_hartree: float | None = Field(default=None, gt=0)
    authorization_required: bool = True
    metadata: BoundedMetadata = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: CapabilityVersion) -> CapabilityVersion:
        return _schema_version(value)

    @field_validator("request_identifier", "subject_identifier", "objective_family")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(
            value, label="scientific verification request identifier"
        )

    @field_validator("calculations")
    @classmethod
    def order_calculations(
        cls, value: tuple[CalculationEvidence, ...]
    ) -> tuple[CalculationEvidence, ...]:
        identifiers = [item.calculation_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError(
                "Scientific verification calculation identifiers must be unique."
            )
        return tuple(sorted(value, key=lambda item: item.calculation_identifier))

    @field_validator("maximum_uncertainty_hartree")
    @classmethod
    def validate_uncertainty_limit(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _finite(value, label="Maximum uncertainty")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)


class EnergyComparisonRequest(ScientificVerificationRequest):
    """Compare two or more energies on a declared comparable footing."""

    objective_family: Literal["energy_comparison"] = "energy_comparison"
    minimum_resolvable_difference_hartree: float = Field(default=1e-6, gt=0)
    expected_lowest_calculation_identifier: str | None = None

    @field_validator("expected_lowest_calculation_identifier")
    @classmethod
    def validate_expected_lowest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_identifier(value, label="expected lowest calculation")

    @model_validator(mode="after")
    def validate_energy_comparison(self) -> Self:
        if len(self.calculations) < 2:
            raise ValueError("Energy comparison requires at least two calculations.")
        if self.expected_lowest_calculation_identifier is not None and (
            self.expected_lowest_calculation_identifier
            not in {item.calculation_identifier for item in self.calculations}
        ):
            raise ValueError(
                "Expected lowest calculation is not present in the request."
            )
        return self


class PotentialEnergyPoint(CanonicalModel):
    """One reaction-coordinate point linked to a calculation."""

    calculation_identifier: str
    reaction_coordinate: float
    reaction_coordinate_unit: str

    @field_validator("calculation_identifier", "reaction_coordinate_unit")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="potential-energy-curve identifier")

    @field_validator("reaction_coordinate")
    @classmethod
    def validate_coordinate(cls, value: float) -> float:
        return _finite(value, label="Reaction coordinate")


class PotentialEnergyCurveRequest(ScientificVerificationRequest):
    """Verify ordered energy evidence across a potential-energy curve."""

    objective_family: Literal["potential_energy_curve"] = "potential_energy_curve"
    points: tuple[PotentialEnergyPoint, ...] = Field(min_length=3)
    maximum_adjacent_energy_jump_hartree: float | None = Field(default=None, gt=0)
    require_interior_minimum: bool = False
    require_same_geometry: bool = False

    @model_validator(mode="after")
    def validate_curve(self) -> Self:
        calculation_ids = {item.calculation_identifier for item in self.calculations}
        point_ids = [item.calculation_identifier for item in self.points]
        if len(point_ids) != len(set(point_ids)):
            raise ValueError("Potential-energy-curve calculation links must be unique.")
        if set(point_ids) != calculation_ids:
            raise ValueError("Every curve point must map exactly one calculation.")
        units = {item.reaction_coordinate_unit for item in self.points}
        if len(units) != 1:
            raise ValueError("Potential-energy-curve coordinates must use one unit.")
        return self


class InteractionEnergyRequest(ScientificVerificationRequest):
    """Verify complex-minus-fragment interaction-energy semantics."""

    objective_family: Literal["interaction_energy"] = "interaction_energy"
    complex_calculation_identifier: str
    fragment_calculation_identifiers: tuple[str, ...] = Field(min_length=2)
    reported_interaction_energy_hartree: float
    consistency_tolerance_hartree: float = Field(default=1e-8, gt=0)
    require_same_molecule: bool = False
    require_same_geometry: bool = False

    @field_validator("complex_calculation_identifier")
    @classmethod
    def validate_complex_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="complex calculation identifier")

    @field_validator("fragment_calculation_identifiers")
    @classmethod
    def validate_fragment_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(
            sorted(
                validate_identifier(item, label="fragment calculation identifier")
                for item in value
            )
        )
        if len(normalized) != len(set(normalized)):
            raise ValueError("Fragment calculation identifiers must be unique.")
        return normalized

    @field_validator(
        "reported_interaction_energy_hartree", "consistency_tolerance_hartree"
    )
    @classmethod
    def validate_interaction_scalar(cls, value: float) -> float:
        return _finite(value, label="Interaction-energy scalar")

    @model_validator(mode="after")
    def validate_interaction_membership(self) -> Self:
        identifiers = {item.calculation_identifier for item in self.calculations}
        expected = {
            self.complex_calculation_identifier,
            *self.fragment_calculation_identifiers,
        }
        if identifiers != expected:
            raise ValueError(
                "Interaction-energy calculations must be exactly the complex and fragments."
            )
        return self


class TransitionStateRequest(ScientificVerificationRequest):
    """Verify first-order saddle-point evidence for one transition-state candidate."""

    objective_family: Literal["transition_state"] = "transition_state"
    imaginary_frequencies_cm_inverse: tuple[float, ...]
    gradient_norm_hartree_per_bohr: float = Field(ge=0)
    maximum_gradient_norm_hartree_per_bohr: float = Field(gt=0)
    minimum_imaginary_frequency_magnitude_cm_inverse: float = Field(gt=0)
    connects_reactant_and_product: bool
    require_same_geometry: bool = False

    @field_validator(
        "imaginary_frequencies_cm_inverse",
    )
    @classmethod
    def validate_frequencies(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        return tuple(_finite(item, label="Imaginary frequency") for item in value)

    @field_validator(
        "gradient_norm_hartree_per_bohr",
        "maximum_gradient_norm_hartree_per_bohr",
        "minimum_imaginary_frequency_magnitude_cm_inverse",
    )
    @classmethod
    def validate_transition_scalar(cls, value: float) -> float:
        return _finite(value, label="Transition-state scalar")

    @model_validator(mode="after")
    def validate_single_calculation(self) -> Self:
        if len(self.calculations) != 1:
            raise ValueError(
                "Transition-state verification requires exactly one calculation."
            )
        return self


class ConformerComparisonRequest(ScientificVerificationRequest):
    """Verify comparable relative energies across distinct conformer geometries."""

    objective_family: Literal["conformer_comparison"] = "conformer_comparison"
    minimum_distinct_geometry_count: int = Field(default=2, ge=2)
    require_same_geometry: bool = False

    @model_validator(mode="after")
    def validate_conformer_count(self) -> Self:
        if len(self.calculations) < 2:
            raise ValueError("Conformer comparison requires at least two calculations.")
        return self


class MetalCentreRequest(ScientificVerificationRequest):
    """Verify explicit metal-centre geometry and electronic-model evidence."""

    objective_family: Literal["metal_centre"] = "metal_centre"
    metal_element: str
    coordination_number: int = Field(gt=0, le=16)
    allowed_coordination_numbers: tuple[int, ...] = Field(min_length=1)
    metal_ligand_distances_angstrom: tuple[float, ...] = Field(min_length=1)
    minimum_metal_ligand_distance_angstrom: float = Field(gt=0)
    maximum_metal_ligand_distance_angstrom: float = Field(gt=0)
    active_space_contains_metal_orbitals: bool
    spin_state_consistent: bool
    require_same_geometry: bool = False

    @field_validator("metal_element")
    @classmethod
    def validate_metal_element(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 3 or not normalized.isalpha():
            raise ValueError(
                "Metal element must be a one-to-three-letter element symbol."
            )
        return normalized[0].upper() + normalized[1:].lower()

    @field_validator("allowed_coordination_numbers")
    @classmethod
    def validate_coordination_numbers(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        normalized = tuple(sorted(set(value)))
        if any(item <= 0 or item > 16 for item in normalized):
            raise ValueError("Allowed coordination numbers must be between 1 and 16.")
        return normalized

    @field_validator(
        "metal_ligand_distances_angstrom",
    )
    @classmethod
    def validate_distances(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        return tuple(_finite(item, label="Metal-ligand distance") for item in value)

    @field_validator(
        "minimum_metal_ligand_distance_angstrom",
        "maximum_metal_ligand_distance_angstrom",
    )
    @classmethod
    def validate_distance_bound(cls, value: float) -> float:
        return _finite(value, label="Metal-ligand distance bound")

    @model_validator(mode="after")
    def validate_metal_request(self) -> Self:
        if len(self.calculations) != 1:
            raise ValueError(
                "Metal-centre verification requires exactly one calculation."
            )
        if self.minimum_metal_ligand_distance_angstrom >= (
            self.maximum_metal_ligand_distance_angstrom
        ):
            raise ValueError("Metal-ligand distance bounds are inverted.")
        if len(self.metal_ligand_distances_angstrom) != self.coordination_number:
            raise ValueError(
                "Metal-ligand distance count must match coordination number."
            )
        return self


class MolecularDynamicsRequest(ScientificVerificationRequest):
    """Verify bounded molecular-dynamics trajectory quality."""

    objective_family: Literal["molecular_dynamics"] = "molecular_dynamics"
    requested_steps: int = Field(gt=0)
    completed_steps: int = Field(ge=0)
    timestep_femtosecond: float = Field(gt=0)
    target_temperature_kelvin: float = Field(gt=0)
    mean_temperature_kelvin: float = Field(gt=0)
    temperature_tolerance_kelvin: float = Field(gt=0)
    energy_drift_hartree_per_picosecond: float
    maximum_absolute_energy_drift_hartree_per_picosecond: float = Field(gt=0)
    equilibration_steps: int = Field(ge=0)
    production_steps: int = Field(ge=0)
    minimum_production_steps: int = Field(gt=0)
    trajectory_values_finite: bool
    constraints_satisfied: bool
    require_same_geometry: bool = False

    @field_validator(
        "timestep_femtosecond",
        "target_temperature_kelvin",
        "mean_temperature_kelvin",
        "temperature_tolerance_kelvin",
        "energy_drift_hartree_per_picosecond",
        "maximum_absolute_energy_drift_hartree_per_picosecond",
    )
    @classmethod
    def validate_md_scalar(cls, value: float) -> float:
        return _finite(value, label="Molecular-dynamics scalar")

    @model_validator(mode="after")
    def validate_md_request(self) -> Self:
        if len(self.calculations) != 1:
            raise ValueError(
                "Molecular-dynamics verification requires exactly one calculation."
            )
        if self.equilibration_steps + self.production_steps > self.completed_steps:
            raise ValueError(
                "MD equilibration plus production steps exceed completed steps."
            )
        return self


class RankedCandidate(CanonicalModel):
    """One candidate score and upstream scientific-verification state."""

    calculation_identifier: str
    score: float
    score_uncertainty: float = Field(ge=0)
    eligible: bool
    scientific_verification_passed: bool

    @field_validator("calculation_identifier")
    @classmethod
    def validate_identifier_field(cls, value: str) -> str:
        return validate_identifier(value, label="ranked candidate identifier")

    @field_validator("score", "score_uncertainty")
    @classmethod
    def validate_score(cls, value: float) -> float:
        return _finite(value, label="Candidate ranking score")


class CandidateRankingRequest(ScientificVerificationRequest):
    """Verify that a ranking is formed from eligible, distinguishable candidates."""

    objective_family: Literal["candidate_ranking"] = "candidate_ranking"
    candidates: tuple[RankedCandidate, ...] = Field(min_length=2)
    higher_score_is_better: bool
    minimum_score_separation: float = Field(default=0.0, ge=0)
    require_all_candidates_scientifically_verified: bool = True
    require_same_geometry: bool = False

    @field_validator("minimum_score_separation")
    @classmethod
    def validate_score_separation(cls, value: float) -> float:
        return _finite(value, label="Minimum candidate score separation")

    @model_validator(mode="after")
    def validate_candidates(self) -> Self:
        calculation_ids = {item.calculation_identifier for item in self.calculations}
        candidate_ids = [item.calculation_identifier for item in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("Candidate ranking identifiers must be unique.")
        if set(candidate_ids) != calculation_ids:
            raise ValueError("Every ranked candidate must map exactly one calculation.")
        return self


ScientificVerificationRequestType = (
    ScientificVerificationRequest
    | EnergyComparisonRequest
    | PotentialEnergyCurveRequest
    | InteractionEnergyRequest
    | TransitionStateRequest
    | ConformerComparisonRequest
    | MetalCentreRequest
    | MolecularDynamicsRequest
    | CandidateRankingRequest
)
