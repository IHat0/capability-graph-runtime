"""Candidate-type-neutral contracts for evidence-driven scientific discovery."""

from __future__ import annotations

import math
from enum import Enum
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.science.artifacts import ArtifactPointer
from cgr.science.canonical import (
    BoundedMetadata,
    CanonicalModel,
    validate_bounded_metadata,
    validate_identifier,
    validate_sha256,
)

_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_MAX_TEXT_LENGTH = 4096
_MAX_DESCRIPTION_LENGTH = 16_384
_MAX_COLLECTION = 4096


def _ordered_identifiers(
    values: tuple[str, ...],
    *,
    label: str,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if not allow_empty and not values:
        raise ValueError(f"{label.capitalize()} values cannot be empty.")
    if len(values) > _MAX_COLLECTION:
        raise ValueError(
            f"{label.capitalize()} values exceed the bounded collection size."
        )
    validated = tuple(validate_identifier(value, label=label) for value in values)
    if len(validated) != len(set(validated)):
        raise ValueError(f"{label.capitalize()} values must be unique.")
    return tuple(sorted(validated))


def _ordered_artifacts(
    values: tuple[ArtifactPointer, ...],
) -> tuple[ArtifactPointer, ...]:
    if len(values) > _MAX_COLLECTION:
        raise ValueError("Artifact collections exceed the bounded collection size.")
    identities = [(value.artifact_identifier, value.content_sha256) for value in values]
    if len(identities) != len(set(identities)):
        raise ValueError("Artifact references must be unique.")
    return tuple(
        sorted(
            values,
            key=lambda value: (value.artifact_identifier, value.content_sha256),
        )
    )


def _bounded_text(value: str, *, label: str, maximum: int = _MAX_TEXT_LENGTH) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label.capitalize()} cannot be blank.")
    if len(normalized) > maximum:
        raise ValueError(f"{label.capitalize()} is too long.")
    return normalized


def _finite(value: object, *, label: str) -> object:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label.capitalize()} must be numeric.")
    if not math.isfinite(float(value)):
        raise ValueError(f"{label.capitalize()} must be finite.")
    return value


class ObjectiveDirection(str, Enum):
    """Optimization direction for one campaign objective."""

    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"


class SelectionOutcome(str, Enum):
    """Non-authorizing discovery selection states."""

    SELECTED = "selected"
    REJECTED = "rejected"
    INELIGIBLE = "ineligible"


class DiscoveryObjective(CanonicalModel):
    """One candidate-type-neutral objective used by a discovery campaign."""

    objective_identifier: str
    metric_identifier: str
    direction: ObjectiveDirection
    weight: float = Field(default=1.0, ge=0)
    hard_constraint: bool = False
    required: bool = True
    require_uncertainty: bool = False
    maximum_uncertainty: float | None = Field(default=None, ge=0)
    required_verifier_families: tuple[str, ...] = ()
    description: str = Field(default="", max_length=_MAX_DESCRIPTION_LENGTH)
    metadata: BoundedMetadata = Field(default_factory=dict)

    @field_validator("objective_identifier", "metric_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="discovery objective identifier")

    @field_validator("weight", mode="before")
    @classmethod
    def validate_weight(cls, value: object) -> object:
        return _finite(value, label="discovery objective weight")

    @field_validator("maximum_uncertainty", mode="before")
    @classmethod
    def validate_uncertainty(cls, value: object) -> object:
        if value is None:
            return value
        return _finite(value, label="discovery objective uncertainty")

    @field_validator("required_verifier_families")
    @classmethod
    def order_verifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="verifier family")

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        return value.strip()

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)

    @model_validator(mode="after")
    def validate_uncertainty_requirement(self) -> Self:
        if self.maximum_uncertainty is not None and not self.require_uncertainty:
            raise ValueError(
                "An objective uncertainty ceiling requires uncertainty evidence."
            )
        return self


class PredictedImprovement(CanonicalModel):
    """Explicit prediction motivating a candidate transformation."""

    objective_identifier: str
    expected_direction: ObjectiveDirection
    expected_delta: float | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=_MAX_TEXT_LENGTH)

    @field_validator("objective_identifier")
    @classmethod
    def validate_objective_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="predicted objective identifier")

    @field_validator("expected_delta", "confidence", mode="before")
    @classmethod
    def validate_numbers(cls, value: object) -> object:
        if value is None:
            return value
        return _finite(value, label="predicted improvement number")

    @field_validator("rationale")
    @classmethod
    def normalize_rationale(cls, value: str) -> str:
        return _bounded_text(value, label="predicted improvement rationale")


class DiscoveryDiagnosisReference(CanonicalModel):
    """Stable Phase 6 diagnosis reference without embedding mutable runtime state."""

    diagnosis_identifier: str
    diagnosis_sha256: str
    report_identifier: str
    report_sha256: str
    replannable: bool
    summary: str = Field(min_length=1, max_length=_MAX_TEXT_LENGTH)

    @field_validator("diagnosis_identifier", "report_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="scientific diagnosis reference")

    @field_validator("diagnosis_sha256", "report_sha256")
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("summary")
    @classmethod
    def normalize_summary(cls, value: str) -> str:
        return _bounded_text(value, label="diagnosis summary")


class DiscoveryReplanningReference(CanonicalModel):
    """Stable Phase 6 replanning reference used only as discovery rationale."""

    recommendation_identifier: str
    recommendation_sha256: str
    diagnosis_identifier: str
    disposition: str
    change_identifiers: tuple[str, ...] = ()
    requires_fresh_authorization: bool
    authorizes_execution: Literal[False] = False

    @field_validator(
        "recommendation_identifier",
        "diagnosis_identifier",
        "disposition",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="replanning reference identifier")

    @field_validator("recommendation_sha256")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("change_identifiers")
    @classmethod
    def order_changes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="replanning change identifier")


