"""Pure scientific objective and planning-policy contracts."""

from __future__ import annotations

import math
from enum import Enum
from typing import Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion

from .artifacts import ArtifactPointer, CreationProvenance
from .canonical import CanonicalModel, validate_identifier
from .capabilities import (
    CapabilityEstimate,
    CapabilityFidelityDeclaration,
    CapabilityFidelityLevel,
    CapabilityVerificationRequirement,
)
from .contracts import ScientificAssumption
from .resources import ResourceQuantity, ResourceValue

_DESCRIPTION_MAXIMUM_LENGTH = 16_384
_DECLARATION_MAXIMUM_LENGTH = 4096


def _ordered_identifiers(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    validated = tuple(validate_identifier(item, label=label) for item in values)
    if len(validated) != len(set(validated)):
        raise ValueError(f"{label.capitalize()} values must be unique.")
    return tuple(sorted(validated))


def _ordered_text(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    normalized: list[str] = []
    for item in values:
        item = item.strip()
        if not item:
            raise ValueError(f"{label.capitalize()} values cannot be blank.")
        if len(item) > _DECLARATION_MAXIMUM_LENGTH:
            raise ValueError(f"{label.capitalize()} values are too long.")
        normalized.append(item)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label.capitalize()} values must be unique.")
    return tuple(sorted(normalized))


def _ordered_artifacts(
    values: tuple[ArtifactPointer, ...], *, label: str
) -> tuple[ArtifactPointer, ...]:
    identities = [(item.artifact_identifier, item.content_sha256) for item in values]
    if len(identities) != len(set(identities)):
        raise ValueError(f"{label.capitalize()} values must be unique.")
    return tuple(
        sorted(values, key=lambda item: (item.artifact_identifier, item.content_sha256))
    )


def _version_key(version: CapabilityVersion) -> tuple[int, int, int]:
    return version.major, version.minor, version.patch


class ScientificObjective(CanonicalModel):
    """A normalized, attributable scientific objective without execution authority."""

    objective_identifier: str
    schema_version: CapabilityVersion
    objective_type: str
    original_description: str = Field(
        min_length=1, max_length=_DESCRIPTION_MAXIMUM_LENGTH
    )
    normalized_description: str = Field(
        min_length=1, max_length=_DESCRIPTION_MAXIMUM_LENGTH
    )
    project_identifier: str
    molecular_system_identifiers: tuple[str, ...] = ()
    structure_identifiers: tuple[str, ...] = ()
    structural_region_identifiers: tuple[str, ...] = ()
    comparison_state_identifiers: tuple[str, ...] = ()
    requested_property_identifiers: tuple[str, ...] = ()
    scientific_constraints: tuple[str, ...] = ()
    required_confidence: float | None = Field(default=None, ge=0, le=1)
    stopping_criteria: tuple[str, ...] = ()
    assumptions: tuple[ScientificAssumption, ...] = ()
    provenance: CreationProvenance

    @field_validator("objective_identifier", "objective_type", "project_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("original_description", "normalized_description")
    @classmethod
    def normalize_descriptions(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Scientific objective descriptions cannot be blank.")
        return value

    @field_validator(
        "molecular_system_identifiers",
        "structure_identifiers",
        "structural_region_identifiers",
        "comparison_state_identifiers",
        "requested_property_identifiers",
    )
    @classmethod
    def order_identifier_collections(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="objective identifier")

    @field_validator("scientific_constraints", "stopping_criteria")
    @classmethod
    def order_text_collections(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_text(value, label="objective declaration")

    @field_validator("required_confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value: object) -> object:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ValueError("Required confidence must be numeric.")
        if value is not None and not math.isfinite(value):
            raise ValueError("Required confidence must be finite.")
        return value

    @field_validator("assumptions")
    @classmethod
    def order_assumptions(
        cls, value: tuple[ScientificAssumption, ...]
    ) -> tuple[ScientificAssumption, ...]:
        identifiers = [item.assumption_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError(
                "Scientific objective assumption identifiers must be unique."
            )
        return tuple(sorted(value, key=lambda item: item.assumption_identifier))


class PlanningConstraints(CanonicalModel):
    """Non-authorizing policy constraints for a future scientific planner."""

    permitted_execution_targets: tuple[str, ...] = ()
    forbidden_execution_targets: tuple[str, ...] = ()
    resource_ceilings: tuple[ResourceQuantity, ...] = ()
    cost_ceilings: tuple[ResourceQuantity, ...] = ()
    maximum_wall_time_seconds: float | None = Field(default=None, gt=0)
    minimum_fidelity: CapabilityFidelityLevel | None = None
    required_confidence: float | None = Field(default=None, ge=0, le=1)
    required_verifications: tuple[CapabilityVerificationRequirement, ...] = ()
    approval_requirements: tuple[str, ...] = ()
    policy_notes: tuple[str, ...] = ()

    @field_validator(
        "permitted_execution_targets",
        "forbidden_execution_targets",
        "approval_requirements",
    )
    @classmethod
    def order_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="planning identifier")

    @field_validator("resource_ceilings", "cost_ceilings")
    @classmethod
    def order_ceilings(
        cls, value: tuple[ResourceQuantity, ...]
    ) -> tuple[ResourceQuantity, ...]:
        identities = [(item.dimension, item.unit) for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError(
                "Planning ceiling dimension and unit pairs must be unique."
            )
        return tuple(sorted(value, key=lambda item: (item.dimension, item.unit)))

    @field_validator(
        "maximum_wall_time_seconds", "required_confidence", mode="before"
    )
    @classmethod
    def validate_finite_numbers(cls, value: object) -> object:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ValueError("Planning numeric constraints must be numeric.")
        if value is not None and not math.isfinite(value):
            raise ValueError("Planning numeric constraints must be finite.")
        return value

    @field_validator("required_verifications")
    @classmethod
    def order_verifications(
        cls, value: tuple[CapabilityVerificationRequirement, ...]
    ) -> tuple[CapabilityVerificationRequirement, ...]:
        identities = [
            (item.verifier_identifier, item.subject_artifact_type) for item in value
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("Planning verification requirements must be unique.")
        return tuple(
            sorted(
                value,
                key=lambda item: (item.verifier_identifier, item.subject_artifact_type),
            )
        )

    @field_validator("policy_notes")
    @classmethod
    def order_policy_notes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_text(value, label="policy note")

    @model_validator(mode="after")
    def reject_target_conflicts(self) -> Self:
        if set(self.permitted_execution_targets).intersection(
            self.forbidden_execution_targets
        ):
            raise ValueError(
                "An execution target cannot be both permitted and forbidden."
            )
        return self


class PlanningFact(CanonicalModel):
    """One strict scalar planning fact tied to explicit scientific evidence."""

    fact_identifier: str
    schema_version: CapabilityVersion
    subject_identifier: str
    dimension: str
    unit: str
    value: ResourceValue
    evidence_artifacts: tuple[ArtifactPointer, ...] = ()
    derivation_identifier: str

    @field_validator(
        "fact_identifier",
        "subject_identifier",
        "dimension",
        "unit",
        "derivation_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="planning fact identifier")

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: ResourceValue) -> ResourceValue:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Planning fact values must be finite.")
        return value

    @field_validator("evidence_artifacts")
    @classmethod
    def order_evidence(
        cls, value: tuple[ArtifactPointer, ...]
    ) -> tuple[ArtifactPointer, ...]:
        return _ordered_artifacts(value, label="planning fact evidence")

    @property
    def semantic_address(self) -> tuple[str, str, str]:
        """Return the subject, dimension, and unit address of this fact."""
        return self.subject_identifier, self.dimension, self.unit


class PlanningFactSet(CanonicalModel):
    """A deterministic collection of uniquely addressed planning facts."""

    facts: tuple[PlanningFact, ...] = ()

    @field_validator("facts")
    @classmethod
    def order_facts(cls, value: tuple[PlanningFact, ...]) -> tuple[PlanningFact, ...]:
        identifiers = [item.fact_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Planning fact identifiers must be unique.")
        addresses = [item.semantic_address for item in value]
        if len(addresses) != len(set(addresses)):
            raise ValueError("Planning fact semantic addresses must be unique.")
        return tuple(sorted(value, key=lambda item: (*item.semantic_address, item.fact_identifier)))

    def lookup(
        self, subject_identifier: str, dimension: str, unit: str
    ) -> PlanningFact | None:
        """Return one exact fact without fabricating or mutating evidence."""
        address = (
            validate_identifier(subject_identifier, label="planning fact subject"),
            validate_identifier(dimension, label="planning fact dimension"),
            validate_identifier(unit, label="planning fact unit"),
        )
        return next((item for item in self.facts if item.semantic_address == address), None)


class FeasibilityStatus(str, Enum):
    """Deterministic planning status ordered from weakest to strongest blocker."""

    FEASIBLE = "feasible"
    CONDITIONAL = "conditional"
    UNKNOWN = "unknown"
    INFEASIBLE = "infeasible"

    @property
    def precedence(self) -> int:
        return {
            FeasibilityStatus.FEASIBLE: 0,
            FeasibilityStatus.CONDITIONAL: 1,
            FeasibilityStatus.UNKNOWN: 2,
            FeasibilityStatus.INFEASIBLE: 3,
        }[self]


class FeasibilityFinding(CanonicalModel):
    """One bounded explanation produced by pure feasibility evaluation."""

    finding_identifier: str
    finding_type: str
    status: FeasibilityStatus
    subject_identifier: str
    capability_name: str
    capability_version: CapabilityVersion
    explanation: str = Field(min_length=1, max_length=_DECLARATION_MAXIMUM_LENGTH)
    related_fact_identifiers: tuple[str, ...] = ()
    related_prerequisite_identifiers: tuple[str, ...] = ()
    evidence_artifacts: tuple[ArtifactPointer, ...] = ()

    @field_validator(
        "finding_identifier",
        "finding_type",
        "subject_identifier",
        "capability_name",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="feasibility finding identifier")

    @field_validator("explanation")
    @classmethod
    def normalize_explanation(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Feasibility explanations cannot be blank.")
        return value

    @field_validator(
        "related_fact_identifiers", "related_prerequisite_identifiers"
    )
    @classmethod
    def order_related_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="feasibility related identifier")

    @field_validator("evidence_artifacts")
    @classmethod
    def order_evidence(
        cls, value: tuple[ArtifactPointer, ...]
    ) -> tuple[ArtifactPointer, ...]:
        return _ordered_artifacts(value, label="feasibility evidence")


class CapabilityFeasibilityResult(CanonicalModel):
    """Pure evaluation of one exact scientific capability envelope."""

    evaluation_identifier: str
    objective_identifier: str
    capability_name: str
    capability_version: CapabilityVersion
    status: FeasibilityStatus
    execution_target: str | None = None
    findings: tuple[FeasibilityFinding, ...] = ()
    unresolved_requirement_identifiers: tuple[str, ...] = ()
    estimates: tuple[CapabilityEstimate, ...] = ()
    fidelity: CapabilityFidelityDeclaration | None = None
    required_verifications: tuple[CapabilityVerificationRequirement, ...] = ()
    required_approvals: tuple[str, ...] = ()
    evidence_artifacts: tuple[ArtifactPointer, ...] = ()

    @field_validator(
        "evaluation_identifier", "objective_identifier", "capability_name"
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="capability feasibility identifier")

    @field_validator("execution_target")
    @classmethod
    def validate_target(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_identifier(value, label="execution target")

    @field_validator("findings")
    @classmethod
    def order_findings(
        cls, value: tuple[FeasibilityFinding, ...]
    ) -> tuple[FeasibilityFinding, ...]:
        identifiers = [item.finding_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Feasibility finding identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.finding_identifier))

    @field_validator("unresolved_requirement_identifiers", "required_approvals")
    @classmethod
    def order_requirements(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="feasibility requirement")

    @field_validator("estimates")
    @classmethod
    def order_estimates(
        cls, value: tuple[CapabilityEstimate, ...]
    ) -> tuple[CapabilityEstimate, ...]:
        fingerprints = [item.fingerprint for item in value]
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("Capability feasibility estimates must be unique.")
        return tuple(sorted(value, key=lambda item: item.fingerprint))

    @field_validator("required_verifications")
    @classmethod
    def order_verifications(
        cls, value: tuple[CapabilityVerificationRequirement, ...]
    ) -> tuple[CapabilityVerificationRequirement, ...]:
        identities = [
            (item.verifier_identifier, item.subject_artifact_type) for item in value
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("Capability feasibility verifications must be unique.")
        return tuple(
            sorted(
                value,
                key=lambda item: (item.verifier_identifier, item.subject_artifact_type),
            )
        )

    @field_validator("evidence_artifacts")
    @classmethod
    def order_evidence(
        cls, value: tuple[ArtifactPointer, ...]
    ) -> tuple[ArtifactPointer, ...]:
        return _ordered_artifacts(value, label="capability feasibility evidence")

    @model_validator(mode="after")
    def validate_identity_and_status(self) -> Self:
        for finding in self.findings:
            if (
                finding.capability_name != self.capability_name
                or finding.capability_version != self.capability_version
            ):
                raise ValueError("Every finding must reference the evaluated capability.")
        expected = max(
            (item.status for item in self.findings),
            key=lambda status: status.precedence,
            default=FeasibilityStatus.FEASIBLE,
        )
        if self.status != expected:
            raise ValueError("Capability feasibility status must follow finding precedence.")
        return self


class CandidatePlanAssignment(CanonicalModel):
    """One selected, non-executing capability assignment for an objective."""

    assignment_identifier: str
    objective_identifier: str
    capability_name: str
    capability_version: CapabilityVersion
    execution_target: str | None = None
    feasibility: CapabilityFeasibilityResult
    input_artifacts: tuple[ArtifactPointer, ...] = ()
    input_artifact_types: tuple[str, ...] = ()
    produced_artifact_types: tuple[str, ...] = ()

    @field_validator(
        "assignment_identifier", "objective_identifier", "capability_name"
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="candidate assignment identifier")

    @field_validator("execution_target")
    @classmethod
    def validate_target(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_identifier(value, label="execution target")

    @field_validator("input_artifacts")
    @classmethod
    def order_artifacts(
        cls, value: tuple[ArtifactPointer, ...]
    ) -> tuple[ArtifactPointer, ...]:
        return _ordered_artifacts(value, label="candidate assignment input")

    @field_validator("input_artifact_types", "produced_artifact_types")
    @classmethod
    def order_artifact_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="candidate assignment artifact type")

    @model_validator(mode="after")
    def validate_feasibility_reference(self) -> Self:
        if (
            self.objective_identifier != self.feasibility.objective_identifier
            or self.capability_name != self.feasibility.capability_name
            or self.capability_version != self.feasibility.capability_version
            or self.execution_target != self.feasibility.execution_target
        ):
            raise ValueError("Candidate assignment feasibility identity must match.")
        if self.feasibility.status not in {
            FeasibilityStatus.FEASIBLE,
            FeasibilityStatus.CONDITIONAL,
        }:
            raise ValueError("Only feasible or conditional capabilities may be assigned.")
        return self


class RejectedCapabilityAlternative(CanonicalModel):
    """One evaluated alternative not selected by deterministic plan construction."""

    alternative_identifier: str
    objective_identifier: str
    capability_name: str
    capability_version: CapabilityVersion
    execution_target: str | None = None
    feasibility: CapabilityFeasibilityResult
    rejection_reasons: tuple[str, ...]

    @field_validator(
        "alternative_identifier", "objective_identifier", "capability_name"
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="rejected capability identifier")

    @field_validator("execution_target")
    @classmethod
    def validate_target(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_identifier(value, label="execution target")

    @field_validator("rejection_reasons")
    @classmethod
    def order_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("A rejected capability requires an explanation.")
        return _ordered_text(value, label="capability rejection reason")

    @model_validator(mode="after")
    def validate_feasibility_reference(self) -> Self:
        if (
            self.objective_identifier != self.feasibility.objective_identifier
            or self.capability_name != self.feasibility.capability_name
            or self.capability_version != self.feasibility.capability_version
            or self.execution_target != self.feasibility.execution_target
        ):
            raise ValueError("Rejected capability feasibility identity must match.")
        return self


class CandidateResearchPlan(CanonicalModel):
    """Explainable candidate assignments without execution or authorization."""

    plan_identifier: str
    schema_version: CapabilityVersion
    objective_identifier: str
    objective_fingerprint: str
    project_identifier: str
    molecular_system_identifiers: tuple[str, ...] = ()
    planning_constraints_fingerprint: str
    resource_snapshot_identifier: str
    resource_snapshot_fingerprint: str
    planning_facts: PlanningFactSet
    selected_assignments: tuple[CandidatePlanAssignment, ...] = ()
    rejected_alternatives: tuple[RejectedCapabilityAlternative, ...] = ()
    unresolved_requirement_identifiers: tuple[str, ...] = ()
    unresolved_goal_identifiers: tuple[str, ...] = ()
    aggregate_estimates: tuple[CapabilityEstimate, ...] = ()
    verification_requirements: tuple[CapabilityVerificationRequirement, ...] = ()
    approval_requirements: tuple[str, ...] = ()
    evidence_artifacts: tuple[ArtifactPointer, ...] = ()
    provenance: CreationProvenance

    @field_validator(
        "plan_identifier",
        "objective_identifier",
        "project_identifier",
        "resource_snapshot_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="candidate plan identifier")

    @field_validator(
        "objective_fingerprint",
        "planning_constraints_fingerprint",
        "resource_snapshot_fingerprint",
    )
    @classmethod
    def validate_fingerprints(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("Candidate plan fingerprints must be lowercase SHA-256 values.")
        return value

    @field_validator(
        "molecular_system_identifiers",
        "unresolved_requirement_identifiers",
        "unresolved_goal_identifiers",
        "approval_requirements",
    )
    @classmethod
    def order_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="candidate plan identifier")

    @field_validator("selected_assignments")
    @classmethod
    def order_assignments(
        cls, value: tuple[CandidatePlanAssignment, ...]
    ) -> tuple[CandidatePlanAssignment, ...]:
        identifiers = [item.assignment_identifier for item in value]
        identities = [
            (item.capability_name, *_version_key(item.capability_version)) for item in value
        ]
        if len(identifiers) != len(set(identifiers)) or len(identities) != len(
            set(identities)
        ):
            raise ValueError("Candidate plan assignments must be unique.")
        return tuple(sorted(value, key=lambda item: item.assignment_identifier))

    @field_validator("rejected_alternatives")
    @classmethod
    def order_alternatives(
        cls, value: tuple[RejectedCapabilityAlternative, ...]
    ) -> tuple[RejectedCapabilityAlternative, ...]:
        identifiers = [item.alternative_identifier for item in value]
        identities = [
            (item.capability_name, *_version_key(item.capability_version)) for item in value
        ]
        if len(identifiers) != len(set(identifiers)) or len(identities) != len(
            set(identities)
        ):
            raise ValueError("Rejected capability alternatives must be unique.")
        return tuple(sorted(value, key=lambda item: item.alternative_identifier))

    @field_validator("aggregate_estimates")
    @classmethod
    def order_aggregate_estimates(
        cls, value: tuple[CapabilityEstimate, ...]
    ) -> tuple[CapabilityEstimate, ...]:
        identities = [(item.estimate_type.value, item.unit) for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError("Candidate plan aggregate estimates must be unique.")
        return tuple(sorted(value, key=lambda item: (item.estimate_type.value, item.unit)))

    @field_validator("verification_requirements")
    @classmethod
    def order_verifications(
        cls, value: tuple[CapabilityVerificationRequirement, ...]
    ) -> tuple[CapabilityVerificationRequirement, ...]:
        identities = [
            (item.verifier_identifier, item.subject_artifact_type) for item in value
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("Candidate plan verification requirements must be unique.")
        return tuple(
            sorted(
                value,
                key=lambda item: (item.verifier_identifier, item.subject_artifact_type),
            )
        )

    @field_validator("evidence_artifacts")
    @classmethod
    def order_evidence(
        cls, value: tuple[ArtifactPointer, ...]
    ) -> tuple[ArtifactPointer, ...]:
        return _ordered_artifacts(value, label="candidate plan evidence")

    @model_validator(mode="after")
    def validate_plan_references(self) -> Self:
        selected = {
            (item.capability_name, *_version_key(item.capability_version))
            for item in self.selected_assignments
        }
        rejected = {
            (item.capability_name, *_version_key(item.capability_version))
            for item in self.rejected_alternatives
        }
        if selected.intersection(rejected):
            raise ValueError("A capability cannot be both selected and rejected.")
        if any(
            item.objective_identifier != self.objective_identifier
            for item in (*self.selected_assignments, *self.rejected_alternatives)
        ):
            raise ValueError("Every plan alternative must reference the plan objective.")
        return self
