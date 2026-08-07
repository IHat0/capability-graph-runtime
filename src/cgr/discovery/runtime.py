"""Bounded candidate-type-neutral discovery campaign orchestration."""

from __future__ import annotations

import math
import time
from enum import Enum
from typing import Callable, Literal, Protocol, Self, runtime_checkable

from pydantic import Field, field_validator, model_validator

from cgr.science.canonical import (
    CanonicalModel,
    sha256_fingerprint,
    validate_identifier,
    validate_sha256,
)
from cgr.scientific_verification import (
    ReplanningDisposition,
    ReplanningPolicy,
    ReplanningRecommendation,
    ScientificFailureDiagnoser,
    ScientificFailureDiagnosis,
    ScientificReplanner,
    ScientificVerificationReport,
    VerificationOutcome,
)

from .contracts import (
    CandidateAssessment,
    CandidateConstraintResult,
    CandidateEvaluationResult,
    CandidateEvidenceRecord,
    CandidateGenerationRequest,
    CandidateGenerationResult,
    CandidateLineageGraph,
    CandidateObjectiveScore,
    CandidateValidityResult,
    CandidateVerificationRecord,
    DiscoveryCampaign,
    DiscoveryCandidate,
    DiscoveryCandidateRecord,
    DiscoveryDiagnosisReference,
    DiscoveryReplanningReference,
    MultiObjectiveRanking,
    SelectionOutcome,
    SelectionPolicy,
)
from .integration import diagnosis_reference, replanning_reference, verification_record
from .ranking import rank_candidates
from .registry import (
    CandidateEvaluator,
    CandidateEvaluatorRegistry,
    CandidateGenerator,
    CandidateGeneratorRegistry,
    CandidateValidityChecker,
    CandidateValidityCheckerRegistry,
)


class DiscoveryCampaignRuntimeError(RuntimeError):
    """Base controlled error for discovery-loop orchestration."""


class DiscoveryCampaignConfigurationError(DiscoveryCampaignRuntimeError):
    """Raised when a campaign cannot be executed without guessing policy."""


class DiscoveryCampaignIntegrityError(DiscoveryCampaignRuntimeError):
    """Raised when an external discovery component violates immutable contracts."""


class DiscoveryCampaignDisposition(str, Enum):
    """Current or terminal discovery campaign disposition."""

    RUNNING = "running"
    PAUSED = "paused"
    SUCCESS = "success"
    BUDGET_EXHAUSTED = "budget_exhausted"
    GENERATION_LIMIT_REACHED = "generation_limit_reached"
    CANDIDATE_LIMIT_REACHED = "candidate_limit_reached"
    STAGNATED = "stagnated"
    NO_VALID_CANDIDATES = "no_valid_candidates"
    NO_SELECTABLE_CANDIDATES = "no_selectable_candidates"
    AUTHORIZATION_REQUIRED = "authorization_required"
    EXTERNALLY_STOPPED = "externally_stopped"


TERMINAL_DISCOVERY_DISPOSITIONS = frozenset(
    {
        DiscoveryCampaignDisposition.SUCCESS,
        DiscoveryCampaignDisposition.BUDGET_EXHAUSTED,
        DiscoveryCampaignDisposition.GENERATION_LIMIT_REACHED,
        DiscoveryCampaignDisposition.CANDIDATE_LIMIT_REACHED,
        DiscoveryCampaignDisposition.STAGNATED,
        DiscoveryCampaignDisposition.NO_VALID_CANDIDATES,
        DiscoveryCampaignDisposition.NO_SELECTABLE_CANDIDATES,
        DiscoveryCampaignDisposition.AUTHORIZATION_REQUIRED,
        DiscoveryCampaignDisposition.EXTERNALLY_STOPPED,
    }
)


_RESERVED_RUNTIME_PREFIXES = (
    "authorization.",
    "budget.",
    "validity-capability.",
    "verification-capability.",
    "verification-family.",
)


def _stable_identifier(prefix: str, payload: object) -> str:
    return f"{prefix}-{sha256_fingerprint(payload)[:32]}"


def _ordered_identifiers(
    values: tuple[str, ...],
    *,
    label: str,
) -> tuple[str, ...]:
    normalized = tuple(validate_identifier(value, label=label) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label.capitalize()} values must be unique.")
    return tuple(sorted(normalized))


def _finite_nonnegative(value: float, *, label: str) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError(f"{label} must be a finite non-negative number.")
    return normalized


def _supports(values: tuple[str, ...], value: str, *, label: str) -> bool:
    normalized = validate_identifier(value, label=label)
    supported = _ordered_identifiers(values, label=label)
    return not supported or normalized in supported


def _optional_identifier_tuple(component: object, attribute: str) -> tuple[str, ...]:
    raw = getattr(component, attribute, ())
    if raw is None:
        return ()
    if not isinstance(raw, tuple) or not all(isinstance(item, str) for item in raw):
        raise DiscoveryCampaignConfigurationError(
            f"Optional discovery component attribute '{attribute}' must be a tuple of identifiers."
        )
    return _ordered_identifiers(raw, label=attribute.replace("_", " "))


def _component_matches_objectives(
    component: object, campaign: DiscoveryCampaign
) -> bool:
    supported = _optional_identifier_tuple(component, "supported_objective_identifiers")
    if not supported:
        return True
    campaign_objectives = {item.objective_identifier for item in campaign.objectives}
    return bool(campaign_objectives.intersection(supported))


def _optional_boolean(component: object, attribute: str) -> bool:
    value = getattr(component, attribute, False)
    if not isinstance(value, bool):
        raise DiscoveryCampaignConfigurationError(
            f"Optional discovery component attribute '{attribute}' must be boolean."
        )
    return value


@runtime_checkable
class CandidateScientificVerifier(Protocol):
    """Candidate-scoped adapter into the Phase 6 verification framework."""

    @property
    def verifier_identifier(self) -> str:
        """Stable identifier matching the returned Phase 6 report."""
        ...

    @property
    def supported_candidate_types(self) -> tuple[str, ...]:
        """Supported candidate types; empty means candidate-type neutral."""
        ...

    @property
    def objective_families(self) -> tuple[str, ...]:
        """Phase 6 objective families supported by this adapter."""
        ...

    def verify(
        self,
        candidate: DiscoveryCandidate,
        assessment: CandidateAssessment,
        objective_family: str,
    ) -> ScientificVerificationReport:
        """Return a Phase 6 report bound to the immutable discovery candidate."""
        ...


@runtime_checkable
class ContextualCandidateEvaluator(Protocol):
    """Optional staged evaluator API that consumes evidence from earlier stages."""

    def evaluate_with_context(
        self,
        candidate: DiscoveryCandidate,
        assessment: CandidateAssessment,
    ) -> CandidateEvaluationResult:
        """Evaluate a candidate using the immutable assessment accumulated so far."""
        ...


class CandidateScientificVerifierRegistry:
    """Candidate-type-neutral routing for adapters into Phase 6 verification."""

    def __init__(
        self,
        verifiers: tuple[CandidateScientificVerifier, ...] = (),
    ) -> None:
        self._verifiers: dict[str, CandidateScientificVerifier] = {}
        for verifier in verifiers:
            self.register(verifier)

    def register(self, verifier: CandidateScientificVerifier) -> None:
        if not isinstance(verifier, CandidateScientificVerifier):
            raise TypeError(
                "Scientific verification adapters must implement CandidateScientificVerifier."
            )
        identifier = validate_identifier(
            verifier.verifier_identifier,
            label="candidate scientific verifier identifier",
        )
        _ordered_identifiers(
            verifier.supported_candidate_types,
            label="supported candidate type",
        )
        _ordered_identifiers(
            verifier.objective_families,
            label="scientific verifier objective family",
        )
        if identifier in self._verifiers:
            raise DiscoveryCampaignConfigurationError(
                f"Candidate scientific verifier '{identifier}' is already registered."
            )
        self._verifiers[identifier] = verifier

    def get(self, identifier: str) -> CandidateScientificVerifier:
        normalized = validate_identifier(
            identifier,
            label="candidate scientific verifier identifier",
        )
        try:
            return self._verifiers[normalized]
        except KeyError as exc:
            raise DiscoveryCampaignConfigurationError(
                f"Candidate scientific verifier '{identifier}' is not registered."
            ) from exc

    def resolve(
        self,
        candidate_type: str,
        objective_family: str,
    ) -> tuple[CandidateScientificVerifier, ...]:
        family = validate_identifier(
            objective_family,
            label="scientific verifier objective family",
        )
        matched = []
        for identifier in sorted(self._verifiers):
            verifier = self._verifiers[identifier]
            if not _supports(
                verifier.supported_candidate_types,
                candidate_type,
                label="candidate type",
            ):
                continue
            if not _supports(
                verifier.objective_families,
                family,
                label="scientific verifier objective family",
            ):
                continue
            matched.append(verifier)
        return tuple(matched)

    def identifiers(self) -> tuple[str, ...]:
        return tuple(sorted(self._verifiers))