class CandidateTransformation(CanonicalModel):
    """Immutable explanation of how one or more parents produced a new candidate."""

    transformation_identifier: str
    transformation_kind: str
    source_candidate_identifiers: tuple[str, ...] = Field(min_length=1)
    target_candidate_type: str
    generator_identifier: str
    rationale: str = Field(min_length=1, max_length=_MAX_TEXT_LENGTH)
    predicted_improvements: tuple[PredictedImprovement, ...] = ()
    parameters: BoundedMetadata = Field(default_factory=dict)
    diagnosis_reference: DiscoveryDiagnosisReference | None = None
    replanning_reference: DiscoveryReplanningReference | None = None
    authorizes_execution: Literal[False] = False

    @field_validator(
        "transformation_identifier",
        "transformation_kind",
        "target_candidate_type",
        "generator_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="candidate transformation identifier")

    @field_validator("source_candidate_identifiers")
    @classmethod
    def order_sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(
            value,
            label="source candidate identifier",
            allow_empty=False,
        )

    @field_validator("rationale")
    @classmethod
    def normalize_rationale(cls, value: str) -> str:
        return _bounded_text(value, label="candidate transformation rationale")

    @field_validator("predicted_improvements")
    @classmethod
    def order_predictions(
        cls, value: tuple[PredictedImprovement, ...]
    ) -> tuple[PredictedImprovement, ...]:
        objective_ids = [item.objective_identifier for item in value]
        if len(objective_ids) != len(set(objective_ids)):
            raise ValueError(
                "Predicted improvements require unique objective identifiers."
            )
        return tuple(sorted(value, key=lambda item: item.objective_identifier))

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)

    @model_validator(mode="after")
    def validate_phase6_references(self) -> Self:
        if self.replanning_reference is not None and self.diagnosis_reference is None:
            raise ValueError(
                "A replanning reference requires its Phase 6 diagnosis reference."
            )
        if (
            self.replanning_reference is not None
            and self.diagnosis_reference is not None
            and self.replanning_reference.diagnosis_identifier
            != self.diagnosis_reference.diagnosis_identifier
        ):
            raise ValueError(
                "Replanning and diagnosis references must belong together."
            )
        return self


class DiscoveryCandidate(CanonicalModel):
    """Immutable scientific candidate; candidate type is an extensible identifier."""

    schema_version: CapabilityVersion = _SCHEMA_VERSION
    candidate_identifier: str
    candidate_type: str
    generation: int = Field(ge=0)
    generated_by: str
    representation_artifacts: tuple[ArtifactPointer, ...] = Field(min_length=1)
    objective_identifiers: tuple[str, ...] = ()
    parent_candidate_identifiers: tuple[str, ...] = ()
    transformation: CandidateTransformation | None = None
    metadata: BoundedMetadata = Field(default_factory=dict)
    authorization_carried_forward: Literal[False] = False
    authorizes_execution: Literal[False] = False

    @field_validator("candidate_identifier", "candidate_type", "generated_by")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="discovery candidate identifier")

    @field_validator("representation_artifacts")
    @classmethod
    def order_artifacts(
        cls, value: tuple[ArtifactPointer, ...]
    ) -> tuple[ArtifactPointer, ...]:
        if not value:
            raise ValueError(
                "Discovery candidates require a content-addressed representation."
            )
        return _ordered_artifacts(value)

    @field_validator("objective_identifiers", "parent_candidate_identifiers")
    @classmethod
    def order_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="candidate identifier")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)

    @model_validator(mode="after")
    def validate_lineage_identity(self) -> Self:
        if self.candidate_identifier in self.parent_candidate_identifiers:
            raise ValueError("A discovery candidate cannot be its own parent.")

        if self.generation == 0:
            if self.parent_candidate_identifiers or self.transformation is not None:
                raise ValueError(
                    "Generation-zero discovery candidates cannot declare transformed parents."
                )
            return self

        if not self.parent_candidate_identifiers or self.transformation is None:
            raise ValueError(
                "Transformed discovery candidates require parents and one transformation."
            )
        if (
            self.transformation.source_candidate_identifiers
            != self.parent_candidate_identifiers
        ):
            raise ValueError(
                "Candidate parents must exactly match transformation sources."
            )
        if self.transformation.target_candidate_type != self.candidate_type:
            raise ValueError(
                "Transformation target type must match the child candidate type."
            )
        if self.transformation.generator_identifier != self.generated_by:
            raise ValueError(
                "Transformation generator must match candidate provenance."
            )
        predicted = {
            item.objective_identifier
            for item in self.transformation.predicted_improvements
        }
        if not predicted.issubset(self.objective_identifiers):
            raise ValueError(
                "Predicted improvements must reference candidate scientific objectives."
            )
        return self


