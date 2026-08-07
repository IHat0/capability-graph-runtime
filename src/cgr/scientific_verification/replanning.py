"""Diagnosis, bounded scientific replanning, and evidence comparison."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.science import BoundedMetadata, CanonicalModel
from cgr.science.canonical import (
    sha256_fingerprint,
    validate_bounded_metadata,
    validate_identifier,
    validate_sha256,
)

from .contracts import (
    CandidateRankingRequest,
    ConformerComparisonRequest,
    EnergyComparisonRequest,
    InteractionEnergyRequest,
    MetalCentreRequest,
    MolecularDynamicsRequest,
    PotentialEnergyCurveRequest,
    ScientificVerificationFinding,
    ScientificVerificationReport,
    ScientificVerificationRequestType,
    TransitionStateRequest,
    VerificationDimension,
    VerificationOutcome,
)

_QUALITY_DIMENSIONS = (
    VerificationDimension.IDENTITY_INTEGRITY,
    VerificationDimension.NUMERICAL_INTEGRITY,
    VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
    VerificationDimension.CROSS_CALCULATION_COMPARABILITY,
    VerificationDimension.UNCERTAINTY,
)


class ScientificFailureCause(str, Enum):
    """High-level cause classes derived from blocking verification findings."""

    EXECUTION = "execution"
    IDENTITY = "identity"
    NUMERICAL = "numerical"
    SCIENTIFIC_PLAUSIBILITY = "scientific_plausibility"
    COMPARABILITY = "comparability"
    UNCERTAINTY = "uncertainty"
    AUTHORIZATION = "authorization"


class CandidateChangeKind(str, Enum):
    """Closed scientific change classes allowed in Phase 6 replanning."""

    ACTIVE_SPACE = "active_space"
    GEOMETRY = "geometry"
    SOLVER = "solver"
    SAMPLING = "sampling"
    SCIENTIFIC_METHOD = "scientific_method"


CANDIDATE_CHANGE_KIND_ORDER = (
    CandidateChangeKind.ACTIVE_SPACE,
    CandidateChangeKind.GEOMETRY,
    CandidateChangeKind.SOLVER,
    CandidateChangeKind.SAMPLING,
    CandidateChangeKind.SCIENTIFIC_METHOD,
)


class ReplanningDisposition(str, Enum):
    """Disposition produced from one scientific failure diagnosis."""

    REPLAN = "replan"
    REQUIRE_AUTHORIZATION = "require_authorization"
    STOP = "stop"


class EvidenceComparisonOutcome(str, Enum):
    """Relative scientific quality of candidate evidence versus its baseline."""

    PASSED = "passed"
    IMPROVED = "improved"
    UNCHANGED = "unchanged"
    REGRESSED = "regressed"


def _ordered_identifiers(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    normalized = tuple(validate_identifier(item, label=label) for item in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label.capitalize()} values must be unique.")
    return tuple(sorted(normalized))


def _stable_identifier(prefix: str, payload: object) -> str:
    return f"{prefix}-{sha256_fingerprint(payload)[:32]}"


def _verification_contract_payload(
    request: ScientificVerificationRequestType,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": request.schema_version.model_dump(mode="json"),
        "objective_family": request.objective_family,
        "subject_identifier": request.subject_identifier,
        "require_same_molecule": request.require_same_molecule,
        "require_same_method": request.require_same_method,
        "require_same_geometry": request.require_same_geometry,
        "require_uncertainty": request.require_uncertainty,
        "maximum_uncertainty_hartree": request.maximum_uncertainty_hartree,
        "authorization_required": request.authorization_required,
        "metadata": dict(request.metadata),
    }
    if isinstance(request, EnergyComparisonRequest):
        payload.update(
            {
                "minimum_resolvable_difference_hartree": (
                    request.minimum_resolvable_difference_hartree
                ),
                "expected_lowest_calculation_identifier": (
                    request.expected_lowest_calculation_identifier
                ),
            }
        )
    elif isinstance(request, PotentialEnergyCurveRequest):
        payload.update(
            {
                "maximum_adjacent_energy_jump_hartree": (
                    request.maximum_adjacent_energy_jump_hartree
                ),
                "require_interior_minimum": request.require_interior_minimum,
            }
        )
    elif isinstance(request, InteractionEnergyRequest):
        payload.update(
            {
                "complex_calculation_identifier": (
                    request.complex_calculation_identifier
                ),
                "fragment_calculation_identifiers": (
                    request.fragment_calculation_identifiers
                ),
                "consistency_tolerance_hartree": (
                    request.consistency_tolerance_hartree
                ),
            }
        )
    elif isinstance(request, TransitionStateRequest):
        payload.update(
            {
                "maximum_gradient_norm_hartree_per_bohr": (
                    request.maximum_gradient_norm_hartree_per_bohr
                ),
                "minimum_imaginary_frequency_magnitude_cm_inverse": (
                    request.minimum_imaginary_frequency_magnitude_cm_inverse
                ),
            }
        )
    elif isinstance(request, ConformerComparisonRequest):
        payload["minimum_distinct_geometry_count"] = (
            request.minimum_distinct_geometry_count
        )
    elif isinstance(request, MetalCentreRequest):
        payload.update(
            {
                "metal_element": request.metal_element,
                "allowed_coordination_numbers": request.allowed_coordination_numbers,
                "minimum_metal_ligand_distance_angstrom": (
                    request.minimum_metal_ligand_distance_angstrom
                ),
                "maximum_metal_ligand_distance_angstrom": (
                    request.maximum_metal_ligand_distance_angstrom
                ),
            }
        )
    elif isinstance(request, MolecularDynamicsRequest):
        payload.update(
            {
                "requested_steps": request.requested_steps,
                "timestep_femtosecond": request.timestep_femtosecond,
                "target_temperature_kelvin": request.target_temperature_kelvin,
                "temperature_tolerance_kelvin": (request.temperature_tolerance_kelvin),
                "maximum_absolute_energy_drift_hartree_per_picosecond": (
                    request.maximum_absolute_energy_drift_hartree_per_picosecond
                ),
                "minimum_production_steps": request.minimum_production_steps,
            }
        )
    elif isinstance(request, CandidateRankingRequest):
        payload.update(
            {
                "higher_score_is_better": request.higher_score_is_better,
                "minimum_score_separation": request.minimum_score_separation,
                "require_all_candidates_scientifically_verified": (
                    request.require_all_candidates_scientifically_verified
                ),
            }
        )
    return payload


def verification_contract_fingerprint(
    request: ScientificVerificationRequestType,
) -> str:
    """Fingerprint immutable verification criteria separately from candidate evidence."""

    return sha256_fingerprint(_verification_contract_payload(request))


def _candidate_evidence_payload(
    request: ScientificVerificationRequestType,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "objective_family": request.objective_family,
        "subject_identifier": request.subject_identifier,
        "calculations": [item.fingerprint for item in request.calculations],
    }
    if isinstance(request, PotentialEnergyCurveRequest):
        payload["points"] = [item.model_dump(mode="json") for item in request.points]
    elif isinstance(request, InteractionEnergyRequest):
        payload["reported_interaction_energy_hartree"] = (
            request.reported_interaction_energy_hartree
        )
    elif isinstance(request, TransitionStateRequest):
        payload.update(
            {
                "imaginary_frequencies_cm_inverse": (
                    request.imaginary_frequencies_cm_inverse
                ),
                "gradient_norm_hartree_per_bohr": (
                    request.gradient_norm_hartree_per_bohr
                ),
                "connects_reactant_and_product": (
                    request.connects_reactant_and_product
                ),
            }
        )
    elif isinstance(request, MetalCentreRequest):
        payload.update(
            {
                "coordination_number": request.coordination_number,
                "metal_ligand_distances_angstrom": (
                    request.metal_ligand_distances_angstrom
                ),
                "active_space_contains_metal_orbitals": (
                    request.active_space_contains_metal_orbitals
                ),
                "spin_state_consistent": request.spin_state_consistent,
            }
        )
    elif isinstance(request, MolecularDynamicsRequest):
        payload.update(
            {
                "completed_steps": request.completed_steps,
                "mean_temperature_kelvin": request.mean_temperature_kelvin,
                "energy_drift_hartree_per_picosecond": (
                    request.energy_drift_hartree_per_picosecond
                ),
                "equilibration_steps": request.equilibration_steps,
                "production_steps": request.production_steps,
                "trajectory_values_finite": request.trajectory_values_finite,
                "constraints_satisfied": request.constraints_satisfied,
            }
        )
    elif isinstance(request, CandidateRankingRequest):
        payload["candidates"] = [
            item.model_dump(mode="json") for item in request.candidates
        ]
    return payload


def candidate_evidence_fingerprint(
    request: ScientificVerificationRequestType,
) -> str:
    """Fingerprint scientific evidence without request identifiers or acceptance limits."""

    return sha256_fingerprint(_candidate_evidence_payload(request))


def _cause_for_dimension(dimension: VerificationDimension) -> ScientificFailureCause:
    return {
        VerificationDimension.EXECUTION_INTEGRITY: ScientificFailureCause.EXECUTION,
        VerificationDimension.IDENTITY_INTEGRITY: ScientificFailureCause.IDENTITY,
        VerificationDimension.NUMERICAL_INTEGRITY: ScientificFailureCause.NUMERICAL,
        VerificationDimension.SCIENTIFIC_PLAUSIBILITY: (
            ScientificFailureCause.SCIENTIFIC_PLAUSIBILITY
        ),
        VerificationDimension.CROSS_CALCULATION_COMPARABILITY: (
            ScientificFailureCause.COMPARABILITY
        ),
        VerificationDimension.UNCERTAINTY: ScientificFailureCause.UNCERTAINTY,
        VerificationDimension.AUTHORIZATION: ScientificFailureCause.AUTHORIZATION,
    }[dimension]


_FINDING_CHANGE_KINDS: dict[str, tuple[CandidateChangeKind, ...]] = {
    "verification.numerical.not_converged": (CandidateChangeKind.SOLVER,),
    "verification.numerical.nonfinite": (CandidateChangeKind.SOLVER,),
    "verification.numerical.energy_missing": (
        CandidateChangeKind.SOLVER,
        CandidateChangeKind.SCIENTIFIC_METHOD,
    ),
    "verification.comparability.molecule_mismatch": (),
    "verification.comparability.method_mismatch": (
        CandidateChangeKind.SCIENTIFIC_METHOD,
    ),
    "verification.comparability.geometry_mismatch": (CandidateChangeKind.GEOMETRY,),
    "verification.uncertainty.missing": (CandidateChangeKind.SAMPLING,),
    "verification.uncertainty.exceeds_limit": (CandidateChangeKind.SAMPLING,),
    "verification.uncertainty.energy_difference_unresolved": (
        CandidateChangeKind.SAMPLING,
    ),
    "verification.uncertainty.energy_intervals_overlap": (
        CandidateChangeKind.SAMPLING,
    ),
    "verification.plausibility.energy_order_mismatch": (
        CandidateChangeKind.SCIENTIFIC_METHOD,
    ),
    "verification.comparability.duplicate_reaction_coordinate": (
        CandidateChangeKind.GEOMETRY,
    ),
    "verification.numerical.curve_discontinuity": (
        CandidateChangeKind.GEOMETRY,
        CandidateChangeKind.SOLVER,
    ),
    "verification.plausibility.curve_minimum_at_boundary": (
        CandidateChangeKind.GEOMETRY,
    ),
    "verification.plausibility.interaction_energy_inconsistent": (
        CandidateChangeKind.SCIENTIFIC_METHOD,
    ),
    "verification.plausibility.transition_state_order": (
        CandidateChangeKind.GEOMETRY,
        CandidateChangeKind.SCIENTIFIC_METHOD,
    ),
    "verification.plausibility.imaginary_frequency_too_small": (
        CandidateChangeKind.GEOMETRY,
    ),
    "verification.numerical.transition_state_gradient": (
        CandidateChangeKind.GEOMETRY,
        CandidateChangeKind.SOLVER,
    ),
    "verification.plausibility.transition_path_unconfirmed": (
        CandidateChangeKind.GEOMETRY,
        CandidateChangeKind.SCIENTIFIC_METHOD,
    ),
    "verification.plausibility.conformers_not_distinct": (
        CandidateChangeKind.GEOMETRY,
    ),
    "verification.uncertainty.conformer_order_unresolved": (
        CandidateChangeKind.SAMPLING,
    ),
    "verification.plausibility.metal_coordination_number": (
        CandidateChangeKind.GEOMETRY,
    ),
    "verification.plausibility.metal_ligand_distance": (CandidateChangeKind.GEOMETRY,),
    "verification.identity.metal_active_space_missing": (
        CandidateChangeKind.ACTIVE_SPACE,
    ),
    "verification.plausibility.metal_spin_state": (
        CandidateChangeKind.ACTIVE_SPACE,
        CandidateChangeKind.SCIENTIFIC_METHOD,
    ),
    "verification.execution.md_trajectory_incomplete": (),
    "verification.numerical.md_nonfinite_trajectory": (CandidateChangeKind.SOLVER,),
    "verification.plausibility.md_temperature_outside_tolerance": (
        CandidateChangeKind.SOLVER,
        CandidateChangeKind.SAMPLING,
    ),
    "verification.numerical.md_energy_drift": (
        CandidateChangeKind.SOLVER,
        CandidateChangeKind.SCIENTIFIC_METHOD,
    ),
    "verification.uncertainty.md_sampling_insufficient": (
        CandidateChangeKind.SAMPLING,
    ),
    "verification.plausibility.md_constraints_failed": (CandidateChangeKind.SOLVER,),
    "verification.plausibility.ranking_ineligible_candidate": (
        CandidateChangeKind.SCIENTIFIC_METHOD,
    ),
    "verification.identity.ranking_unverified_candidate": (
        CandidateChangeKind.SCIENTIFIC_METHOD,
    ),
    "verification.uncertainty.ranking_unresolved": (CandidateChangeKind.SAMPLING,),
}


def suggested_change_kinds(
    finding: ScientificVerificationFinding,
) -> tuple[CandidateChangeKind, ...]:
    """Return bounded scientific changes for one known verification finding."""

    explicit = _FINDING_CHANGE_KINDS.get(finding.finding_identifier)
    if explicit is not None:
        return explicit

    if finding.dimension is VerificationDimension.NUMERICAL_INTEGRITY:
        return (CandidateChangeKind.SOLVER,)
    if finding.dimension is VerificationDimension.SCIENTIFIC_PLAUSIBILITY:
        return (CandidateChangeKind.SCIENTIFIC_METHOD,)
    if finding.dimension is VerificationDimension.UNCERTAINTY:
        return (CandidateChangeKind.SAMPLING,)
    if finding.dimension is VerificationDimension.CROSS_CALCULATION_COMPARABILITY:
        return (CandidateChangeKind.SCIENTIFIC_METHOD,)
    return ()


class DiagnosticCause(CanonicalModel):
    """One blocking verifier finding translated into a replanning diagnosis."""

    cause_identifier: str
    finding_identifier: str
    dimension: VerificationDimension
    cause: ScientificFailureCause
    subject_identifier: str | None = None
    message: str = Field(min_length=1, max_length=4096)
    evidence_identifiers: tuple[str, ...] = ()
    suggested_change_kinds: tuple[CandidateChangeKind, ...] = ()

    @field_validator(
        "cause_identifier",
        "finding_identifier",
        "subject_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_identifier(value, label="scientific diagnosis identifier")

    @field_validator("evidence_identifiers")
    @classmethod
    def validate_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="diagnosis evidence identifier")

    @field_validator("suggested_change_kinds")
    @classmethod
    def order_change_kinds(
        cls, value: tuple[CandidateChangeKind, ...]
    ) -> tuple[CandidateChangeKind, ...]:
        unique = set(value)
        return tuple(kind for kind in CANDIDATE_CHANGE_KIND_ORDER if kind in unique)

    @model_validator(mode="after")
    def validate_cause_dimension(self) -> Self:
        if self.cause is not _cause_for_dimension(self.dimension):
            raise ValueError("Scientific failure cause does not match its dimension.")
        return self


class ScientificFailureDiagnosis(CanonicalModel):
    """Structured diagnosis of one failed scientific verification report."""

    diagnosis_identifier: str
    report_identifier: str
    report_sha256: str
    objective_family: str
    subject_identifier: str
    causes: tuple[DiagnosticCause, ...] = Field(min_length=1)
    execution_integrity_passed: bool
    scientific_quality_passed: bool
    authorization_passed: bool
    replannable: bool
    summary: str = Field(min_length=1, max_length=4096)

    @field_validator(
        "diagnosis_identifier",
        "report_identifier",
        "objective_family",
        "subject_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="scientific diagnosis identifier")

    @field_validator("report_sha256")
    @classmethod
    def validate_report_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("causes")
    @classmethod
    def order_causes(
        cls, value: tuple[DiagnosticCause, ...]
    ) -> tuple[DiagnosticCause, ...]:
        identities = [item.cause_identifier for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError("Scientific diagnosis causes must be unique.")
        return tuple(
            sorted(
                value,
                key=lambda item: (
                    item.dimension.value,
                    item.finding_identifier,
                    item.subject_identifier or "",
                    item.cause_identifier,
                ),
            )
        )

    @model_validator(mode="after")
    def validate_replanning_state(self) -> Self:
        has_actionable_scientific_cause = any(
            cause.dimension in _QUALITY_DIMENSIONS and cause.suggested_change_kinds
            for cause in self.causes
        )
        expected = bool(
            self.execution_integrity_passed
            and self.authorization_passed
            and not self.scientific_quality_passed
            and has_actionable_scientific_cause
        )
        if self.replannable != expected:
            raise ValueError("Scientific diagnosis replannable flag is inconsistent.")
        return self


class CandidateChange(CanonicalModel):
    """One bounded, non-authorizing scientific change for a new candidate plan."""

    change_identifier: str
    change_kind: CandidateChangeKind
    action_identifier: str
    target_identifier: str | None = None
    rationale: str = Field(min_length=1, max_length=4096)
    source_finding_identifiers: tuple[str, ...] = Field(min_length=1)
    parameters: BoundedMetadata = Field(default_factory=dict)

    @field_validator(
        "change_identifier",
        "action_identifier",
        "target_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_identifier(value, label="candidate change identifier")

    @field_validator("source_finding_identifiers")
    @classmethod
    def validate_sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="source finding identifier")

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)


class ReplanningRecommendation(CanonicalModel):
    """Bounded response to one diagnosis; it never grants execution authority."""

    recommendation_identifier: str
    diagnosis_identifier: str
    disposition: ReplanningDisposition
    changes: tuple[CandidateChange, ...] = ()
    rationale: str = Field(min_length=1, max_length=4096)
    requires_fresh_authorization: bool = False
    authorizes_execution: Literal[False] = False

    @field_validator("recommendation_identifier", "diagnosis_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="replanning recommendation identifier")

    @field_validator("changes")
    @classmethod
    def order_changes(
        cls, value: tuple[CandidateChange, ...]
    ) -> tuple[CandidateChange, ...]:
        identities = [item.change_identifier for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError("Replanning changes must be unique.")
        order = {kind: index for index, kind in enumerate(CANDIDATE_CHANGE_KIND_ORDER)}
        return tuple(
            sorted(
                value,
                key=lambda item: (
                    order[item.change_kind],
                    item.action_identifier,
                    item.target_identifier or "",
                    item.change_identifier,
                ),
            )
        )

    @model_validator(mode="after")
    def validate_disposition(self) -> Self:
        if self.disposition is ReplanningDisposition.REPLAN:
            if not self.changes:
                raise ValueError("Replanning dispositions require at least one change.")
            if not self.requires_fresh_authorization:
                raise ValueError(
                    "New scientific candidate plans must require fresh authorization."
                )
        elif self.changes:
            raise ValueError(
                "Non-replanning dispositions cannot contain candidate changes."
            )
        return self


class CandidatePlan(CanonicalModel):
    """Immutable candidate-plan delta produced from verified scientific evidence."""

    candidate_plan_identifier: str
    objective_family: str
    subject_identifier: str
    generation: int = Field(ge=1)
    parent_plan_identifier: str | None = None
    source_report_identifier: str
    source_diagnosis_identifier: str
    baseline_request_sha256: str
    baseline_verification_contract_sha256: str
    changes: tuple[CandidateChange, ...] = Field(min_length=1)
    metadata: BoundedMetadata = Field(default_factory=dict)
    authorization_carried_forward: Literal[False] = False
    authorizes_execution: Literal[False] = False

    @field_validator(
        "candidate_plan_identifier",
        "objective_family",
        "subject_identifier",
        "parent_plan_identifier",
        "source_report_identifier",
        "source_diagnosis_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_identifier(value, label="candidate plan identifier")

    @field_validator(
        "baseline_request_sha256",
        "baseline_verification_contract_sha256",
    )
    @classmethod
    def validate_request_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("changes")
    @classmethod
    def order_changes(
        cls, value: tuple[CandidateChange, ...]
    ) -> tuple[CandidateChange, ...]:
        if not value:
            raise ValueError("Candidate plans require at least one scientific change.")
        identities = [item.change_identifier for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError("Candidate plan changes must be unique.")
        order = {kind: index for index, kind in enumerate(CANDIDATE_CHANGE_KIND_ORDER)}
        return tuple(
            sorted(
                value,
                key=lambda item: (
                    order[item.change_kind],
                    item.action_identifier,
                    item.change_identifier,
                ),
            )
        )

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)


class ReplanningPolicy(CanonicalModel):
    """Bounded policy for deterministic scientific replanning."""

    max_candidate_generations: int = Field(default=3, ge=1, le=10)
    maximum_changes_per_candidate: int = Field(default=5, ge=1, le=5)
    allowed_change_kinds: tuple[CandidateChangeKind, ...] = CANDIDATE_CHANGE_KIND_ORDER
    stop_on_no_improvement: bool = True

    @field_validator("allowed_change_kinds")
    @classmethod
    def order_allowed_kinds(
        cls, value: tuple[CandidateChangeKind, ...]
    ) -> tuple[CandidateChangeKind, ...]:
        unique = set(value)
        if not unique:
            raise ValueError("Replanning policy must allow at least one change kind.")
        return tuple(kind for kind in CANDIDATE_CHANGE_KIND_ORDER if kind in unique)


class EvidenceComparison(CanonicalModel):
    """Deterministic comparison of baseline and replanned scientific evidence."""

    comparison_identifier: str
    baseline_report_identifier: str
    candidate_report_identifier: str
    baseline_request_sha256: str
    candidate_request_sha256: str
    verification_contract_sha256: str
    baseline_evidence_sha256: str
    candidate_evidence_sha256: str
    evidence_changed: bool
    baseline_scientific_dimension_pass_count: int = Field(ge=0, le=5)
    candidate_scientific_dimension_pass_count: int = Field(ge=0, le=5)
    baseline_blocking_finding_count: int = Field(ge=0)
    candidate_blocking_finding_count: int = Field(ge=0)
    resolved_finding_identifiers: tuple[str, ...] = ()
    persistent_finding_identifiers: tuple[str, ...] = ()
    introduced_finding_identifiers: tuple[str, ...] = ()
    outcome: EvidenceComparisonOutcome
    candidate_preferred: bool
    summary: str = Field(min_length=1, max_length=4096)

    @field_validator(
        "comparison_identifier",
        "baseline_report_identifier",
        "candidate_report_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="evidence comparison identifier")

    @field_validator(
        "baseline_request_sha256",
        "candidate_request_sha256",
        "verification_contract_sha256",
        "baseline_evidence_sha256",
        "candidate_evidence_sha256",
    )
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator(
        "resolved_finding_identifiers",
        "persistent_finding_identifiers",
        "introduced_finding_identifiers",
    )
    @classmethod
    def validate_findings(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="comparison finding identifier")


class ScientificFailureDiagnoser:
    """Translate blocking verification findings into deterministic scientific causes."""

    def diagnose(
        self, report: ScientificVerificationReport
    ) -> ScientificFailureDiagnosis:
        blocking = tuple(
            finding
            for result in report.dimension_results
            for finding in result.findings
            if finding.blocking
        )
        if not blocking:
            raise ValueError("Scientific failure diagnosis requires a failed report.")

        causes = tuple(self._cause(report, finding) for finding in blocking)
        replannable = bool(
            report.execution_integrity_passed
            and report.authorization_passed
            and not report.scientific_quality_passed
            and any(
                item.dimension in _QUALITY_DIMENSIONS and item.suggested_change_kinds
                for item in causes
            )
        )
        diagnosis_identifier = _stable_identifier(
            "scientific-diagnosis",
            {
                "report_identifier": report.report_identifier,
                "report_sha256": report.fingerprint,
                "causes": [item.fingerprint for item in causes],
            },
        )
        return ScientificFailureDiagnosis(
            diagnosis_identifier=diagnosis_identifier,
            report_identifier=report.report_identifier,
            report_sha256=report.fingerprint,
            objective_family=report.objective_family,
            subject_identifier=report.subject_identifier,
            causes=causes,
            execution_integrity_passed=report.execution_integrity_passed,
            scientific_quality_passed=report.scientific_quality_passed,
            authorization_passed=report.authorization_passed,
            replannable=replannable,
            summary=self._summary(report, causes, replannable),
        )

    @staticmethod
    def _cause(
        report: ScientificVerificationReport,
        finding: ScientificVerificationFinding,
    ) -> DiagnosticCause:
        kinds = suggested_change_kinds(finding)
        cause_identifier = _stable_identifier(
            "scientific-cause",
            {
                "report": report.report_identifier,
                "finding": finding.fingerprint,
                "kinds": [item.value for item in kinds],
            },
        )
        return DiagnosticCause(
            cause_identifier=cause_identifier,
            finding_identifier=finding.finding_identifier,
            dimension=finding.dimension,
            cause=_cause_for_dimension(finding.dimension),
            subject_identifier=finding.subject_identifier,
            message=finding.message,
            evidence_identifiers=finding.evidence_identifiers,
            suggested_change_kinds=kinds,
        )

    @staticmethod
    def _summary(
        report: ScientificVerificationReport,
        causes: tuple[DiagnosticCause, ...],
        replannable: bool,
    ) -> str:
        dimensions = ", ".join(sorted({item.dimension.value for item in causes}))
        if replannable:
            return (
                "Scientific verification failed with actionable quality findings in "
                f"{dimensions}; bounded scientific replanning is permitted."
            )
        if not report.authorization_passed:
            return (
                "Scientific verification is blocked by authorization; replanning "
                "cannot grant or inherit execution authority."
            )
        if not report.execution_integrity_passed:
            return (
                "Execution integrity failed; the scientific replanner does not "
                "reinterpret an execution failure as a scientific-quality repair."
            )
        return (
            "Scientific verification failed, but no bounded scientific change is "
            "available for the blocking findings."
        )


_ACTION_BY_KIND = {
    CandidateChangeKind.ACTIVE_SPACE: "revise_active_space",
    CandidateChangeKind.GEOMETRY: "revise_geometry",
    CandidateChangeKind.SOLVER: "revise_solver",
    CandidateChangeKind.SAMPLING: "revise_sampling",
    CandidateChangeKind.SCIENTIFIC_METHOD: "revise_scientific_method",
}


class ScientificReplanner:
    """Create bounded candidate changes without granting execution authority."""

    def recommend(
        self,
        diagnosis: ScientificFailureDiagnosis,
        *,
        policy: ReplanningPolicy | None = None,
    ) -> ReplanningRecommendation:
        effective_policy = policy or ReplanningPolicy()

        if not diagnosis.authorization_passed:
            return self._non_replan(
                diagnosis,
                ReplanningDisposition.REQUIRE_AUTHORIZATION,
                "Fresh authorization is required before any candidate execution.",
            )
        if not diagnosis.execution_integrity_passed:
            return self._non_replan(
                diagnosis,
                ReplanningDisposition.STOP,
                "Execution-integrity failures require execution recovery, not scientific replanning.",
            )
        if diagnosis.scientific_quality_passed:
            return self._non_replan(
                diagnosis,
                ReplanningDisposition.STOP,
                "Scientific quality already passed; no scientific replan is justified.",
            )

        changes = self._changes(diagnosis, effective_policy)
        if not changes:
            return self._non_replan(
                diagnosis,
                ReplanningDisposition.STOP,
                "No allowed bounded change addresses the blocking scientific findings.",
            )

        recommendation_identifier = _stable_identifier(
            "replanning-recommendation",
            {
                "diagnosis": diagnosis.fingerprint,
                "changes": [item.fingerprint for item in changes],
                "policy": effective_policy.fingerprint,
            },
        )
        return ReplanningRecommendation(
            recommendation_identifier=recommendation_identifier,
            diagnosis_identifier=diagnosis.diagnosis_identifier,
            disposition=ReplanningDisposition.REPLAN,
            changes=changes,
            rationale=(
                "Create one new scientific candidate that changes only the bounded "
                "dimensions supported by the failed verification evidence."
            ),
            requires_fresh_authorization=True,
        )

    def build_candidate_plan(
        self,
        *,
        request: ScientificVerificationRequestType,
        report: ScientificVerificationReport,
        diagnosis: ScientificFailureDiagnosis,
        recommendation: ReplanningRecommendation,
        generation: int,
        parent_plan_identifier: str | None = None,
    ) -> CandidatePlan:
        if recommendation.disposition is not ReplanningDisposition.REPLAN:
            raise ValueError("Candidate plans require a replanning recommendation.")
        if recommendation.diagnosis_identifier != diagnosis.diagnosis_identifier:
            raise ValueError(
                "Recommendation does not belong to the supplied diagnosis."
            )
        if diagnosis.report_identifier != report.report_identifier:
            raise ValueError("Diagnosis does not belong to the supplied report.")
        if report.request_sha256 != request.fingerprint:
            raise ValueError(
                "Verification report does not belong to the supplied request."
            )
        if request.objective_family != diagnosis.objective_family:
            raise ValueError(
                "Candidate request objective family does not match diagnosis."
            )
        if request.subject_identifier != diagnosis.subject_identifier:
            raise ValueError("Candidate request subject does not match diagnosis.")

        payload = {
            "objective_family": request.objective_family,
            "subject_identifier": request.subject_identifier,
            "generation": generation,
            "parent": parent_plan_identifier,
            "request": request.fingerprint,
            "report": report.fingerprint,
            "diagnosis": diagnosis.fingerprint,
            "recommendation": recommendation.fingerprint,
            "changes": [item.fingerprint for item in recommendation.changes],
        }
        return CandidatePlan(
            candidate_plan_identifier=_stable_identifier(
                "scientific-candidate-plan", payload
            ),
            objective_family=request.objective_family,
            subject_identifier=request.subject_identifier,
            generation=generation,
            parent_plan_identifier=parent_plan_identifier,
            source_report_identifier=report.report_identifier,
            source_diagnosis_identifier=diagnosis.diagnosis_identifier,
            baseline_request_sha256=request.fingerprint,
            baseline_verification_contract_sha256=(
                verification_contract_fingerprint(request)
            ),
            changes=recommendation.changes,
            metadata={
                "recommendation_identifier": recommendation.recommendation_identifier,
            },
        )

    @staticmethod
    def _non_replan(
        diagnosis: ScientificFailureDiagnosis,
        disposition: ReplanningDisposition,
        rationale: str,
    ) -> ReplanningRecommendation:
        identifier = _stable_identifier(
            "replanning-recommendation",
            {
                "diagnosis": diagnosis.fingerprint,
                "disposition": disposition.value,
                "rationale": rationale,
            },
        )
        return ReplanningRecommendation(
            recommendation_identifier=identifier,
            diagnosis_identifier=diagnosis.diagnosis_identifier,
            disposition=disposition,
            changes=(),
            rationale=rationale,
            requires_fresh_authorization=(
                disposition is ReplanningDisposition.REQUIRE_AUTHORIZATION
            ),
        )

    @staticmethod
    def _changes(
        diagnosis: ScientificFailureDiagnosis,
        policy: ReplanningPolicy,
    ) -> tuple[CandidateChange, ...]:
        allowed = set(policy.allowed_change_kinds)
        grouped: dict[CandidateChangeKind, list[DiagnosticCause]] = {}

        for cause in diagnosis.causes:
            for kind in cause.suggested_change_kinds:
                if kind not in allowed:
                    continue
                grouped.setdefault(kind, []).append(cause)

        kinds = [kind for kind in CANDIDATE_CHANGE_KIND_ORDER if kind in grouped][
            : policy.maximum_changes_per_candidate
        ]

        changes: list[CandidateChange] = []
        for kind in kinds:
            causes = grouped[kind]
            targets = {
                item.subject_identifier
                for item in causes
                if item.subject_identifier is not None
            }
            target = next(iter(targets)) if len(targets) == 1 else None
            finding_ids = tuple(sorted({item.finding_identifier for item in causes}))
            action = _ACTION_BY_KIND[kind]
            identifier = _stable_identifier(
                "candidate-change",
                {
                    "diagnosis": diagnosis.diagnosis_identifier,
                    "kind": kind.value,
                    "action": action,
                    "target": target,
                    "findings": finding_ids,
                },
            )
            changes.append(
                CandidateChange(
                    change_identifier=identifier,
                    change_kind=kind,
                    action_identifier=action,
                    target_identifier=target,
                    rationale=(
                        f"Address {', '.join(finding_ids)} with a bounded "
                        f"{kind.value} change."
                    ),
                    source_finding_identifiers=finding_ids,
                    parameters={
                        "strategy": action,
                    },
                )
            )
        return tuple(changes)


def _blocking_findings(
    report: ScientificVerificationReport,
) -> tuple[ScientificVerificationFinding, ...]:
    return tuple(
        finding
        for result in report.dimension_results
        for finding in result.findings
        if finding.blocking
    )


def _scientific_pass_count(report: ScientificVerificationReport) -> int:
    by_dimension = {item.dimension: item for item in report.dimension_results}
    return sum(
        by_dimension[dimension].outcome is VerificationOutcome.PASSED
        for dimension in _QUALITY_DIMENSIONS
    )


def compare_evidence(
    baseline_request: ScientificVerificationRequestType,
    baseline_report: ScientificVerificationReport,
    candidate_request: ScientificVerificationRequestType,
    candidate_report: ScientificVerificationReport,
) -> EvidenceComparison:
    """Compare new candidate evidence without allowing verification-contract drift."""

    if baseline_report.objective_family != candidate_report.objective_family:
        raise ValueError("Evidence comparisons require one objective family.")
    if baseline_report.subject_identifier != candidate_report.subject_identifier:
        raise ValueError("Evidence comparisons require one scientific subject.")
    if baseline_report.verifier_identifier != candidate_report.verifier_identifier:
        raise ValueError("Evidence comparisons require one verifier identity.")
    if baseline_report.verifier_version != candidate_report.verifier_version:
        raise ValueError("Evidence comparisons require one verifier version.")
    if baseline_report.request_sha256 != baseline_request.fingerprint:
        raise ValueError("Baseline report does not belong to baseline evidence.")
    if candidate_report.request_sha256 != candidate_request.fingerprint:
        raise ValueError("Candidate report does not belong to candidate evidence.")

    baseline_contract = verification_contract_fingerprint(baseline_request)
    candidate_contract = verification_contract_fingerprint(candidate_request)
    if baseline_contract != candidate_contract:
        raise ValueError(
            "Candidate execution changed the scientific verification contract."
        )

    baseline_evidence = candidate_evidence_fingerprint(baseline_request)
    candidate_evidence = candidate_evidence_fingerprint(candidate_request)
    evidence_changed = baseline_evidence != candidate_evidence

    baseline_blocking_findings = _blocking_findings(baseline_report)
    candidate_blocking_findings = _blocking_findings(candidate_report)
    baseline_finding_ids = {
        item.finding_identifier for item in baseline_blocking_findings
    }
    candidate_finding_ids = {
        item.finding_identifier for item in candidate_blocking_findings
    }
    resolved = tuple(sorted(baseline_finding_ids - candidate_finding_ids))
    persistent = tuple(sorted(baseline_finding_ids & candidate_finding_ids))
    introduced = tuple(sorted(candidate_finding_ids - baseline_finding_ids))

    baseline_passes = _scientific_pass_count(baseline_report)
    candidate_passes = _scientific_pass_count(candidate_report)
    baseline_blocking = len(baseline_blocking_findings)
    candidate_blocking = len(candidate_blocking_findings)

    candidate_safe = (
        candidate_report.execution_integrity_passed
        and candidate_report.authorization_passed
    )
    baseline_safe = (
        baseline_report.execution_integrity_passed
        and baseline_report.authorization_passed
    )

    if not evidence_changed:
        outcome = EvidenceComparisonOutcome.UNCHANGED
        preferred = False
    elif candidate_report.overall_outcome is VerificationOutcome.PASSED and (
        baseline_report.overall_outcome is VerificationOutcome.FAILED
    ):
        outcome = EvidenceComparisonOutcome.PASSED
        preferred = True
    elif not candidate_safe and baseline_safe:
        outcome = EvidenceComparisonOutcome.REGRESSED
        preferred = False
    elif candidate_safe and (
        candidate_passes > baseline_passes
        or (
            candidate_passes == baseline_passes
            and candidate_blocking < baseline_blocking
        )
    ):
        outcome = EvidenceComparisonOutcome.IMPROVED
        preferred = True
    elif candidate_passes < baseline_passes or (
        candidate_passes == baseline_passes and candidate_blocking > baseline_blocking
    ):
        outcome = EvidenceComparisonOutcome.REGRESSED
        preferred = False
    else:
        outcome = EvidenceComparisonOutcome.UNCHANGED
        preferred = False

    payload = {
        "baseline": baseline_report.fingerprint,
        "candidate": candidate_report.fingerprint,
        "baseline_evidence": baseline_evidence,
        "candidate_evidence": candidate_evidence,
        "resolved": resolved,
        "persistent": persistent,
        "introduced": introduced,
        "outcome": outcome.value,
    }
    return EvidenceComparison(
        comparison_identifier=_stable_identifier("evidence-comparison", payload),
        baseline_report_identifier=baseline_report.report_identifier,
        candidate_report_identifier=candidate_report.report_identifier,
        baseline_request_sha256=baseline_request.fingerprint,
        candidate_request_sha256=candidate_request.fingerprint,
        verification_contract_sha256=baseline_contract,
        baseline_evidence_sha256=baseline_evidence,
        candidate_evidence_sha256=candidate_evidence,
        evidence_changed=evidence_changed,
        baseline_scientific_dimension_pass_count=baseline_passes,
        candidate_scientific_dimension_pass_count=candidate_passes,
        baseline_blocking_finding_count=baseline_blocking,
        candidate_blocking_finding_count=candidate_blocking,
        resolved_finding_identifiers=resolved,
        persistent_finding_identifiers=persistent,
        introduced_finding_identifiers=introduced,
        outcome=outcome,
        candidate_preferred=preferred,
        summary=(
            "Candidate evidence passed scientific verification."
            if outcome is EvidenceComparisonOutcome.PASSED
            else f"Candidate evidence is {outcome.value} relative to its baseline."
        ),
    )