class DiscoveryResourceUsage(CanonicalModel):
    """Observed generic resource consumption supplied by an external meter."""

    dimension: str
    unit: str
    consumed: float = Field(ge=0)

    @field_validator("dimension", "unit")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="discovery resource identifier")

    @field_validator("consumed", mode="before")
    @classmethod
    def validate_consumed(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Discovery resource consumption must be numeric.")
        return _finite_nonnegative(
            float(value),
            label="Discovery resource consumption",
        )


@runtime_checkable
class DiscoveryResourceMeter(Protocol):
    """External meter for campaign-specific generic resource ceilings."""

    def read(self) -> tuple[DiscoveryResourceUsage, ...]:
        """Return current cumulative resource consumption."""
        ...


class DiscoveryAuthorizationDecision(CanonicalModel):
    """External authorization evidence consulted before protected evaluation."""

    decision_identifier: str
    candidate_identifier: str
    candidate_sha256: str
    operation_identifier: str
    authorized: bool
    rationale: str = Field(min_length=1, max_length=4096)
    evidence_sha256: str | None = None

    @field_validator(
        "decision_identifier",
        "candidate_identifier",
        "operation_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="discovery authorization identifier")

    @field_validator("candidate_sha256", "evidence_sha256")
    @classmethod
    def validate_hashes(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None


@runtime_checkable
class DiscoveryAuthorizationGate(Protocol):
    """External authorization gate; generation and selection never implement it."""

    def check(
        self,
        campaign: DiscoveryCampaign,
        candidate: DiscoveryCandidate,
        operation_identifier: str,
    ) -> DiscoveryAuthorizationDecision:
        """Return explicit authorization evidence for one protected operation."""
        ...


class DiscoveryGenerationContext(CanonicalModel):
    """Why and from which immutable parents a new generation may be proposed."""

    context_identifier: str
    parent_candidate_identifiers: tuple[str, ...] = ()
    diagnosis_references: tuple[DiscoveryDiagnosisReference, ...] = ()
    replanning_references: tuple[DiscoveryReplanningReference, ...] = ()
    rationale: str = Field(min_length=1, max_length=4096)
    authorizes_execution: Literal[False] = False

    @field_validator("context_identifier")
    @classmethod
    def validate_identifier_field(cls, value: str) -> str:
        return validate_identifier(
            value, label="discovery generation context identifier"
        )

    @field_validator("parent_candidate_identifiers")
    @classmethod
    def order_parents(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="generation context parent")

    @field_validator("diagnosis_references")
    @classmethod
    def order_diagnoses(
        cls,
        value: tuple[DiscoveryDiagnosisReference, ...],
    ) -> tuple[DiscoveryDiagnosisReference, ...]:
        identifiers = [item.diagnosis_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Generation-context diagnoses must be unique.")
        return tuple(sorted(value, key=lambda item: item.diagnosis_identifier))

    @field_validator("replanning_references")
    @classmethod
    def order_replans(
        cls,
        value: tuple[DiscoveryReplanningReference, ...],
    ) -> tuple[DiscoveryReplanningReference, ...]:
        identifiers = [item.recommendation_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Generation-context replanning references must be unique.")
        return tuple(sorted(value, key=lambda item: item.recommendation_identifier))

    @model_validator(mode="after")
    def validate_replanning_context(self) -> Self:
        diagnosis_ids = {
            item.diagnosis_identifier for item in self.diagnosis_references
        }
        if any(
            item.diagnosis_identifier not in diagnosis_ids
            for item in self.replanning_references
        ):
            raise ValueError(
                "Generation-context replanning references require their diagnoses."
            )
        if (self.diagnosis_references or self.replanning_references) and not (
            self.parent_candidate_identifiers
        ):
            raise ValueError(
                "Scientific failure context requires at least one parent candidate."
            )
        return self


class DiscoveryCampaignUsage(CanonicalModel):
    """Monotonic bounded counters for one discovery campaign."""

    generations_completed: int = Field(default=0, ge=0)
    candidates_generated: int = Field(default=0, ge=0)
    validity_checks: int = Field(default=0, ge=0)
    evaluations: int = Field(default=0, ge=0)
    expensive_evaluations: int = Field(default=0, ge=0)
    elapsed_wall_time_seconds: float = Field(default=0.0, ge=0)
    resources: tuple[DiscoveryResourceUsage, ...] = ()

    @field_validator("elapsed_wall_time_seconds", mode="before")
    @classmethod
    def validate_elapsed(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Discovery elapsed wall time must be numeric.")
        return _finite_nonnegative(float(value), label="Discovery elapsed wall time")

    @field_validator("resources")
    @classmethod
    def order_resources(
        cls,
        value: tuple[DiscoveryResourceUsage, ...],
    ) -> tuple[DiscoveryResourceUsage, ...]:
        identities = [(item.dimension, item.unit) for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError(
                "Discovery resource usage must be unique per dimension/unit."
            )
        return tuple(sorted(value, key=lambda item: (item.dimension, item.unit)))


class DiscoveryVerificationTrace(CanonicalModel):
    """Full Phase 6 verification/diagnosis evidence retained in campaign history."""

    candidate_identifier: str
    candidate_sha256: str
    report: ScientificVerificationReport
    diagnosis: ScientificFailureDiagnosis | None = None
    recommendation: ReplanningRecommendation | None = None

    @field_validator("candidate_identifier")
    @classmethod
    def validate_candidate_identifier(cls, value: str) -> str:
        return validate_identifier(
            value, label="verification trace candidate identifier"
        )

    @field_validator("candidate_sha256")
    @classmethod
    def validate_candidate_sha(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def validate_trace(self) -> Self:
        failed = self.report.overall_outcome is VerificationOutcome.FAILED
        if failed != (self.diagnosis is not None):
            raise ValueError(
                "Failed Phase 6 reports require exactly one scientific diagnosis."
            )
        if (self.diagnosis is None) != (self.recommendation is None):
            raise ValueError(
                "Verification diagnosis and replanning recommendation appear together."
            )
        if self.diagnosis is not None:
            if self.diagnosis.report_identifier != self.report.report_identifier:
                raise ValueError(
                    "Verification diagnosis does not belong to its report."
                )
            if (
                self.recommendation is not None
                and self.recommendation.diagnosis_identifier
                != self.diagnosis.diagnosis_identifier
            ):
                raise ValueError(
                    "Replanning recommendation does not belong to its diagnosis."
                )
        return self


class DiscoveryGenerationRecord(CanonicalModel):
    """Immutable evidence for one complete or budget-interrupted generation."""

    generation: int = Field(ge=0)
    generation_contexts: tuple[DiscoveryGenerationContext, ...]
    generation_requests: tuple[CandidateGenerationRequest, ...] = ()
    generation_results: tuple[CandidateGenerationResult, ...] = ()
    candidate_records: tuple[DiscoveryCandidateRecord, ...] = ()
    validity_results: tuple[CandidateValidityResult, ...] = ()
    evaluation_results: tuple[CandidateEvaluationResult, ...] = ()
    verification_traces: tuple[DiscoveryVerificationTrace, ...] = ()
    authorization_decisions: tuple[DiscoveryAuthorizationDecision, ...] = ()
    ranking: MultiObjectiveRanking | None = None
    valid_candidate_identifiers: tuple[str, ...] = ()
    selected_candidate_identifiers: tuple[str, ...] = ()
    budget_interrupted: bool = False
    budget_reason: str | None = Field(default=None, max_length=4096)

    @field_validator(
        "valid_candidate_identifiers",
        "selected_candidate_identifiers",
    )
    @classmethod
    def order_candidate_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="generation candidate identifier")

    @field_validator("candidate_records")
    @classmethod
    def order_candidate_records(
        cls,
        value: tuple[DiscoveryCandidateRecord, ...],
    ) -> tuple[DiscoveryCandidateRecord, ...]:
        return tuple(
            sorted(value, key=lambda item: item.candidate.candidate_identifier)
        )

    @field_validator("validity_results")
    @classmethod
    def order_validity_results(
        cls,
        value: tuple[CandidateValidityResult, ...],
    ) -> tuple[CandidateValidityResult, ...]:
        return tuple(
            sorted(
                value,
                key=lambda item: (item.candidate_identifier, item.checker_identifier),
            )
        )

    @field_validator("evaluation_results")
    @classmethod
    def order_evaluation_results(
        cls,
        value: tuple[CandidateEvaluationResult, ...],
    ) -> tuple[CandidateEvaluationResult, ...]:
        return tuple(
            sorted(
                value,
                key=lambda item: (
                    item.candidate_identifier,
                    item.stage_order,
                    item.stage_identifier,
                    item.evaluator_identifier,
                ),
            )
        )

    @field_validator("verification_traces")
    @classmethod
    def order_verification_traces(
        cls,
        value: tuple[DiscoveryVerificationTrace, ...],
    ) -> tuple[DiscoveryVerificationTrace, ...]:
        return tuple(
            sorted(
                value,
                key=lambda item: (
                    item.candidate_identifier,
                    item.report.objective_family,
                    item.report.verifier_identifier,
                ),
            )
        )

    @field_validator("authorization_decisions")
    @classmethod
    def order_authorizations(
        cls,
        value: tuple[DiscoveryAuthorizationDecision, ...],
    ) -> tuple[DiscoveryAuthorizationDecision, ...]:
        identities = [item.decision_identifier for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError(
                "Discovery authorization decisions must be uniquely identified."
            )
        return tuple(
            sorted(
                value,
                key=lambda item: (
                    item.candidate_identifier,
                    item.operation_identifier,
                    item.decision_identifier,
                ),
            )
        )

    @model_validator(mode="after")
    def validate_generation_record(self) -> Self:
        candidate_map = {
            item.candidate.candidate_identifier: item.candidate
            for item in self.candidate_records
        }
        if len(candidate_map) != len(self.candidate_records):
            raise ValueError("Generation candidate records must be unique.")
        if any(
            candidate.generation != self.generation
            for candidate in candidate_map.values()
        ):
            raise ValueError(
                "Every candidate record must belong to the generation record."
            )
        for request in self.generation_requests:
            if request.generation != self.generation:
                raise ValueError(
                    "Generation requests must belong to their generation record."
                )
        request_hashes = {item.fingerprint for item in self.generation_requests}
        for result in self.generation_results:
            if result.request_sha256 not in request_hashes:
                raise ValueError(
                    "Generation result does not belong to a recorded request."
                )
            for candidate in result.candidates:
                recorded = candidate_map.get(candidate.candidate_identifier)
                if recorded is None or recorded.fingerprint != candidate.fingerprint:
                    raise ValueError(
                        "Generated candidate result must match immutable campaign history."
                    )
        for result in (*self.validity_results, *self.evaluation_results):
            candidate = candidate_map.get(result.candidate_identifier)
            if candidate is None or result.candidate_sha256 != candidate.fingerprint:
                raise ValueError("Generation evidence belongs to an unknown candidate.")
        for trace in self.verification_traces:
            candidate = candidate_map.get(trace.candidate_identifier)
            if candidate is None or trace.candidate_sha256 != candidate.fingerprint:
                raise ValueError("Verification trace belongs to an unknown candidate.")
        for decision in self.authorization_decisions:
            candidate = candidate_map.get(decision.candidate_identifier)
            if candidate is None or decision.candidate_sha256 != candidate.fingerprint:
                raise ValueError(
                    "Authorization evidence belongs to an unknown candidate."
                )
        if not set(self.valid_candidate_identifiers).issubset(candidate_map):
            raise ValueError(
                "Valid candidate identifiers must exist in the generation."
            )
        if not set(self.selected_candidate_identifiers).issubset(candidate_map):
            raise ValueError(
                "Selected candidate identifiers must exist in the generation."
            )
        if self.budget_interrupted != (self.budget_reason is not None):
            raise ValueError(
                "Budget interruption and its rationale must appear together."
            )
        if self.ranking is not None:
            decision_ids = {
                item.candidate_identifier for item in self.ranking.decisions
            }
            if decision_ids != set(candidate_map):
                raise ValueError(
                    "Generation ranking must cover every candidate record."
                )
            selected = {
                item.candidate_identifier
                for item in self.ranking.decisions
                if item.outcome is SelectionOutcome.SELECTED
            }
            if selected != set(self.selected_candidate_identifiers):
                raise ValueError(
                    "Selected candidate identifiers must match ranking evidence."
                )
        return self


class DiscoveryCampaignState(CanonicalModel):
    """Deterministic immutable checkpointable state for a discovery campaign."""

    campaign_identifier: str
    campaign_sha256: str
    lineage: CandidateLineageGraph
    generations: tuple[DiscoveryGenerationRecord, ...] = ()
    usage: DiscoveryCampaignUsage = Field(default_factory=DiscoveryCampaignUsage)
    next_generation: int = Field(default=0, ge=0)
    next_generation_contexts: tuple[DiscoveryGenerationContext, ...] = ()
    best_composite_score: float | None = Field(default=None, ge=0, le=1)
    stagnant_generations: int = Field(default=0, ge=0)
    consecutive_invalid_generations: int = Field(default=0, ge=0)
    seen_pareto_candidate_identifiers: tuple[str, ...] = ()
    disposition: DiscoveryCampaignDisposition = DiscoveryCampaignDisposition.RUNNING
    rationale: str = Field(
        default="Campaign initialized.", min_length=1, max_length=4096
    )
    authorizes_execution: Literal[False] = False

    @field_validator("campaign_identifier")
    @classmethod
    def validate_campaign_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="discovery campaign identifier")

    @field_validator("campaign_sha256")
    @classmethod
    def validate_campaign_sha(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("seen_pareto_candidate_identifiers")
    @classmethod
    def order_seen_pareto(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="seen Pareto candidate identifier")

    @field_validator("best_composite_score", mode="before")
    @classmethod
    def validate_best_score(cls, value: object) -> object:
        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Best composite score must be numeric.")
        normalized = float(value)
        if not math.isfinite(normalized):
            raise ValueError("Best composite score must be finite.")
        return normalized

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        expected_generations = list(range(len(self.generations)))
        observed_generations = [item.generation for item in self.generations]
        if observed_generations != expected_generations:
            raise ValueError(
                "Discovery generation history must be contiguous from generation zero."
            )
        if self.next_generation != len(self.generations):
            raise ValueError(
                "Next generation must follow immutable generation history."
            )

        candidate_map: dict[str, DiscoveryCandidate] = {}
        expected_lineage = CandidateLineageGraph()
        for generation in self.generations:
            prior = dict(candidate_map)
            for record in generation.candidate_records:
                candidate = record.candidate
                if candidate.candidate_identifier in candidate_map:
                    raise ValueError(
                        "One candidate identifier cannot be reused across campaign generations."
                    )
                if candidate.generation > 0:
                    parents = tuple(
                        prior[identifier]
                        for identifier in candidate.parent_candidate_identifiers
                        if identifier in prior
                    )
                    if len(parents) != len(candidate.parent_candidate_identifiers):
                        raise ValueError(
                            "Candidate lineage references an unavailable prior candidate."
                        )
                    if any(
                        parent.generation != candidate.generation - 1
                        for parent in parents
                    ):
                        raise ValueError(
                            "Discovery candidate parents must come from the prior generation."
                        )
                    expected_lineage = expected_lineage.add_candidate(
                        candidate, parents
                    )
                candidate_map[candidate.candidate_identifier] = candidate

        if expected_lineage.fingerprint != self.lineage.fingerprint:
            raise ValueError(
                "Campaign lineage must exactly match immutable candidate history."
            )

        if self.next_generation_contexts:
            for context in self.next_generation_contexts:
                for identifier in context.parent_candidate_identifiers:
                    candidate = candidate_map.get(identifier)
                    if candidate is None:
                        raise ValueError(
                            "Next-generation context references an unknown candidate."
                        )
                    if candidate.generation != self.next_generation - 1:
                        raise ValueError(
                            "Next-generation parents must come from the latest generation."
                        )

        if self.usage.generations_completed != len(self.generations):
            raise ValueError(
                "Generation usage counter must equal immutable generation history."
            )
        if self.usage.candidates_generated != len(candidate_map):
            raise ValueError(
                "Candidate usage counter must equal immutable candidate history."
            )
        expected_evaluations = sum(
            len(item.evaluation_results) for item in self.generations
        )
        if self.usage.evaluations != expected_evaluations:
            raise ValueError(
                "Evaluation usage counter must equal recorded evaluations."
            )
        expected_expensive = sum(
            result.expensive
            for generation in self.generations
            for result in generation.evaluation_results
        )
        if self.usage.expensive_evaluations != expected_expensive:
            raise ValueError(
                "Expensive-evaluation counter must equal recorded expensive evaluations."
            )
        expected_validity = sum(
            result.checker_identifier != "discovery.runtime"
            for generation in self.generations
            for result in generation.validity_results
        )
        if self.usage.validity_checks != expected_validity:
            raise ValueError(
                "Validity-check counter must equal recorded checker results."
            )
        return self

    @property
    def terminal(self) -> bool:
        return self.disposition in TERMINAL_DISCOVERY_DISPOSITIONS


class DiscoveryCampaignCheckpoint(CanonicalModel):
    """Content-addressed checkpoint suitable for deterministic resume."""

    checkpoint_identifier: str
    campaign_identifier: str
    campaign_sha256: str
    state_sha256: str
    state: DiscoveryCampaignState
    authorizes_execution: Literal[False] = False

    @field_validator("checkpoint_identifier", "campaign_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="discovery checkpoint identifier")

    @field_validator("campaign_sha256", "state_sha256")
    @classmethod
    def validate_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def validate_checkpoint(self) -> Self:
        if (
            self.state.campaign_identifier != self.campaign_identifier
            or self.state.campaign_sha256 != self.campaign_sha256
        ):
            raise ValueError("Checkpoint state does not belong to its campaign.")
        if self.state.fingerprint != self.state_sha256:
            raise ValueError("Checkpoint state fingerprint does not match its content.")
        return self


class DiscoveryCampaignRun(CanonicalModel):
    """Result of one bounded run or resume call."""

    campaign_identifier: str
    campaign_sha256: str
    state: DiscoveryCampaignState
    checkpoint: DiscoveryCampaignCheckpoint
    completed: bool
    authorizes_execution: Literal[False] = False

    @field_validator("campaign_identifier")
    @classmethod
    def validate_campaign_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="discovery campaign identifier")

    @field_validator("campaign_sha256")
    @classmethod
    def validate_campaign_sha(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def validate_run(self) -> Self:
        if (
            self.state.campaign_identifier != self.campaign_identifier
            or self.state.campaign_sha256 != self.campaign_sha256
        ):
            raise ValueError("Discovery run state belongs to a different campaign.")
        if self.checkpoint.state_sha256 != self.state.fingerprint:
            raise ValueError("Discovery run checkpoint does not match final state.")
        if self.completed != self.state.terminal:
            raise ValueError(
                "Discovery run completion must match terminal campaign disposition."
            )
        return self


class DiscoveryCampaignRuntime:
    """Run bounded evidence-driven discovery without privileging candidate domains."""

    def __init__(
        self,
        *,
        generators: CandidateGeneratorRegistry,
        validity_checkers: CandidateValidityCheckerRegistry,
        evaluators: CandidateEvaluatorRegistry,
        scientific_verifiers: CandidateScientificVerifierRegistry | None = None,
        selection_policy: SelectionPolicy | None = None,
        diagnoser: ScientificFailureDiagnoser | None = None,
        replanner: ScientificReplanner | None = None,
        replanning_policy: ReplanningPolicy | None = None,
        authorization_gate: DiscoveryAuthorizationGate | None = None,
        resource_meter: DiscoveryResourceMeter | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.generators = generators
        self.validity_checkers = validity_checkers
        self.evaluators = evaluators
        self.scientific_verifiers = (
            scientific_verifiers or CandidateScientificVerifierRegistry()
        )
        self.selection_policy = selection_policy or SelectionPolicy()
        self.diagnoser = diagnoser or ScientificFailureDiagnoser()
        self.replanner = replanner or ScientificReplanner()
        self.replanning_policy = replanning_policy or ReplanningPolicy()
        self.authorization_gate = authorization_gate
        self.resource_meter = resource_meter
        self.clock = clock or time.monotonic

    def initialize(self, campaign: DiscoveryCampaign) -> DiscoveryCampaignState:
        self._validate_campaign_configuration(campaign)
        initial_context = DiscoveryGenerationContext(
            context_identifier=_stable_identifier(
                "discovery-context",
                {"campaign": campaign.fingerprint, "generation": 0},
            ),
            rationale="Initial candidate generation for the immutable campaign objective.",
        )
        resources = self._resource_snapshot(campaign)
        return DiscoveryCampaignState(
            campaign_identifier=campaign.campaign_identifier,
            campaign_sha256=campaign.fingerprint,
            lineage=CandidateLineageGraph(),
            next_generation_contexts=(initial_context,),
            usage=DiscoveryCampaignUsage(resources=resources),
        )

    def checkpoint(
        self,
        campaign: DiscoveryCampaign,
        state: DiscoveryCampaignState,
    ) -> DiscoveryCampaignCheckpoint:
        self._validate_state_campaign(campaign, state)
        identifier = _stable_identifier(
            "discovery-checkpoint",
            {"campaign": campaign.fingerprint, "state": state.fingerprint},
        )
        return DiscoveryCampaignCheckpoint(
            checkpoint_identifier=identifier,
            campaign_identifier=campaign.campaign_identifier,
            campaign_sha256=campaign.fingerprint,
            state_sha256=state.fingerprint,
            state=state,
        )

    def run(
        self,
        campaign: DiscoveryCampaign,
        *,
        initial_candidates: tuple[DiscoveryCandidate, ...] = (),
        checkpoint: DiscoveryCampaignCheckpoint | None = None,
        maximum_cycles: int | None = None,
    ) -> DiscoveryCampaignRun:
        self._validate_campaign_configuration(campaign)
        if checkpoint is not None and initial_candidates:
            raise DiscoveryCampaignConfigurationError(
                "Resumed campaigns cannot replace checkpoint history with seed candidates."
            )
        if maximum_cycles is not None and maximum_cycles < 1:
            raise DiscoveryCampaignConfigurationError(
                "Discovery maximum_cycles must be at least one."
            )

        if checkpoint is None:
            state = self.initialize(campaign)
        else:
            self._validate_checkpoint_campaign(campaign, checkpoint)
            state = checkpoint.state
            if state.terminal:
                return self._run_result(campaign, state)
            if state.disposition is DiscoveryCampaignDisposition.PAUSED:
                state = self._replace_state(
                    state,
                    disposition=DiscoveryCampaignDisposition.RUNNING,
                    rationale="Campaign resumed from an immutable checkpoint.",
                )

        cycles = (
            maximum_cycles
            if maximum_cycles is not None
            else max(1, campaign.budget.max_generations - state.next_generation)
        )
        segment_start = self.clock()
        carried_wall = state.usage.elapsed_wall_time_seconds

        for _ in range(cycles):
            if state.terminal:
                break
            state = self._advance(
                campaign,
                state,
                initial_candidates=(
                    initial_candidates if state.next_generation == 0 else ()
                ),
                segment_start=segment_start,
                carried_wall=carried_wall,
            )

        if (
            not state.terminal
            and state.disposition is DiscoveryCampaignDisposition.RUNNING
        ):
            state = self._replace_state(
                state,
                disposition=DiscoveryCampaignDisposition.PAUSED,
                rationale="Bounded run cycle limit reached; checkpoint is resumable.",
            )

        state = self._replace_usage_wall_time(
            state,
            carried_wall + max(0.0, self.clock() - segment_start),
            campaign,
        )
        return self._run_result(campaign, state)

    def resume(
        self,
        campaign: DiscoveryCampaign,
        checkpoint: DiscoveryCampaignCheckpoint,
        *,
        maximum_cycles: int | None = None,
    ) -> DiscoveryCampaignRun:
        return self.run(
            campaign,
            checkpoint=checkpoint,
            maximum_cycles=maximum_cycles,
        )

    def stop(
        self,
        campaign: DiscoveryCampaign,
        checkpoint: DiscoveryCampaignCheckpoint,
        *,
        rationale: str,
    ) -> DiscoveryCampaignRun:
        self._validate_checkpoint_campaign(campaign, checkpoint)
        normalized = rationale.strip()
        if not normalized:
            raise DiscoveryCampaignConfigurationError(
                "External campaign stop requires a rationale."
            )
        if checkpoint.state.terminal:
            return self._run_result(campaign, checkpoint.state)
        state = self._replace_state(
            checkpoint.state,
            disposition=DiscoveryCampaignDisposition.EXTERNALLY_STOPPED,
            rationale=normalized,
        )
        return self._run_result(campaign, state)

    def _advance(
        self,
        campaign: DiscoveryCampaign,
        state: DiscoveryCampaignState,
        *,
        initial_candidates: tuple[DiscoveryCandidate, ...],
        segment_start: float,
        carried_wall: float,
    ) -> DiscoveryCampaignState:
        preterminal = self._pre_generation_terminal(
            campaign,
            state,
            segment_start=segment_start,
            carried_wall=carried_wall,
        )
        if preterminal is not None:
            disposition, rationale = preterminal
            return self._replace_state(
                state, disposition=disposition, rationale=rationale
            )

        generation = state.next_generation
        prior_records = self._candidate_record_map(state)
        prior_candidates = {
            identifier: record.candidate for identifier, record in prior_records.items()
        }
        contexts = state.next_generation_contexts
        if generation == 0 and not contexts:
            raise DiscoveryCampaignIntegrityError(
                "Initial discovery generation is missing its generation context."
            )
        if generation > 0 and not contexts:
            return self._replace_state(
                state,
                disposition=DiscoveryCampaignDisposition.NO_SELECTABLE_CANDIDATES,
                rationale="No scientifically justified parents remain for generation.",
            )

        candidate_slots = min(
            campaign.budget.max_candidates_per_generation,
            campaign.budget.max_candidates_total - state.usage.candidates_generated,
        )
        if candidate_slots <= 0:
            return self._replace_state(
                state,
                disposition=DiscoveryCampaignDisposition.CANDIDATE_LIMIT_REACHED,
                rationale="Campaign candidate-count ceiling has been reached.",
            )

        requests: list[CandidateGenerationRequest] = []
        generation_results: list[CandidateGenerationResult] = []
        candidate_context: dict[
            str,
            tuple[CandidateGenerationRequest, CandidateGenerator],
        ] = {}

        if initial_candidates:
            candidates = list(
                self._validate_initial_candidates(
                    campaign, initial_candidates, candidate_slots
                )
            )
        else:
            candidates = []
            generators = self._resolve_generators(campaign)
            for context in contexts:
                for generator in generators:
                    remaining = candidate_slots - len(candidates)
                    if remaining <= 0:
                        break
                    request = self._generation_request(
                        campaign,
                        generation,
                        context,
                        prior_records,
                        generator,
                        remaining,
                    )
                    result = generator.propose(request)
                    self._validate_generation_result(
                        campaign, request, generator, result
                    )
                    requests.append(request)
                    generation_results.append(result)
                    for candidate in result.candidates:
                        if candidate.candidate_identifier in candidate_context:
                            raise DiscoveryCampaignIntegrityError(
                                "Generators reused a candidate identifier in one generation."
                            )
                        candidate_context[candidate.candidate_identifier] = (
                            request,
                            generator,
                        )
                        candidates.append(candidate)
                    if len(candidates) >= candidate_slots:
                        break
                if len(candidates) >= candidate_slots:
                    break

        candidate_ids = [item.candidate_identifier for item in candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise DiscoveryCampaignIntegrityError(
                "One candidate identifier was generated more than once."
            )
        if any(identifier in prior_candidates for identifier in candidate_ids):
            raise DiscoveryCampaignIntegrityError(
                "Candidate identifiers are immutable and cannot be reused across generations."
            )

        lineage = state.lineage
        for candidate in sorted(candidates, key=lambda item: item.candidate_identifier):
            request_and_generator = candidate_context.get(
                candidate.candidate_identifier
            )
            self._validate_candidate_proposal(
                campaign,
                candidate,
                generation,
                request_and_generator,
                prior_candidates,
            )
            if candidate.generation > 0:
                parents = tuple(
                    prior_candidates[identifier]
                    for identifier in candidate.parent_candidate_identifiers
                )
                lineage = lineage.add_candidate(candidate, parents)

        usage_values = {
            "candidates_generated": state.usage.candidates_generated + len(candidates),
            "validity_checks": state.usage.validity_checks,
            "evaluations": state.usage.evaluations,
            "expensive_evaluations": state.usage.expensive_evaluations,
        }
        budget_interrupted = False
        budget_reason: str | None = None
        authorization_required = False

        validity_results: list[CandidateValidityResult] = []
        evaluation_results: list[CandidateEvaluationResult] = []
        verification_traces: list[DiscoveryVerificationTrace] = []
        authorization_decisions: list[DiscoveryAuthorizationDecision] = []
        provisional_records: list[DiscoveryCandidateRecord] = []
        valid_ids: list[str] = []

        for candidate in sorted(candidates, key=lambda item: item.candidate_identifier):
            evidence_by_id: dict[str, CandidateEvidenceRecord] = {}
            constraints_by_id: dict[str, CandidateConstraintResult] = {}
            scores_by_objective: dict[str, CandidateObjectiveScore] = {}
            verification_records: list[CandidateVerificationRecord] = []

            candidate_authorized = True
            if self._candidate_requires_fresh_authorization(candidate):
                decision = self._authorization_decision(
                    campaign,
                    candidate,
                    "discovery.evaluate-candidate",
                )
                if decision is not None:
                    authorization_decisions.append(decision)
                if decision is None or not decision.authorized:
                    candidate_authorized = False
                    authorization_required = True
                    self._merge_constraint(
                        constraints_by_id,
                        self._runtime_constraint(
                            candidate,
                            "authorization.required",
                            "Fresh external authorization is required before executing "
                            "a Phase 6 diagnosis-guided descendant.",
                        ),
                    )

            checkers = self._resolved_validity_checkers(candidate, campaign)
            candidate_valid = candidate_authorized
            if candidate_authorized and not checkers:
                candidate_valid = False
                failure = self._runtime_constraint(
                    candidate,
                    "validity-capability.available",
                    "No registered validity capability supports this candidate type.",
                )
                self._merge_constraint(constraints_by_id, failure)
                validity_results.append(
                    CandidateValidityResult(
                        result_identifier=_stable_identifier(
                            "validity-routing",
                            {
                                "candidate": candidate.fingerprint,
                                "reason": "missing-capability",
                            },
                        ),
                        checker_identifier="discovery.runtime",
                        candidate_identifier=candidate.candidate_identifier,
                        candidate_sha256=candidate.fingerprint,
                        valid=False,
                        findings=(failure,),
                    )
                )
            elif candidate_authorized:
                for checker in checkers:
                    if (
                        usage_values["validity_checks"]
                        >= campaign.budget.max_validity_checks
                    ):
                        budget_interrupted = True
                        budget_reason = "Campaign validity-check budget is exhausted."
                        candidate_valid = False
                        self._merge_constraint(
                            constraints_by_id,
                            self._runtime_constraint(
                                candidate,
                                "budget.validity-checks",
                                budget_reason,
                            ),
                        )
                        break
                    if _optional_boolean(checker, "requires_authorization"):
                        decision = self._authorization_decision(
                            campaign,
                            candidate,
                            checker.checker_identifier,
                        )
                        if decision is not None:
                            authorization_decisions.append(decision)
                        if decision is None or not decision.authorized:
                            candidate_valid = False
                            authorization_required = True
                            self._merge_constraint(
                                constraints_by_id,
                                self._runtime_constraint(
                                    candidate,
                                    "authorization.required",
                                    "Fresh external authorization is required before "
                                    "the protected validity check.",
                                ),
                            )
                            break
                    result = checker.check(candidate)
                    self._validate_validity_result(candidate, checker, result)
                    usage_values["validity_checks"] += 1
                    validity_results.append(result)
                    for finding in result.findings:
                        self._merge_constraint(constraints_by_id, finding)
                    if not result.valid:
                        candidate_valid = False
                        break

            if candidate_valid:
                valid_ids.append(candidate.candidate_identifier)

            if candidate_valid and not budget_interrupted:
                evaluators = self._resolved_evaluators(candidate, campaign)
                for evaluator in evaluators:
                    if usage_values["evaluations"] >= campaign.budget.max_evaluations:
                        budget_interrupted = True
                        budget_reason = "Campaign evaluation-count budget is exhausted."
                        self._merge_constraint(
                            constraints_by_id,
                            self._runtime_constraint(
                                candidate,
                                "budget.evaluations",
                                budget_reason,
                            ),
                        )
                        break

                    declared_expensive = _optional_boolean(evaluator, "expensive")
                    if (
                        declared_expensive
                        and usage_values["expensive_evaluations"]
                        >= campaign.budget.max_expensive_evaluations
                    ):
                        budget_interrupted = True
                        budget_reason = (
                            "Campaign expensive-evaluation budget is exhausted."
                        )
                        self._merge_constraint(
                            constraints_by_id,
                            self._runtime_constraint(
                                candidate,
                                "budget.expensive-evaluations",
                                budget_reason,
                            ),
                        )
                        break

                    if _optional_boolean(evaluator, "requires_authorization"):
                        decision = self._authorization_decision(
                            campaign,
                            candidate,
                            evaluator.evaluator_identifier,
                        )
                        if decision is not None:
                            authorization_decisions.append(decision)
                        if decision is None or not decision.authorized:
                            authorization_required = True
                            self._merge_constraint(
                                constraints_by_id,
                                self._runtime_constraint(
                                    candidate,
                                    "authorization.required",
                                    "Fresh external authorization is required before "
                                    "the protected candidate evaluator.",
                                ),
                            )
                            break

                    current_assessment = self._assessment(
                        candidate,
                        evidence_by_id,
                        constraints_by_id,
                        scores_by_objective,
                        (),
                    )
                    if isinstance(evaluator, ContextualCandidateEvaluator):
                        result = evaluator.evaluate_with_context(
                            candidate,
                            current_assessment,
                        )
                    else:
                        result = evaluator.evaluate(candidate)
                    self._validate_evaluation_result(
                        candidate,
                        evaluator,
                        result,
                        declared_expensive=declared_expensive,
                    )
                    usage_values["evaluations"] += 1
                    usage_values["expensive_evaluations"] += int(result.expensive)
                    evaluation_results.append(result)
                    for evidence in result.evidence_records:
                        previous = evidence_by_id.get(evidence.evidence_identifier)
                        if previous is not None and previous != evidence:
                            raise DiscoveryCampaignIntegrityError(
                                "Evidence identifiers cannot change meaning across stages."
                            )
                        evidence_by_id[evidence.evidence_identifier] = evidence
                    for constraint in result.constraint_results:
                        self._merge_constraint(constraints_by_id, constraint)
                    for score in result.objective_scores:
                        if score.objective_identifier not in {
                            item.objective_identifier for item in campaign.objectives
                        }:
                            raise DiscoveryCampaignIntegrityError(
                                "Evaluator produced a score outside the campaign objective contract."
                            )
                        scores_by_objective[score.objective_identifier] = score
                    if any(
                        not item.passed and item.hard
                        for item in result.constraint_results
                    ):
                        break

            preliminary = self._assessment(
                candidate,
                evidence_by_id,
                constraints_by_id,
                scores_by_objective,
                (),
            )

            if (
                not preliminary.has_hard_constraint_failure
                and not budget_interrupted
                and not authorization_required
            ):
                families = self._required_verifier_families(campaign)
                if self.selection_policy.require_scientific_quality and not families:
                    self._merge_constraint(
                        constraints_by_id,
                        self._runtime_constraint(
                            candidate,
                            "verification-family.required",
                            "Scientific-quality selection requires at least one declared "
                            "Phase 6 verifier family.",
                        ),
                    )
                for family in families:
                    verifiers = self.scientific_verifiers.resolve(
                        candidate.candidate_type,
                        family,
                    )
                    if not verifiers:
                        self._merge_constraint(
                            constraints_by_id,
                            self._runtime_constraint(
                                candidate,
                                f"verification-capability.{family}",
                                "No registered Phase 6 verification adapter supports "
                                f"required family '{family}'.",
                            ),
                        )
                        continue
                    for verifier in verifiers:
                        if _optional_boolean(verifier, "requires_authorization"):
                            decision = self._authorization_decision(
                                campaign,
                                candidate,
                                verifier.verifier_identifier,
                            )
                            if decision is not None:
                                authorization_decisions.append(decision)
                            if decision is None or not decision.authorized:
                                authorization_required = True
                                self._merge_constraint(
                                    constraints_by_id,
                                    self._runtime_constraint(
                                        candidate,
                                        "authorization.required",
                                        "Fresh external authorization is required before "
                                        "the protected Phase 6 verification adapter.",
                                    ),
                                )
                                break
                        report = verifier.verify(candidate, preliminary, family)
                        self._validate_verification_report(
                            family,
                            verifier,
                            report,
                        )
                        verification_records.append(
                            verification_record(candidate, report)
                        )
                        diagnosis: ScientificFailureDiagnosis | None = None
                        recommendation: ReplanningRecommendation | None = None
                        if report.overall_outcome is VerificationOutcome.FAILED:
                            diagnosis = self.diagnoser.diagnose(report)
                            recommendation = self.replanner.recommend(
                                diagnosis,
                                policy=self.replanning_policy,
                            )
                            if (
                                recommendation.disposition
                                is ReplanningDisposition.REQUIRE_AUTHORIZATION
                            ):
                                authorization_required = True
                        verification_traces.append(
                            DiscoveryVerificationTrace(
                                candidate_identifier=candidate.candidate_identifier,
                                candidate_sha256=candidate.fingerprint,
                                report=report,
                                diagnosis=diagnosis,
                                recommendation=recommendation,
                            )
                        )
                    if authorization_required:
                        break

            final_assessment = self._assessment(
                candidate,
                evidence_by_id,
                constraints_by_id,
                scores_by_objective,
                tuple(verification_records),
            )
            provisional_records.append(
                DiscoveryCandidateRecord(
                    candidate=candidate, assessment=final_assessment
                )
            )

        if budget_interrupted:
            processed_ids = {
                item.candidate.candidate_identifier for item in provisional_records
            }
            for candidate in sorted(
                (
                    item
                    for item in candidates
                    if item.candidate_identifier not in processed_ids
                ),
                key=lambda item: item.candidate_identifier,
            ):
                constraint = self._runtime_constraint(
                    candidate,
                    "budget.interrupted",
                    budget_reason or "Campaign budget interrupted this generation.",
                )
                assessment = self._assessment(
                    candidate,
                    {},
                    {constraint.constraint_identifier: constraint},
                    {},
                    (),
                )
                provisional_records.append(
                    DiscoveryCandidateRecord(candidate=candidate, assessment=assessment)
                )

        assessments = tuple(
            item.assessment
            for item in sorted(
                provisional_records,
                key=lambda item: item.candidate.candidate_identifier,
            )
            if item.assessment is not None
        )
        ranking = rank_candidates(campaign, assessments, policy=self.selection_policy)
        decision_map = {item.candidate_identifier: item for item in ranking.decisions}
        candidate_records = tuple(
            DiscoveryCandidateRecord(
                candidate=record.candidate,
                assessment=record.assessment,
                selection_decision=decision_map[record.candidate.candidate_identifier],
            )
            for record in sorted(
                provisional_records,
                key=lambda item: item.candidate.candidate_identifier,
            )
        )
        selected_ids = tuple(
            sorted(
                decision.candidate_identifier
                for decision in ranking.decisions
                if decision.outcome is SelectionOutcome.SELECTED
            )
        )

        next_contexts = self._next_generation_contexts(
            campaign,
            candidate_records,
            verification_traces,
            selected_ids,
        )

        best_score = self._best_composite_score(ranking)
        seen_pareto = set(state.seen_pareto_candidate_identifiers)
        current_front = (
            set(ranking.pareto_fronts[0]) if ranking.pareto_fronts else set()
        )
        new_pareto = current_front - seen_pareto
        progress = self._generation_made_progress(
            state,
            best_score,
            len(new_pareto),
            has_ranked=bool(ranking.ranked_candidates),
            campaign=campaign,
        )
        stagnant = 0 if progress else state.stagnant_generations + 1
        invalid_generations = (
            state.consecutive_invalid_generations + 1
            if candidates and not valid_ids
            else 0
        )
        updated_best = state.best_composite_score
        if best_score is not None and (
            updated_best is None or best_score > updated_best
        ):
            updated_best = best_score

        generation_record = DiscoveryGenerationRecord(
            generation=generation,
            generation_contexts=contexts,
            generation_requests=tuple(requests),
            generation_results=tuple(generation_results),
            candidate_records=candidate_records,
            validity_results=tuple(validity_results),
            evaluation_results=tuple(evaluation_results),
            verification_traces=tuple(verification_traces),
            authorization_decisions=tuple(authorization_decisions),
            ranking=ranking,
            valid_candidate_identifiers=tuple(valid_ids),
            selected_candidate_identifiers=selected_ids,
            budget_interrupted=budget_interrupted,
            budget_reason=budget_reason,
        )
        new_generations = (*state.generations, generation_record)
        usage = DiscoveryCampaignUsage(
            generations_completed=len(new_generations),
            candidates_generated=usage_values["candidates_generated"],
            validity_checks=usage_values["validity_checks"],
            evaluations=usage_values["evaluations"],
            expensive_evaluations=usage_values["expensive_evaluations"],
            elapsed_wall_time_seconds=state.usage.elapsed_wall_time_seconds,
            resources=self._resource_snapshot(campaign),
        )
        next_generation = generation + 1

        disposition = DiscoveryCampaignDisposition.RUNNING
        rationale = "Generation completed; scientifically justified parents remain."
        if self._campaign_succeeded(campaign, candidate_records, selected_ids):
            disposition = DiscoveryCampaignDisposition.SUCCESS
            rationale = (
                "At least one selected candidate satisfies every required campaign "
                "objective with required verification evidence."
            )
        elif authorization_required:
            disposition = DiscoveryCampaignDisposition.AUTHORIZATION_REQUIRED
            rationale = (
                "Discovery cannot continue protected candidate execution without fresh "
                "external authorization."
            )
        elif budget_interrupted:
            disposition = DiscoveryCampaignDisposition.BUDGET_EXHAUSTED
            rationale = budget_reason or "Campaign budget is exhausted."
        elif usage.candidates_generated >= campaign.budget.max_candidates_total:
            disposition = DiscoveryCampaignDisposition.CANDIDATE_LIMIT_REACHED
            rationale = "Campaign candidate-count ceiling has been reached."
        elif next_generation >= campaign.budget.max_generations:
            disposition = DiscoveryCampaignDisposition.GENERATION_LIMIT_REACHED
            rationale = "Campaign generation ceiling has been reached."
        elif not candidates:
            disposition = DiscoveryCampaignDisposition.NO_VALID_CANDIDATES
            rationale = "Candidate generation produced no candidates."
        elif (
            invalid_generations
            >= campaign.stopping_policy.maximum_consecutive_invalid_generations
        ):
            disposition = DiscoveryCampaignDisposition.NO_VALID_CANDIDATES
            rationale = (
                "Campaign reached its consecutive invalid-generation stopping rule."
            )
        elif stagnant >= campaign.stopping_policy.stagnation_generations:
            disposition = DiscoveryCampaignDisposition.STAGNATED
            rationale = "Campaign reached its no-improvement stopping rule."
        elif not next_contexts:
            disposition = DiscoveryCampaignDisposition.NO_SELECTABLE_CANDIDATES
            rationale = (
                "No selected or scientifically repairable parent candidates remain."
            )

        next_state = DiscoveryCampaignState(
            campaign_identifier=campaign.campaign_identifier,
            campaign_sha256=campaign.fingerprint,
            lineage=lineage,
            generations=new_generations,
            usage=usage,
            next_generation=next_generation,
            next_generation_contexts=next_contexts,
            best_composite_score=updated_best,
            stagnant_generations=stagnant,
            consecutive_invalid_generations=invalid_generations,
            seen_pareto_candidate_identifiers=tuple(seen_pareto | current_front),
            disposition=disposition,
            rationale=rationale,
        )

        boundary_terminal = self._post_generation_external_budget_terminal(
            campaign,
            next_state,
            segment_start=segment_start,
            carried_wall=carried_wall,
        )
        if boundary_terminal is not None and not next_state.terminal:
            terminal_disposition, terminal_rationale = boundary_terminal
            next_state = self._replace_state(
                next_state,
                disposition=terminal_disposition,
                rationale=terminal_rationale,
            )
        return next_state

    def _validate_campaign_configuration(self, campaign: DiscoveryCampaign) -> None:
        if campaign.budget.resource_ceilings and self.resource_meter is None:
            raise DiscoveryCampaignConfigurationError(
                "Campaign resource ceilings require an external DiscoveryResourceMeter."
            )

    def _validate_state_campaign(
        self,
        campaign: DiscoveryCampaign,
        state: DiscoveryCampaignState,
    ) -> None:
        if (
            state.campaign_identifier != campaign.campaign_identifier
            or state.campaign_sha256 != campaign.fingerprint
        ):
            raise DiscoveryCampaignIntegrityError(
                "Discovery state does not match the immutable campaign contract."
            )

    def _validate_checkpoint_campaign(
        self,
        campaign: DiscoveryCampaign,
        checkpoint: DiscoveryCampaignCheckpoint,
    ) -> None:
        if (
            checkpoint.campaign_identifier != campaign.campaign_identifier
            or checkpoint.campaign_sha256 != campaign.fingerprint
        ):
            raise DiscoveryCampaignIntegrityError(
                "Discovery checkpoint belongs to a different campaign."
            )
        self._validate_state_campaign(campaign, checkpoint.state)

    def _run_result(
        self,
        campaign: DiscoveryCampaign,
        state: DiscoveryCampaignState,
    ) -> DiscoveryCampaignRun:
        checkpoint = self.checkpoint(campaign, state)
        return DiscoveryCampaignRun(
            campaign_identifier=campaign.campaign_identifier,
            campaign_sha256=campaign.fingerprint,
            state=state,
            checkpoint=checkpoint,
            completed=state.terminal,
        )

    @staticmethod
    def _replace_state(
        state: DiscoveryCampaignState,
        **updates: object,
    ) -> DiscoveryCampaignState:
        payload = state.model_dump(mode="python")
        payload.update(updates)
        return DiscoveryCampaignState.model_validate(payload)

    def _replace_usage_wall_time(
        self,
        state: DiscoveryCampaignState,
        wall_time: float,
        campaign: DiscoveryCampaign,
    ) -> DiscoveryCampaignState:
        usage = DiscoveryCampaignUsage(
            generations_completed=state.usage.generations_completed,
            candidates_generated=state.usage.candidates_generated,
            validity_checks=state.usage.validity_checks,
            evaluations=state.usage.evaluations,
            expensive_evaluations=state.usage.expensive_evaluations,
            elapsed_wall_time_seconds=wall_time,
            resources=self._resource_snapshot(campaign),
        )
        return self._replace_state(state, usage=usage)

    def _pre_generation_terminal(
        self,
        campaign: DiscoveryCampaign,
        state: DiscoveryCampaignState,
        *,
        segment_start: float,
        carried_wall: float,
    ) -> tuple[DiscoveryCampaignDisposition, str] | None:
        if state.next_generation >= campaign.budget.max_generations:
            return (
                DiscoveryCampaignDisposition.GENERATION_LIMIT_REACHED,
                "Campaign generation ceiling has been reached.",
            )
        if state.usage.candidates_generated >= campaign.budget.max_candidates_total:
            return (
                DiscoveryCampaignDisposition.CANDIDATE_LIMIT_REACHED,
                "Campaign candidate-count ceiling has been reached.",
            )
        return self._external_budget_terminal(
            campaign,
            elapsed=carried_wall + max(0.0, self.clock() - segment_start),
        )

    def _post_generation_external_budget_terminal(
        self,
        campaign: DiscoveryCampaign,
        state: DiscoveryCampaignState,
        *,
        segment_start: float,
        carried_wall: float,
    ) -> tuple[DiscoveryCampaignDisposition, str] | None:
        del state
        return self._external_budget_terminal(
            campaign,
            elapsed=carried_wall + max(0.0, self.clock() - segment_start),
        )

    def _external_budget_terminal(
        self,
        campaign: DiscoveryCampaign,
        *,
        elapsed: float,
    ) -> tuple[DiscoveryCampaignDisposition, str] | None:
        if (
            campaign.budget.maximum_wall_time_seconds is not None
            and elapsed >= campaign.budget.maximum_wall_time_seconds
        ):
            return (
                DiscoveryCampaignDisposition.BUDGET_EXHAUSTED,
                "Campaign wall-time ceiling has been reached.",
            )
        resources = {
            (item.dimension, item.unit): item.consumed
            for item in self._resource_snapshot(campaign)
        }
        for ceiling in campaign.budget.resource_ceilings:
            if resources.get((ceiling.dimension, ceiling.unit), 0.0) >= ceiling.maximum:
                return (
                    DiscoveryCampaignDisposition.BUDGET_EXHAUSTED,
                    "Campaign generic resource ceiling has been reached.",
                )
        return None

    def _resource_snapshot(
        self,
        campaign: DiscoveryCampaign,
    ) -> tuple[DiscoveryResourceUsage, ...]:
        del campaign
        if self.resource_meter is None:
            return ()
        values = tuple(self.resource_meter.read())
        identities = [(item.dimension, item.unit) for item in values]
        if len(identities) != len(set(identities)):
            raise DiscoveryCampaignIntegrityError(
                "Resource meter returned duplicate dimension/unit identities."
            )
        return tuple(sorted(values, key=lambda item: (item.dimension, item.unit)))

    def _resolve_generators(
        self,
        campaign: DiscoveryCampaign,
    ) -> tuple[CandidateGenerator, ...]:
        if not campaign.permitted_candidate_types:
            return tuple(
                self.generators.get(identifier)
                for identifier in self.generators.identifiers()
            )
        resolved: dict[str, CandidateGenerator] = {}
        for candidate_type in campaign.permitted_candidate_types:
            for generator in self.generators.resolve(candidate_type):
                resolved[generator.generator_identifier] = generator
        return tuple(resolved[identifier] for identifier in sorted(resolved))

    def _resolved_validity_checkers(
        self,
        candidate: DiscoveryCandidate,
        campaign: DiscoveryCampaign,
    ) -> tuple[CandidateValidityChecker, ...]:
        return tuple(
            checker
            for checker in self.validity_checkers.resolve(candidate.candidate_type)
            if _component_matches_objectives(checker, campaign)
        )

    def _resolved_evaluators(
        self,
        candidate: DiscoveryCandidate,
        campaign: DiscoveryCampaign,
    ) -> tuple[CandidateEvaluator, ...]:
        return tuple(
            evaluator
            for evaluator in self.evaluators.resolve(candidate.candidate_type)
            if _component_matches_objectives(evaluator, campaign)
        )

    def _generation_request(
        self,
        campaign: DiscoveryCampaign,
        generation: int,
        context: DiscoveryGenerationContext,
        prior_records: dict[str, DiscoveryCandidateRecord],
        generator: CandidateGenerator,
        remaining: int,
    ) -> CandidateGenerationRequest:
        parents = tuple(
            prior_records[identifier].candidate
            for identifier in context.parent_candidate_identifiers
        )
        assessments = tuple(
            prior_records[identifier].assessment
            for identifier in context.parent_candidate_identifiers
            if prior_records[identifier].assessment is not None
        )
        request_identifier = _stable_identifier(
            "candidate-generation-request",
            {
                "campaign": campaign.fingerprint,
                "generation": generation,
                "context": context.fingerprint,
                "generator": generator.generator_identifier,
                "remaining": remaining,
            },
        )
        return CandidateGenerationRequest(
            request_identifier=request_identifier,
            campaign_identifier=campaign.campaign_identifier,
            campaign_sha256=campaign.fingerprint,
            generation=generation,
            parent_candidates=parents,
            parent_assessments=assessments,
            objective_identifiers=tuple(
                item.objective_identifier for item in campaign.objectives
            ),
            diagnosis_references=context.diagnosis_references,
            replanning_references=context.replanning_references,
            max_candidates=remaining,
        )

    def _validate_generation_result(
        self,
        campaign: DiscoveryCampaign,
        request: CandidateGenerationRequest,
        generator: CandidateGenerator,
        result: CandidateGenerationResult,
    ) -> None:
        if result.generator_identifier != generator.generator_identifier:
            raise DiscoveryCampaignIntegrityError(
                "Generator result changed the producing generator identity."
            )
        if result.request_sha256 != request.fingerprint:
            raise DiscoveryCampaignIntegrityError(
                "Generator result does not belong to its immutable request."
            )
        if len(result.candidates) > request.max_candidates:
            raise DiscoveryCampaignIntegrityError(
                "Generator exceeded its requested candidate ceiling."
            )
        for candidate in result.candidates:
            if not campaign.accepts_candidate_type(candidate.candidate_type):
                raise DiscoveryCampaignIntegrityError(
                    "Generator produced a candidate type outside campaign policy."
                )
            supported = generator.supported_candidate_types
            if supported and candidate.candidate_type not in supported:
                raise DiscoveryCampaignIntegrityError(
                    "Generator produced a candidate type outside its registry contract."
                )

    def _validate_initial_candidates(
        self,
        campaign: DiscoveryCampaign,
        candidates: tuple[DiscoveryCandidate, ...],
        candidate_slots: int,
    ) -> tuple[DiscoveryCandidate, ...]:
        if len(candidates) > candidate_slots:
            raise DiscoveryCampaignConfigurationError(
                "Initial candidates exceed the current candidate budget."
            )
        identifiers = [item.candidate_identifier for item in candidates]
        if len(identifiers) != len(set(identifiers)):
            raise DiscoveryCampaignConfigurationError(
                "Initial candidate identifiers must be unique."
            )
        objective_ids = {item.objective_identifier for item in campaign.objectives}
        for candidate in candidates:
            if candidate.generation != 0:
                raise DiscoveryCampaignConfigurationError(
                    "Initial campaign candidates must be generation zero."
                )
            if not campaign.accepts_candidate_type(candidate.candidate_type):
                raise DiscoveryCampaignConfigurationError(
                    "Initial candidate type is not permitted by campaign policy."
                )
            if set(candidate.objective_identifiers) != objective_ids:
                raise DiscoveryCampaignConfigurationError(
                    "Initial candidates must preserve the campaign objective contract."
                )
        return tuple(sorted(candidates, key=lambda item: item.candidate_identifier))

    def _validate_candidate_proposal(
        self,
        campaign: DiscoveryCampaign,
        candidate: DiscoveryCandidate,
        generation: int,
        request_and_generator: tuple[CandidateGenerationRequest, CandidateGenerator]
        | None,
        prior_candidates: dict[str, DiscoveryCandidate],
    ) -> None:
        objective_ids = {item.objective_identifier for item in campaign.objectives}
        if candidate.generation != generation:
            raise DiscoveryCampaignIntegrityError(
                "Generated candidate changed the requested generation."
            )
        if set(candidate.objective_identifiers) != objective_ids:
            raise DiscoveryCampaignIntegrityError(
                "Generated candidate changed the immutable campaign objective contract."
            )
        if not campaign.accepts_candidate_type(candidate.candidate_type):
            raise DiscoveryCampaignIntegrityError(
                "Generated candidate type is outside campaign policy."
            )
        if generation == 0:
            if request_and_generator is not None:
                request, _ = request_and_generator
                if request.parent_candidates:
                    raise DiscoveryCampaignIntegrityError(
                        "Generation-zero generator request cannot contain parents."
                    )
            return
        if request_and_generator is None:
            raise DiscoveryCampaignIntegrityError(
                "Transformed candidates must originate from a generation request."
            )
        request, _ = request_and_generator
        permitted_parents = {
            item.candidate_identifier for item in request.parent_candidates
        }
        if not set(candidate.parent_candidate_identifiers).issubset(permitted_parents):
            raise DiscoveryCampaignIntegrityError(
                "Generated candidate used a parent outside its generation context."
            )
        if not candidate.parent_candidate_identifiers:
            raise DiscoveryCampaignIntegrityError(
                "Transformed candidates require at least one prior-generation parent."
            )
        for identifier in candidate.parent_candidate_identifiers:
            parent = prior_candidates.get(identifier)
            if parent is None or parent.generation != generation - 1:
                raise DiscoveryCampaignIntegrityError(
                    "Candidate lineage parents must come from the immediately prior generation."
                )
        if candidate.transformation is None:
            raise DiscoveryCampaignIntegrityError(
                "Transformed discovery candidates require transformation evidence."
            )
        if request.replanning_references:
            supplied_replans = {
                item.recommendation_identifier: item
                for item in request.replanning_references
            }
            child_replan = candidate.transformation.replanning_reference
            child_diagnosis = candidate.transformation.diagnosis_reference
            if child_replan is None or child_diagnosis is None:
                raise DiscoveryCampaignIntegrityError(
                    "Diagnosis-guided descendants must retain diagnosis and replanning lineage."
                )
            expected = supplied_replans.get(child_replan.recommendation_identifier)
            if expected is None or expected.fingerprint != child_replan.fingerprint:
                raise DiscoveryCampaignIntegrityError(
                    "Descendant replanning reference was not supplied by its generation context."
                )
            supplied_diagnoses = {
                item.diagnosis_identifier: item for item in request.diagnosis_references
            }
            expected_diagnosis = supplied_diagnoses.get(
                child_diagnosis.diagnosis_identifier
            )
            if (
                expected_diagnosis is None
                or expected_diagnosis.fingerprint != child_diagnosis.fingerprint
            ):
                raise DiscoveryCampaignIntegrityError(
                    "Descendant diagnosis reference was not supplied by its generation context."
                )

    @staticmethod
    def _validate_validity_result(
        candidate: DiscoveryCandidate,
        checker: CandidateValidityChecker,
        result: CandidateValidityResult,
    ) -> None:
        if result.checker_identifier != checker.checker_identifier:
            raise DiscoveryCampaignIntegrityError(
                "Validity result changed checker identity."
            )
        if (
            result.candidate_identifier != candidate.candidate_identifier
            or result.candidate_sha256 != candidate.fingerprint
        ):
            raise DiscoveryCampaignIntegrityError(
                "Validity result belongs to a different candidate."
            )

    @staticmethod
    def _validate_evaluation_result(
        candidate: DiscoveryCandidate,
        evaluator: CandidateEvaluator,
        result: CandidateEvaluationResult,
        *,
        declared_expensive: bool,
    ) -> None:
        if (
            result.evaluator_identifier != evaluator.evaluator_identifier
            or result.stage_identifier != evaluator.stage_identifier
            or result.stage_order != evaluator.stage_order
        ):
            raise DiscoveryCampaignIntegrityError(
                "Evaluator result changed registered stage identity."
            )
        if (
            result.candidate_identifier != candidate.candidate_identifier
            or result.candidate_sha256 != candidate.fingerprint
        ):
            raise DiscoveryCampaignIntegrityError(
                "Evaluator result belongs to a different candidate."
            )
        if result.expensive != declared_expensive:
            raise DiscoveryCampaignIntegrityError(
                "Evaluator result changed its declared expensive-evaluation class."
            )

    @staticmethod
    def _required_verifier_families(campaign: DiscoveryCampaign) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    family
                    for objective in campaign.objectives
                    for family in objective.required_verifier_families
                }
            )
        )

    @staticmethod
    def _validate_verification_report(
        family: str,
        verifier: CandidateScientificVerifier,
        report: ScientificVerificationReport,
    ) -> None:
        if report.verifier_identifier != verifier.verifier_identifier:
            raise DiscoveryCampaignIntegrityError(
                "Scientific verifier report changed registered verifier identity."
            )
        if report.objective_family != family:
            raise DiscoveryCampaignIntegrityError(
                "Scientific verifier report changed required objective family."
            )

    def _authorization_decision(
        self,
        campaign: DiscoveryCampaign,
        candidate: DiscoveryCandidate,
        operation_identifier: str,
    ) -> DiscoveryAuthorizationDecision | None:
        if self.authorization_gate is None:
            return None
        decision = self.authorization_gate.check(
            campaign, candidate, operation_identifier
        )
        if (
            decision.candidate_identifier != candidate.candidate_identifier
            or decision.candidate_sha256 != candidate.fingerprint
            or decision.operation_identifier != operation_identifier
        ):
            raise DiscoveryCampaignIntegrityError(
                "Authorization evidence does not match the protected candidate operation."
            )
        return decision

    @staticmethod
    def _candidate_requires_fresh_authorization(candidate: DiscoveryCandidate) -> bool:
        transformation = candidate.transformation
        return bool(
            transformation is not None
            and transformation.replanning_reference is not None
            and transformation.replanning_reference.requires_fresh_authorization
        )

    @staticmethod
    def _runtime_constraint(
        candidate: DiscoveryCandidate,
        identifier: str,
        rationale: str,
    ) -> CandidateConstraintResult:
        constraint_id = validate_identifier(
            identifier, label="runtime constraint identifier"
        )
        return CandidateConstraintResult(
            result_identifier=_stable_identifier(
                "runtime-constraint",
                {
                    "candidate": candidate.fingerprint,
                    "constraint": constraint_id,
                    "rationale": rationale,
                },
            ),
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            constraint_identifier=constraint_id,
            passed=False,
            hard=True,
            rationale=rationale,
        )

    @staticmethod
    def _merge_constraint(
        target: dict[str, CandidateConstraintResult],
        value: CandidateConstraintResult,
    ) -> None:
        previous = target.get(value.constraint_identifier)
        if previous is not None and previous.fingerprint != value.fingerprint:
            raise DiscoveryCampaignIntegrityError(
                "Constraint identifiers cannot change meaning across discovery stages."
            )
        target[value.constraint_identifier] = value

    @staticmethod
    def _assessment(
        candidate: DiscoveryCandidate,
        evidence_by_id: dict[str, CandidateEvidenceRecord],
        constraints_by_id: dict[str, CandidateConstraintResult],
        scores_by_objective: dict[str, CandidateObjectiveScore],
        verification_records: tuple[CandidateVerificationRecord, ...],
    ) -> CandidateAssessment:
        evidence = tuple(
            sorted(evidence_by_id.values(), key=lambda item: item.evidence_identifier)
        )
        constraints = tuple(
            sorted(
                constraints_by_id.values(),
                key=lambda item: (item.constraint_identifier, item.result_identifier),
            )
        )
        scores = tuple(
            sorted(
                scores_by_objective.values(), key=lambda item: item.objective_identifier
            )
        )
        identifier = _stable_identifier(
            "candidate-assessment",
            {
                "candidate": candidate.fingerprint,
                "evidence": [item.fingerprint for item in evidence],
                "constraints": [item.fingerprint for item in constraints],
                "scores": [item.fingerprint for item in scores],
                "verification": [item.fingerprint for item in verification_records],
            },
        )
        return CandidateAssessment(
            assessment_identifier=identifier,
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            evidence_records=evidence,
            verification_records=verification_records,
            constraint_results=constraints,
            objective_scores=scores,
        )

    @staticmethod
    def _candidate_record_map(
        state: DiscoveryCampaignState,
    ) -> dict[str, DiscoveryCandidateRecord]:
        return {
            record.candidate.candidate_identifier: record
            for generation in state.generations
            for record in generation.candidate_records
        }

    def _next_generation_contexts(
        self,
        campaign: DiscoveryCampaign,
        candidate_records: tuple[DiscoveryCandidateRecord, ...],
        verification_traces: list[DiscoveryVerificationTrace],
        selected_ids: tuple[str, ...],
    ) -> tuple[DiscoveryGenerationContext, ...]:
        contexts: list[DiscoveryGenerationContext] = []
        records = {
            item.candidate.candidate_identifier: item for item in candidate_records
        }
        if selected_ids:
            contexts.append(
                DiscoveryGenerationContext(
                    context_identifier=_stable_identifier(
                        "discovery-context",
                        {
                            "campaign": campaign.fingerprint,
                            "kind": "selected",
                            "parents": selected_ids,
                        },
                    ),
                    parent_candidate_identifiers=selected_ids,
                    rationale=(
                        "Continue from deterministically selected candidates while "
                        "preserving their full assessment evidence."
                    ),
                )
            )

        blocked_from_generic_repair: set[str] = set()
        for trace in sorted(
            verification_traces,
            key=lambda item: (
                item.candidate_identifier,
                item.report.objective_family,
                item.report.verifier_identifier,
            ),
        ):
            if trace.report.overall_outcome is VerificationOutcome.FAILED:
                if (
                    not trace.report.execution_integrity_passed
                    or not trace.report.authorization_passed
                    or trace.recommendation is None
                    or trace.recommendation.disposition
                    is not ReplanningDisposition.REPLAN
                ):
                    blocked_from_generic_repair.add(trace.candidate_identifier)
            if (
                trace.diagnosis is None
                or trace.recommendation is None
                or trace.recommendation.disposition is not ReplanningDisposition.REPLAN
            ):
                continue
            diagnosis_ref = diagnosis_reference(trace.diagnosis)
            replan_ref = replanning_reference(trace.recommendation)
            contexts.append(
                DiscoveryGenerationContext(
                    context_identifier=_stable_identifier(
                        "discovery-context",
                        {
                            "campaign": campaign.fingerprint,
                            "candidate": trace.candidate_sha256,
                            "diagnosis": diagnosis_ref.fingerprint,
                            "replan": replan_ref.fingerprint,
                        },
                    ),
                    parent_candidate_identifiers=(trace.candidate_identifier,),
                    diagnosis_references=(diagnosis_ref,),
                    replanning_references=(replan_ref,),
                    rationale=(
                        "Generate a descendant specifically to address blocking Phase 6 "
                        "scientific-quality evidence."
                    ),
                )
            )

        if not contexts and records:
            repairable = [
                record
                for record in records.values()
                if record.candidate.candidate_identifier
                not in blocked_from_generic_repair
                and not self._has_runtime_infrastructure_failure(record)
            ]
            repairable = sorted(repairable, key=self._repair_parent_key)[
                : self.selection_policy.max_selected_candidates
            ]
            if repairable:
                parent_ids = tuple(
                    item.candidate.candidate_identifier for item in repairable
                )
                contexts.append(
                    DiscoveryGenerationContext(
                        context_identifier=_stable_identifier(
                            "discovery-context",
                            {
                                "campaign": campaign.fingerprint,
                                "kind": "repair",
                                "parents": parent_ids,
                                "assessments": [
                                    item.assessment.fingerprint
                                    if item.assessment is not None
                                    else None
                                    for item in repairable
                                ],
                            },
                        ),
                        parent_candidate_identifiers=parent_ids,
                        rationale=(
                            "No candidate was selectable; expose the closest deterministic "
                            "assessment evidence to generators for bounded repair."
                        ),
                    )
                )
        unique = {item.fingerprint: item for item in contexts}
        return tuple(sorted(unique.values(), key=lambda item: item.context_identifier))

    @staticmethod
    def _has_runtime_infrastructure_failure(record: DiscoveryCandidateRecord) -> bool:
        assessment = record.assessment
        if assessment is None:
            return True
        return any(
            not item.passed
            and item.hard
            and item.constraint_identifier.startswith(_RESERVED_RUNTIME_PREFIXES)
            for item in assessment.constraint_results
        )

    @staticmethod
    def _repair_parent_key(record: DiscoveryCandidateRecord) -> tuple[int, int, str]:
        assessment = record.assessment
        if assessment is None:
            return (10**9, 0, record.candidate.candidate_identifier)
        hard_failures = sum(
            not item.passed and item.hard for item in assessment.constraint_results
        )
        satisfied = sum(
            item.satisfies_objective for item in assessment.objective_scores
        )
        return (hard_failures, -satisfied, record.candidate.candidate_identifier)

    @staticmethod
    def _best_composite_score(ranking: MultiObjectiveRanking) -> float | None:
        values = [
            item.composite_score
            for item in ranking.ranked_candidates
            if item.composite_score is not None
        ]
        return max(values) if values else None

    @staticmethod
    def _generation_made_progress(
        state: DiscoveryCampaignState,
        best_score: float | None,
        new_pareto_count: int,
        *,
        has_ranked: bool,
        campaign: DiscoveryCampaign,
    ) -> bool:
        if not state.generations:
            return has_ranked
        score_progress = False
        if best_score is not None:
            if state.best_composite_score is None:
                score_progress = True
            else:
                score_progress = (
                    best_score - state.best_composite_score
                    > campaign.stopping_policy.minimum_composite_improvement
                )
        pareto_progress = bool(
            campaign.stopping_policy.minimum_new_pareto_candidates > 0
            and new_pareto_count
            >= campaign.stopping_policy.minimum_new_pareto_candidates
        )
        return score_progress or pareto_progress

    def _campaign_succeeded(
        self,
        campaign: DiscoveryCampaign,
        records: tuple[DiscoveryCandidateRecord, ...],
        selected_ids: tuple[str, ...],
    ) -> bool:
        if not campaign.stopping_policy.stop_when_objectives_satisfied:
            return False
        by_id = {item.candidate.candidate_identifier: item for item in records}
        required = {
            item.objective_identifier for item in campaign.objectives if item.required
        }
        for candidate_id in selected_ids:
            record = by_id[candidate_id]
            assessment = record.assessment
            if assessment is None:
                continue
            scores = {
                item.objective_identifier: item for item in assessment.objective_scores
            }
            if not required.issubset(scores):
                continue
            if any(
                not scores[identifier].satisfies_objective for identifier in required
            ):
                continue
            if assessment.has_hard_constraint_failure:
                continue
            if assessment.verification_records and not all(
                item.execution_integrity_passed and item.authorization_passed
                for item in assessment.verification_records
            ):
                continue
            if (
                self.selection_policy.require_scientific_quality
                and not assessment.scientific_quality_passed
            ):
                continue
            return True
        return False