class CandidateLineageEdge(CanonicalModel):
    """One immutable parent-to-child candidate transformation edge."""

    parent_candidate_identifier: str
    parent_candidate_sha256: str
    parent_generation: int = Field(ge=0)
    child_candidate_identifier: str
    child_candidate_sha256: str
    child_generation: int = Field(ge=1)
    transformation_identifier: str

    @field_validator(
        "parent_candidate_identifier",
        "child_candidate_identifier",
        "transformation_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="candidate lineage identifier")

    @field_validator("parent_candidate_sha256", "child_candidate_sha256")
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def validate_edge(self) -> Self:
        if self.parent_candidate_identifier == self.child_candidate_identifier:
            raise ValueError("Candidate lineage cannot reference itself.")
        if self.child_generation != self.parent_generation + 1:
            raise ValueError("Candidate lineage generations must advance exactly once.")
        return self


class CandidateLineageGraph(CanonicalModel):
    """Deterministic immutable DAG describing candidate ancestry only."""

    edges: tuple[CandidateLineageEdge, ...] = ()

    @field_validator("edges")
    @classmethod
    def order_edges(
        cls, value: tuple[CandidateLineageEdge, ...]
    ) -> tuple[CandidateLineageEdge, ...]:
        fingerprints = [item.fingerprint for item in value]
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("Duplicate candidate-lineage edges are not meaningful.")
        return tuple(sorted(value, key=lambda item: item.fingerprint))

    @model_validator(mode="after")
    def validate_dag(self) -> Self:
        identities: dict[str, tuple[str, int]] = {}
        adjacency: dict[str, set[str]] = {}
        for edge in self.edges:
            for candidate_id, fingerprint, generation in (
                (
                    edge.parent_candidate_identifier,
                    edge.parent_candidate_sha256,
                    edge.parent_generation,
                ),
                (
                    edge.child_candidate_identifier,
                    edge.child_candidate_sha256,
                    edge.child_generation,
                ),
            ):
                identity = (fingerprint, generation)
                previous = identities.get(candidate_id)
                if previous is not None and previous != identity:
                    raise ValueError(
                        "One candidate identifier cannot represent multiple candidate identities."
                    )
                identities[candidate_id] = identity
            adjacency.setdefault(edge.parent_candidate_identifier, set()).add(
                edge.child_candidate_identifier
            )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("Candidate lineage must remain acyclic.")
            if node in visited:
                return
            visiting.add(node)
            for child in adjacency.get(node, set()):
                visit(child)
            visiting.remove(node)
            visited.add(node)

        for node in sorted(adjacency):
            visit(node)
        return self

    def add_candidate(
        self,
        candidate: DiscoveryCandidate,
        parents: tuple[DiscoveryCandidate, ...],
    ) -> CandidateLineageGraph:
        """Return a new graph with validated edges for one transformed candidate."""

        if candidate.generation == 0:
            if parents:
                raise ValueError(
                    "Generation-zero candidates cannot be added with parents."
                )
            return self
        by_id = {parent.candidate_identifier: parent for parent in parents}
        if len(by_id) != len(parents):
            raise ValueError("Candidate lineage parents must be unique.")
        if tuple(sorted(by_id)) != candidate.parent_candidate_identifiers:
            raise ValueError(
                "Supplied parents must exactly match candidate parent identifiers."
            )
        if candidate.transformation is None:
            raise ValueError("Transformed candidates require transformation evidence.")
        edges = tuple(
            CandidateLineageEdge(
                parent_candidate_identifier=parent.candidate_identifier,
                parent_candidate_sha256=parent.fingerprint,
                parent_generation=parent.generation,
                child_candidate_identifier=candidate.candidate_identifier,
                child_candidate_sha256=candidate.fingerprint,
                child_generation=candidate.generation,
                transformation_identifier=candidate.transformation.transformation_identifier,
            )
            for parent in parents
        )
        return CandidateLineageGraph(edges=(*self.edges, *edges))


class CandidateEvidenceRecord(CanonicalModel):
    """Scientific evidence lineage kept separate from candidate ancestry."""

    evidence_identifier: str
    candidate_identifier: str
    candidate_sha256: str
    stage_identifier: str
    stage_order: int = Field(ge=0)
    evaluator_identifier: str
    artifacts: tuple[ArtifactPointer, ...] = Field(min_length=1)
    metadata: BoundedMetadata = Field(default_factory=dict)
    authorizes_execution: Literal[False] = False

    @field_validator(
        "evidence_identifier",
        "candidate_identifier",
        "stage_identifier",
        "evaluator_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="candidate evidence identifier")

    @field_validator("candidate_sha256")
    @classmethod
    def validate_candidate_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("artifacts")
    @classmethod
    def order_artifacts(
        cls, value: tuple[ArtifactPointer, ...]
    ) -> tuple[ArtifactPointer, ...]:
        if not value:
            raise ValueError("Candidate evidence records require artifact evidence.")
        return _ordered_artifacts(value)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)


class CandidateVerificationRecord(CanonicalModel):
    """Candidate-scoped projection of a Phase 6 scientific verification report."""

    candidate_identifier: str
    candidate_sha256: str
    report_identifier: str
    report_sha256: str
    objective_family: str
    subject_identifier: str
    request_sha256: str
    overall_outcome: Literal["passed", "failed"]
    execution_integrity_passed: bool
    scientific_quality_passed: bool
    authorization_passed: bool

    @field_validator(
        "candidate_identifier",
        "report_identifier",
        "objective_family",
        "subject_identifier",
        "overall_outcome",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="candidate verification identifier")

    @field_validator("candidate_sha256", "report_sha256", "request_sha256")
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def validate_summary_flags(self) -> Self:
        all_dimensions_pass = (
            self.execution_integrity_passed
            and self.scientific_quality_passed
            and self.authorization_passed
        )
        if (self.overall_outcome == "passed") != all_dimensions_pass:
            raise ValueError(
                "Candidate verification outcome must agree with execution, "
                "scientific-quality and authorization summary flags."
            )
        return self


class CandidateConstraintResult(CanonicalModel):
    """One immutable candidate constraint result."""

    result_identifier: str
    candidate_identifier: str
    candidate_sha256: str
    constraint_identifier: str
    passed: bool
    hard: bool
    rationale: str = Field(min_length=1, max_length=_MAX_TEXT_LENGTH)
    evidence_identifiers: tuple[str, ...] = ()

    @field_validator(
        "result_identifier",
        "candidate_identifier",
        "constraint_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="candidate constraint identifier")

    @field_validator("candidate_sha256")
    @classmethod
    def validate_candidate_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("evidence_identifiers")
    @classmethod
    def order_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="candidate evidence identifier")

    @field_validator("rationale")
    @classmethod
    def normalize_rationale(cls, value: str) -> str:
        return _bounded_text(value, label="constraint rationale")


class CandidateObjectiveScore(CanonicalModel):
    """Raw and normalized value for one explicit discovery objective."""

    score_identifier: str
    candidate_identifier: str
    candidate_sha256: str
    objective_identifier: str
    value: float
    normalized_value: float = Field(ge=0, le=1)
    uncertainty: float | None = Field(default=None, ge=0)
    satisfies_objective: bool
    evidence_identifiers: tuple[str, ...] = ()
    verification_report_identifiers: tuple[str, ...] = ()

    @field_validator(
        "score_identifier",
        "candidate_identifier",
        "objective_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="candidate objective score identifier")

    @field_validator("candidate_sha256")
    @classmethod
    def validate_candidate_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("value", "normalized_value", "uncertainty", mode="before")
    @classmethod
    def validate_numbers(cls, value: object) -> object:
        if value is None:
            return value
        return _finite(value, label="candidate objective score")

    @field_validator("evidence_identifiers", "verification_report_identifiers")
    @classmethod
    def order_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="candidate score evidence identifier")


class CandidateAssessment(CanonicalModel):
    """Immutable assessment overlay; evaluation never mutates the candidate itself."""

    assessment_identifier: str
    candidate_identifier: str
    candidate_sha256: str
    evidence_records: tuple[CandidateEvidenceRecord, ...] = ()
    verification_records: tuple[CandidateVerificationRecord, ...] = ()
    constraint_results: tuple[CandidateConstraintResult, ...] = ()
    objective_scores: tuple[CandidateObjectiveScore, ...] = ()
    metadata: BoundedMetadata = Field(default_factory=dict)

    @field_validator("assessment_identifier", "candidate_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="candidate assessment identifier")

    @field_validator("candidate_sha256")
    @classmethod
    def validate_candidate_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("evidence_records")
    @classmethod
    def order_evidence_records(
        cls, value: tuple[CandidateEvidenceRecord, ...]
    ) -> tuple[CandidateEvidenceRecord, ...]:
        return tuple(sorted(value, key=lambda item: item.evidence_identifier))

    @field_validator("verification_records")
    @classmethod
    def order_verification_records(
        cls, value: tuple[CandidateVerificationRecord, ...]
    ) -> tuple[CandidateVerificationRecord, ...]:
        return tuple(sorted(value, key=lambda item: item.report_identifier))

    @field_validator("constraint_results")
    @classmethod
    def order_constraint_results(
        cls, value: tuple[CandidateConstraintResult, ...]
    ) -> tuple[CandidateConstraintResult, ...]:
        return tuple(
            sorted(
                value,
                key=lambda item: (item.constraint_identifier, item.result_identifier),
            )
        )

    @field_validator("objective_scores")
    @classmethod
    def order_objective_scores(
        cls, value: tuple[CandidateObjectiveScore, ...]
    ) -> tuple[CandidateObjectiveScore, ...]:
        return tuple(sorted(value, key=lambda item: item.objective_identifier))

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)

    @model_validator(mode="after")
    def validate_candidate_scope(self) -> Self:
        groups = (
            self.evidence_records,
            self.verification_records,
            self.constraint_results,
            self.objective_scores,
        )
        for records in groups:
            for record in records:
                if (
                    record.candidate_identifier != self.candidate_identifier
                    or record.candidate_sha256 != self.candidate_sha256
                ):
                    raise ValueError(
                        "Candidate assessment evidence must belong to one immutable candidate."
                    )
        evidence_ids = [item.evidence_identifier for item in self.evidence_records]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError(
                "Candidate assessment evidence identifiers must be unique."
            )
        report_ids = [item.report_identifier for item in self.verification_records]
        if len(report_ids) != len(set(report_ids)):
            raise ValueError(
                "Candidate verification report identifiers must be unique."
            )
        constraint_ids = [
            item.constraint_identifier for item in self.constraint_results
        ]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError(
                "Candidate constraint identifiers must be unique per assessment."
            )
        score_ids = [item.objective_identifier for item in self.objective_scores]
        if len(score_ids) != len(set(score_ids)):
            raise ValueError("Candidate objective scores must be unique per objective.")
        return self

    @property
    def has_hard_constraint_failure(self) -> bool:
        """Return whether any explicit hard constraint failed."""

        return any(not item.passed and item.hard for item in self.constraint_results)

    @property
    def scientific_quality_passed(self) -> bool:
        """Return whether Phase 6 quality evidence exists and all such evidence passes."""

        return bool(self.verification_records) and all(
            item.scientific_quality_passed for item in self.verification_records
        )


class CandidateSelectionDecision(CanonicalModel):
    """Transparent ranking outcome that never grants execution authority."""

    decision_identifier: str
    campaign_identifier: str
    campaign_sha256: str
    candidate_identifier: str
    candidate_sha256: str
    assessment_identifier: str
    assessment_sha256: str
    outcome: SelectionOutcome
    rank: int | None = Field(default=None, ge=1)
    pareto_front: int | None = Field(default=None, ge=0)
    composite_score: float | None = Field(default=None, ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=_MAX_TEXT_LENGTH)
    authorizes_execution: Literal[False] = False

    @field_validator(
        "decision_identifier",
        "campaign_identifier",
        "candidate_identifier",
        "assessment_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="candidate selection identifier")

    @field_validator(
        "campaign_sha256",
        "candidate_sha256",
        "assessment_sha256",
    )
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("composite_score", mode="before")
    @classmethod
    def validate_composite_score(cls, value: object) -> object:
        if value is None:
            return value
        return _finite(value, label="composite score")

    @field_validator("rationale")
    @classmethod
    def normalize_rationale(cls, value: str) -> str:
        return _bounded_text(value, label="selection rationale")

    @model_validator(mode="after")
    def validate_ranking_fields(self) -> Self:
        ranked = self.rank is not None or self.pareto_front is not None
        if self.outcome is SelectionOutcome.INELIGIBLE and ranked:
            raise ValueError("Ineligible candidates cannot receive a scientific rank.")
        if self.outcome is SelectionOutcome.SELECTED and (
            self.rank is None or self.pareto_front is None
        ):
            raise ValueError(
                "Selected candidates require rank and Pareto-front evidence."
            )
        return self


class DiscoveryCandidateRecord(CanonicalModel):
    """Transparent immutable view combining candidate, assessment, and selection evidence."""

    candidate: DiscoveryCandidate
    assessment: CandidateAssessment | None = None
    selection_decision: CandidateSelectionDecision | None = None

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if self.assessment is not None and (
            self.assessment.candidate_identifier != self.candidate.candidate_identifier
            or self.assessment.candidate_sha256 != self.candidate.fingerprint
        ):
            raise ValueError(
                "Candidate record assessment must belong to its candidate."
            )
        if self.selection_decision is not None:
            if self.assessment is None:
                raise ValueError("Candidate selection evidence requires an assessment.")
            if (
                self.selection_decision.candidate_identifier
                != self.candidate.candidate_identifier
                or self.selection_decision.candidate_sha256
                != self.candidate.fingerprint
                or self.selection_decision.assessment_identifier
                != self.assessment.assessment_identifier
                or self.selection_decision.assessment_sha256
                != self.assessment.fingerprint
            ):
                raise ValueError(
                    "Candidate selection evidence must belong to the candidate assessment."
                )
        return self


class DiscoveryBudgetLimit(CanonicalModel):
    """Generic bounded resource ceiling for a discovery campaign."""

    dimension: str
    unit: str
    maximum: float = Field(gt=0)

    @field_validator("dimension", "unit")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="discovery budget identifier")

    @field_validator("maximum", mode="before")
    @classmethod
    def validate_maximum(cls, value: object) -> object:
        return _finite(value, label="discovery budget maximum")


class DiscoveryCampaignBudget(CanonicalModel):
    """Hard bounds preventing unbounded discovery search."""

    max_generations: int = Field(default=10, ge=1, le=10_000)
    max_candidates_total: int = Field(default=1000, ge=1, le=1_000_000)
    max_candidates_per_generation: int = Field(default=100, ge=1, le=100_000)
    max_validity_checks: int = Field(default=10_000, ge=1, le=10_000_000)
    max_evaluations: int = Field(default=10_000, ge=1, le=10_000_000)
    max_expensive_evaluations: int = Field(default=1000, ge=0, le=1_000_000)
    maximum_wall_time_seconds: float | None = Field(default=None, gt=0)
    resource_ceilings: tuple[DiscoveryBudgetLimit, ...] = ()

    @field_validator("maximum_wall_time_seconds", mode="before")
    @classmethod
    def validate_wall_time(cls, value: object) -> object:
        if value is None:
            return value
        return _finite(value, label="maximum wall time")

    @field_validator("resource_ceilings")
    @classmethod
    def order_resource_ceilings(
        cls, value: tuple[DiscoveryBudgetLimit, ...]
    ) -> tuple[DiscoveryBudgetLimit, ...]:
        identities = [(item.dimension, item.unit) for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError(
                "Discovery resource ceiling dimension/unit pairs must be unique."
            )
        return tuple(sorted(value, key=lambda item: (item.dimension, item.unit)))

    @model_validator(mode="after")
    def validate_candidate_bounds(self) -> Self:
        if self.max_candidates_per_generation > self.max_candidates_total:
            raise ValueError(
                "Per-generation candidate budget cannot exceed total candidate budget."
            )
        if self.max_expensive_evaluations > self.max_evaluations:
            raise ValueError(
                "Expensive evaluation budget cannot exceed total evaluation budget."
            )
        return self


class DiscoveryStoppingPolicy(CanonicalModel):
    """Explicit stopping rules for a future discovery campaign runtime."""

    stop_when_objectives_satisfied: bool = True
    stop_on_budget_exhaustion: Literal[True] = True
    stagnation_generations: int = Field(default=3, ge=1, le=1000)
    maximum_consecutive_invalid_generations: int = Field(default=3, ge=1, le=1000)
    minimum_composite_improvement: float = Field(default=0.0, ge=0, le=1)
    minimum_new_pareto_candidates: int = Field(default=0, ge=0, le=100_000)

    @field_validator("minimum_composite_improvement", mode="before")
    @classmethod
    def validate_improvement(cls, value: object) -> object:
        return _finite(value, label="minimum composite improvement")


class DiscoveryCampaign(CanonicalModel):
    """Candidate-type-neutral discovery objective, policy, and bounded search contract."""

    schema_version: CapabilityVersion = _SCHEMA_VERSION
    campaign_identifier: str
    description: str = Field(min_length=1, max_length=_MAX_DESCRIPTION_LENGTH)
    objectives: tuple[DiscoveryObjective, ...] = Field(min_length=1)
    permitted_candidate_types: tuple[str, ...] = ()
    constraint_identifiers: tuple[str, ...] = ()
    budget: DiscoveryCampaignBudget = Field(default_factory=DiscoveryCampaignBudget)
    stopping_policy: DiscoveryStoppingPolicy = Field(
        default_factory=DiscoveryStoppingPolicy
    )
    metadata: BoundedMetadata = Field(default_factory=dict)
    authorizes_execution: Literal[False] = False

    @field_validator("campaign_identifier")
    @classmethod
    def validate_campaign_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="discovery campaign identifier")

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        return _bounded_text(
            value,
            label="discovery campaign description",
            maximum=_MAX_DESCRIPTION_LENGTH,
        )

    @field_validator("objectives")
    @classmethod
    def order_objectives(
        cls, value: tuple[DiscoveryObjective, ...]
    ) -> tuple[DiscoveryObjective, ...]:
        identifiers = [item.objective_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Discovery campaign objective identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.objective_identifier))

    @field_validator("permitted_candidate_types", "constraint_identifiers")
    @classmethod
    def order_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="discovery campaign identifier")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)

    def accepts_candidate_type(self, candidate_type: str) -> bool:
        """Return whether the campaign allows an extensible candidate type."""

        normalized = validate_identifier(candidate_type, label="candidate type")
        return (
            not self.permitted_candidate_types
            or normalized in self.permitted_candidate_types
        )


class CandidateGenerationRequest(CanonicalModel):
    """Non-authorizing request for candidate proposals from an external generator."""

    request_identifier: str
    campaign_identifier: str
    campaign_sha256: str
    generation: int = Field(ge=0)
    parent_candidates: tuple[DiscoveryCandidate, ...] = ()
    parent_assessments: tuple[CandidateAssessment, ...] = ()
    objective_identifiers: tuple[str, ...] = ()
    diagnosis_references: tuple[DiscoveryDiagnosisReference, ...] = ()
    replanning_references: tuple[DiscoveryReplanningReference, ...] = ()
    max_candidates: int = Field(ge=1, le=100_000)
    metadata: BoundedMetadata = Field(default_factory=dict)
    authorizes_execution: Literal[False] = False

    @field_validator("request_identifier", "campaign_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="candidate generation identifier")

    @field_validator("campaign_sha256")
    @classmethod
    def validate_campaign_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("objective_identifiers")
    @classmethod
    def order_objectives(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="generation objective identifier")

    @field_validator("parent_candidates")
    @classmethod
    def order_parents(
        cls, value: tuple[DiscoveryCandidate, ...]
    ) -> tuple[DiscoveryCandidate, ...]:
        identifiers = [item.candidate_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Candidate generation parents must be unique.")
        return tuple(sorted(value, key=lambda item: item.candidate_identifier))

    @field_validator("parent_assessments")
    @classmethod
    def order_parent_assessments(
        cls, value: tuple[CandidateAssessment, ...]
    ) -> tuple[CandidateAssessment, ...]:
        identifiers = [item.candidate_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Candidate-generation parent assessments must be unique.")
        return tuple(sorted(value, key=lambda item: item.candidate_identifier))

    @field_validator("diagnosis_references")
    @classmethod
    def order_diagnoses(
        cls, value: tuple[DiscoveryDiagnosisReference, ...]
    ) -> tuple[DiscoveryDiagnosisReference, ...]:
        ids = [item.diagnosis_identifier for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("Discovery diagnosis references must be unique.")
        return tuple(sorted(value, key=lambda item: item.diagnosis_identifier))

    @field_validator("replanning_references")
    @classmethod
    def order_replanning(
        cls, value: tuple[DiscoveryReplanningReference, ...]
    ) -> tuple[DiscoveryReplanningReference, ...]:
        ids = [item.recommendation_identifier for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("Discovery replanning references must be unique.")
        return tuple(sorted(value, key=lambda item: item.recommendation_identifier))

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)

    @model_validator(mode="after")
    def validate_generation(self) -> Self:
        if self.generation == 0 and self.parent_candidates:
            raise ValueError(
                "Generation-zero requests cannot contain parent candidates."
            )
        if self.parent_candidates and any(
            parent.generation >= self.generation for parent in self.parent_candidates
        ):
            raise ValueError("Parent candidates must precede the requested generation.")
        if self.parent_assessments:
            parents = {
                item.candidate_identifier: item for item in self.parent_candidates
            }
            assessment_ids = {
                item.candidate_identifier for item in self.parent_assessments
            }
            if assessment_ids != set(parents):
                raise ValueError(
                    "Supplied parent assessments must cover exactly the parent candidates."
                )
            for assessment in self.parent_assessments:
                parent = parents[assessment.candidate_identifier]
                if assessment.candidate_sha256 != parent.fingerprint:
                    raise ValueError(
                        "Parent assessment identity must match its immutable parent candidate."
                    )
        diagnosis_ids = {
            item.diagnosis_identifier for item in self.diagnosis_references
        }
        if any(
            item.diagnosis_identifier not in diagnosis_ids
            for item in self.replanning_references
        ):
            raise ValueError(
                "Replanning references require matching diagnosis references."
            )
        return self


class CandidateGenerationResult(CanonicalModel):
    """Generator output containing proposals only, never authorization."""

    result_identifier: str
    generator_identifier: str
    request_sha256: str
    candidates: tuple[DiscoveryCandidate, ...]
    authorizes_execution: Literal[False] = False

    @field_validator("result_identifier", "generator_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(
            value, label="candidate generation result identifier"
        )

    @field_validator("request_sha256")
    @classmethod
    def validate_request_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("candidates")
    @classmethod
    def order_candidates(
        cls, value: tuple[DiscoveryCandidate, ...]
    ) -> tuple[DiscoveryCandidate, ...]:
        ids = [item.candidate_identifier for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("Generated candidate identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.candidate_identifier))

    @model_validator(mode="after")
    def validate_generator(self) -> Self:
        if any(
            item.generated_by != self.generator_identifier for item in self.candidates
        ):
            raise ValueError(
                "Generated candidates must identify the producing generator."
            )
        return self


class CandidateValidityResult(CanonicalModel):
    """Candidate-type-neutral validity result before expensive evaluation."""

    result_identifier: str
    checker_identifier: str
    candidate_identifier: str
    candidate_sha256: str
    valid: bool
    findings: tuple[CandidateConstraintResult, ...] = ()
    authorizes_execution: Literal[False] = False

    @field_validator(
        "result_identifier",
        "checker_identifier",
        "candidate_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="candidate validity identifier")

    @field_validator("candidate_sha256")
    @classmethod
    def validate_candidate_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("findings")
    @classmethod
    def order_findings(
        cls, value: tuple[CandidateConstraintResult, ...]
    ) -> tuple[CandidateConstraintResult, ...]:
        return tuple(
            sorted(
                value,
                key=lambda item: (item.constraint_identifier, item.result_identifier),
            )
        )

    @model_validator(mode="after")
    def validate_findings(self) -> Self:
        identities = [finding.constraint_identifier for finding in self.findings]
        if len(identities) != len(set(identities)):
            raise ValueError("Validity constraint identifiers must be unique.")
        for finding in self.findings:
            if (
                finding.candidate_identifier != self.candidate_identifier
                or finding.candidate_sha256 != self.candidate_sha256
            ):
                raise ValueError(
                    "Validity findings must belong to the checked candidate."
                )
        failed_hard = any(
            not finding.passed and finding.hard for finding in self.findings
        )
        if self.valid == failed_hard:
            raise ValueError(
                "Validity must fail exactly when a hard validity constraint fails."
            )
        return self


class CandidateEvaluationResult(CanonicalModel):
    """Immutable evaluator output; candidate identity is never changed in place."""

    result_identifier: str
    evaluator_identifier: str
    candidate_identifier: str
    candidate_sha256: str
    stage_identifier: str
    stage_order: int = Field(ge=0)
    evidence_records: tuple[CandidateEvidenceRecord, ...] = ()
    objective_scores: tuple[CandidateObjectiveScore, ...] = ()
    constraint_results: tuple[CandidateConstraintResult, ...] = ()
    expensive: bool = False
    authorizes_execution: Literal[False] = False

    @field_validator(
        "result_identifier",
        "evaluator_identifier",
        "candidate_identifier",
        "stage_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="candidate evaluation identifier")

    @field_validator("candidate_sha256")
    @classmethod
    def validate_candidate_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("evidence_records")
    @classmethod
    def order_evidence_records(
        cls, value: tuple[CandidateEvidenceRecord, ...]
    ) -> tuple[CandidateEvidenceRecord, ...]:
        return tuple(sorted(value, key=lambda item: item.evidence_identifier))

    @field_validator("objective_scores")
    @classmethod
    def order_objective_scores(
        cls, value: tuple[CandidateObjectiveScore, ...]
    ) -> tuple[CandidateObjectiveScore, ...]:
        return tuple(sorted(value, key=lambda item: item.objective_identifier))

    @field_validator("constraint_results")
    @classmethod
    def order_constraint_results(
        cls, value: tuple[CandidateConstraintResult, ...]
    ) -> tuple[CandidateConstraintResult, ...]:
        return tuple(
            sorted(
                value,
                key=lambda item: (item.constraint_identifier, item.result_identifier),
            )
        )

    @model_validator(mode="after")
    def validate_candidate_scope(self) -> Self:
        evidence_ids = [item.evidence_identifier for item in self.evidence_records]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Evaluation evidence identifiers must be unique.")
        objective_ids = [item.objective_identifier for item in self.objective_scores]
        if len(objective_ids) != len(set(objective_ids)):
            raise ValueError("Evaluation objective identifiers must be unique.")
        constraint_ids = [
            item.constraint_identifier for item in self.constraint_results
        ]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError("Evaluation constraint identifiers must be unique.")
        for record in (
            *self.evidence_records,
            *self.objective_scores,
            *self.constraint_results,
        ):
            if (
                record.candidate_identifier != self.candidate_identifier
                or record.candidate_sha256 != self.candidate_sha256
            ):
                raise ValueError(
                    "Evaluator outputs must belong to the evaluated candidate."
                )
        for evidence in self.evidence_records:
            if (
                evidence.evaluator_identifier != self.evaluator_identifier
                or evidence.stage_identifier != self.stage_identifier
                or evidence.stage_order != self.stage_order
            ):
                raise ValueError(
                    "Evaluation evidence must match evaluator stage identity."
                )
        return self


class SelectionPolicy(CanonicalModel):
    """Deterministic, non-authorizing multi-objective selection policy."""

    max_selected_candidates: int = Field(default=1, ge=1, le=100_000)
    require_complete_objectives: Literal[True] = True
    require_scientific_quality: bool = True
    select_only_first_pareto_front: bool = True
    use_composite_tiebreak: bool = True


class RankedDiscoveryCandidate(CanonicalModel):
    """Transparent candidate rank retaining its complete objective score vector."""

    candidate_identifier: str
    candidate_sha256: str
    assessment_identifier: str
    assessment_sha256: str
    pareto_front: int = Field(ge=0)
    rank: int = Field(ge=1)
    composite_score: float | None = Field(default=None, ge=0, le=1)
    objective_scores: tuple[CandidateObjectiveScore, ...]

    @field_validator("candidate_identifier", "assessment_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="ranked candidate identifier")

    @field_validator("candidate_sha256", "assessment_sha256")
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("objective_scores")
    @classmethod
    def order_objective_scores(
        cls, value: tuple[CandidateObjectiveScore, ...]
    ) -> tuple[CandidateObjectiveScore, ...]:
        return tuple(sorted(value, key=lambda item: item.objective_identifier))

    @field_validator("composite_score", mode="before")
    @classmethod
    def validate_composite(cls, value: object) -> object:
        if value is None:
            return value
        return _finite(value, label="ranked composite score")


class MultiObjectiveRanking(CanonicalModel):
    """Complete Pareto and deterministic selection evidence for one campaign cohort."""

    ranking_identifier: str
    campaign_identifier: str
    campaign_sha256: str
    ranked_candidates: tuple[RankedDiscoveryCandidate, ...]
    pareto_fronts: tuple[tuple[str, ...], ...]
    decisions: tuple[CandidateSelectionDecision, ...]
    ineligible_candidate_identifiers: tuple[str, ...] = ()
    authorizes_execution: Literal[False] = False

    @field_validator("ranking_identifier", "campaign_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="multi-objective ranking identifier")

    @field_validator("campaign_sha256")
    @classmethod
    def validate_campaign_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("ranked_candidates")
    @classmethod
    def order_ranked_candidates(
        cls, value: tuple[RankedDiscoveryCandidate, ...]
    ) -> tuple[RankedDiscoveryCandidate, ...]:
        return tuple(sorted(value, key=lambda item: item.rank))

    @field_validator("pareto_fronts")
    @classmethod
    def order_pareto_front_members(
        cls, value: tuple[tuple[str, ...], ...]
    ) -> tuple[tuple[str, ...], ...]:
        return tuple(
            _ordered_identifiers(front, label="Pareto candidate identifier")
            for front in value
        )

    @field_validator("decisions")
    @classmethod
    def order_decisions(
        cls, value: tuple[CandidateSelectionDecision, ...]
    ) -> tuple[CandidateSelectionDecision, ...]:
        return tuple(sorted(value, key=lambda item: item.candidate_identifier))

    @field_validator("ineligible_candidate_identifiers")
    @classmethod
    def order_ineligible(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="ineligible candidate identifier")

    @model_validator(mode="after")
    def validate_ranking(self) -> Self:
        ranked_ids = [item.candidate_identifier for item in self.ranked_candidates]
        if len(ranked_ids) != len(set(ranked_ids)):
            raise ValueError("A candidate can appear only once in a ranking.")
        expected_ranks = list(range(1, len(self.ranked_candidates) + 1))
        if [item.rank for item in self.ranked_candidates] != expected_ranks:
            raise ValueError(
                "Ranked discovery candidates require contiguous rank values."
            )
        flattened = [candidate for front in self.pareto_fronts for candidate in front]
        if sorted(flattened) != sorted(ranked_ids):
            raise ValueError(
                "Pareto fronts must contain exactly the ranked candidates."
            )
        ranked_by_id = {
            item.candidate_identifier: item for item in self.ranked_candidates
        }
        for front_index, front in enumerate(self.pareto_fronts):
            if any(
                ranked_by_id[candidate_id].pareto_front != front_index
                for candidate_id in front
            ):
                raise ValueError(
                    "Pareto-front membership must match ranked candidate records."
                )
        decision_ids = [item.candidate_identifier for item in self.decisions]
        all_ids = sorted((*ranked_ids, *self.ineligible_candidate_identifiers))
        if sorted(decision_ids) != all_ids:
            raise ValueError(
                "Selection decisions must cover every ranked or ineligible candidate."
            )
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("Selection decisions must be unique per candidate.")
        ineligible = set(self.ineligible_candidate_identifiers)
        for decision in self.decisions:
            if (
                decision.campaign_identifier != self.campaign_identifier
                or decision.campaign_sha256 != self.campaign_sha256
            ):
                raise ValueError(
                    "Selection decisions must belong to the ranking campaign."
                )
            ranked = ranked_by_id.get(decision.candidate_identifier)
            if ranked is None:
                if (
                    decision.candidate_identifier not in ineligible
                    or decision.outcome is not SelectionOutcome.INELIGIBLE
                ):
                    raise ValueError(
                        "Unranked candidates must be explicitly ineligible."
                    )
                continue
            if (
                decision.candidate_sha256 != ranked.candidate_sha256
                or decision.assessment_identifier != ranked.assessment_identifier
                or decision.assessment_sha256 != ranked.assessment_sha256
                or decision.rank != ranked.rank
                or decision.pareto_front != ranked.pareto_front
            ):
                raise ValueError(
                    "Selection decision rank evidence must match its candidate."
                )
            if decision.outcome is SelectionOutcome.INELIGIBLE:
                raise ValueError("Ranked candidates cannot be marked ineligible.")
        return self
