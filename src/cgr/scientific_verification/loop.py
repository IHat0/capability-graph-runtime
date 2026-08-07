"""Bounded verify-diagnose-replan-execute-compare scientific loop."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import Field, field_validator, model_validator

from cgr.science import CanonicalModel
from cgr.science.canonical import validate_identifier, validate_sha256

from .contracts import (
    ScientificVerificationReport,
    ScientificVerificationRequest,
    ScientificVerificationRequestType,
    VerificationOutcome,
)
from .families import default_scientific_verifier_registry
from .framework import ScientificVerifierRegistry
from .replanning import (
    CandidatePlan,
    EvidenceComparison,
    EvidenceComparisonOutcome,
    ReplanningDisposition,
    ReplanningPolicy,
    ReplanningRecommendation,
    ScientificFailureDiagnoser,
    ScientificFailureDiagnosis,
    ScientificReplanner,
    compare_evidence,
    verification_contract_fingerprint,
)


class ScientificCandidateExecutionError(RuntimeError):
    """Raised when a candidate executor violates the scientific replanning contract."""


@runtime_checkable
class ScientificCandidateExecutor(Protocol):
    """Engine-neutral executor for one non-authorizing candidate plan."""

    def execute(
        self,
        plan: CandidatePlan,
        previous_request: ScientificVerificationRequestType,
    ) -> ScientificVerificationRequestType:
        """Execute one externally authorized candidate and return verification evidence."""
        ...


class ScientificReplanningRunDisposition(str, Enum):
    """String constants for terminal scientific replanning outcomes."""

    VERIFIED = "verified"
    ACCEPTED_REPLAN = "accepted_replan"
    AUTHORIZATION_REQUIRED = "authorization_required"
    NOT_REPLANNABLE = "not_replannable"
    NO_IMPROVEMENT = "no_improvement"
    EXHAUSTED = "exhausted"


class ScientificReplanningAttempt(CanonicalModel):
    """One candidate generation and its evidence comparison."""

    generation: int = Field(ge=1)
    plan: CandidatePlan
    request_sha256: str
    report: ScientificVerificationReport
    comparison: EvidenceComparison
    diagnosis: ScientificFailureDiagnosis | None = None
    recommendation: ReplanningRecommendation | None = None

    @field_validator("request_sha256")
    @classmethod
    def validate_request_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def validate_attempt(self) -> Self:
        if self.plan.generation != self.generation:
            raise ValueError("Attempt generation must match its candidate plan.")
        if self.report.request_sha256 != self.request_sha256:
            raise ValueError("Attempt report must belong to the candidate request.")
        if (self.diagnosis is None) != (self.recommendation is None):
            raise ValueError(
                "Attempt follow-up diagnosis and recommendation must appear together."
            )
        if self.diagnosis is not None:
            if self.diagnosis.report_identifier != self.report.report_identifier:
                raise ValueError("Attempt diagnosis does not belong to its report.")
            if (
                self.recommendation is not None
                and self.recommendation.diagnosis_identifier
                != self.diagnosis.diagnosis_identifier
            ):
                raise ValueError(
                    "Attempt recommendation does not belong to its diagnosis."
                )
        return self


class ScientificReplanningRun(CanonicalModel):
    """Complete bounded scientific replanning history."""

    initial_request_sha256: str
    initial_report: ScientificVerificationReport
    initial_diagnosis: ScientificFailureDiagnosis | None = None
    initial_recommendation: ReplanningRecommendation | None = None
    attempts: tuple[ScientificReplanningAttempt, ...] = ()
    final_report: ScientificVerificationReport
    disposition: ScientificReplanningRunDisposition
    accepted_plan_identifier: str | None = None
    completed: Literal[True] = True

    @field_validator("initial_request_sha256")
    @classmethod
    def validate_request_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("accepted_plan_identifier")
    @classmethod
    def validate_accepted_plan_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_identifier(value, label="accepted candidate plan identifier")

    @field_validator("attempts")
    @classmethod
    def order_attempts(
        cls, value: tuple[ScientificReplanningAttempt, ...]
    ) -> tuple[ScientificReplanningAttempt, ...]:
        generations = [item.generation for item in value]
        if generations != list(range(1, len(value) + 1)):
            raise ValueError(
                "Scientific replanning attempts must be contiguous and ordered."
            )
        return value

    @model_validator(mode="after")
    def validate_run(self) -> Self:
        if self.initial_report.request_sha256 != self.initial_request_sha256:
            raise ValueError("Initial report does not belong to the initial request.")
        if (self.initial_diagnosis is None) != (self.initial_recommendation is None):
            raise ValueError(
                "Initial diagnosis and recommendation must appear together."
            )
        if self.initial_diagnosis is not None:
            if (
                self.initial_diagnosis.report_identifier
                != self.initial_report.report_identifier
            ):
                raise ValueError("Initial diagnosis does not belong to initial report.")
            if (
                self.initial_recommendation is not None
                and self.initial_recommendation.diagnosis_identifier
                != self.initial_diagnosis.diagnosis_identifier
            ):
                raise ValueError(
                    "Initial recommendation does not belong to initial diagnosis."
                )
        expected_final = (
            self.attempts[-1].report if self.attempts else self.initial_report
        )
        if self.final_report.fingerprint != expected_final.fingerprint:
            raise ValueError("Final report must equal the last available report.")
        if self.disposition == ScientificReplanningRunDisposition.ACCEPTED_REPLAN:
            if not self.attempts or self.accepted_plan_identifier is None:
                raise ValueError("Accepted replans require an accepted candidate plan.")
            if (
                self.accepted_plan_identifier
                != self.attempts[-1].plan.candidate_plan_identifier
            ):
                raise ValueError("Accepted candidate plan must be the final attempt.")
            if self.final_report.overall_outcome is not VerificationOutcome.PASSED:
                raise ValueError("Accepted replans require a passing final report.")
        elif self.accepted_plan_identifier is not None:
            raise ValueError(
                "Only accepted replanning runs may name an accepted candidate plan."
            )
        return self


class ScientificReplanningLoop:
    """Run a bounded scientific-quality repair loop over engine-neutral evidence."""

    def __init__(
        self,
        *,
        registry: ScientificVerifierRegistry | None = None,
        diagnoser: ScientificFailureDiagnoser | None = None,
        replanner: ScientificReplanner | None = None,
        policy: ReplanningPolicy | None = None,
    ) -> None:
        self.registry = registry or default_scientific_verifier_registry()
        self.diagnoser = diagnoser or ScientificFailureDiagnoser()
        self.replanner = replanner or ScientificReplanner()
        self.policy = policy or ReplanningPolicy()

    def run(
        self,
        request: ScientificVerificationRequestType,
        *,
        executor: ScientificCandidateExecutor,
    ) -> ScientificReplanningRun:
        if not isinstance(request, ScientificVerificationRequest):
            raise TypeError("Scientific replanning requires a verification request.")
        if not isinstance(executor, ScientificCandidateExecutor):
            raise TypeError(
                "Scientific replanning requires an engine-neutral candidate executor."
            )

        initial_report = self.registry.verify(request)
        if initial_report.overall_outcome is VerificationOutcome.PASSED:
            return ScientificReplanningRun(
                initial_request_sha256=request.fingerprint,
                initial_report=initial_report,
                final_report=initial_report,
                disposition=ScientificReplanningRunDisposition.VERIFIED,
            )

        diagnosis = self.diagnoser.diagnose(initial_report)
        recommendation = self.replanner.recommend(
            diagnosis,
            policy=self.policy,
        )
        terminal = self._terminal_for_recommendation(recommendation)
        if terminal is not None:
            return ScientificReplanningRun(
                initial_request_sha256=request.fingerprint,
                initial_report=initial_report,
                initial_diagnosis=diagnosis,
                initial_recommendation=recommendation,
                final_report=initial_report,
                disposition=terminal,
            )

        attempts: list[ScientificReplanningAttempt] = []
        current_request = request
        current_report = initial_report
        current_diagnosis = diagnosis
        current_recommendation = recommendation
        parent_plan_identifier: str | None = None

        for generation in range(1, self.policy.max_candidate_generations + 1):
            plan = self.replanner.build_candidate_plan(
                request=current_request,
                report=current_report,
                diagnosis=current_diagnosis,
                recommendation=current_recommendation,
                generation=generation,
                parent_plan_identifier=parent_plan_identifier,
            )
            candidate_request = executor.execute(plan, current_request)
            self._validate_candidate_request(
                plan=plan,
                previous_request=current_request,
                candidate_request=candidate_request,
            )
            candidate_report = self.registry.verify(candidate_request)
            comparison = compare_evidence(
                current_request,
                current_report,
                candidate_request,
                candidate_report,
            )

            followup_diagnosis: ScientificFailureDiagnosis | None = None
            followup_recommendation: ReplanningRecommendation | None = None
            if candidate_report.overall_outcome is VerificationOutcome.FAILED:
                followup_diagnosis = self.diagnoser.diagnose(candidate_report)
                followup_recommendation = self.replanner.recommend(
                    followup_diagnosis,
                    policy=self.policy,
                )

            attempt = ScientificReplanningAttempt(
                generation=generation,
                plan=plan,
                request_sha256=candidate_request.fingerprint,
                report=candidate_report,
                comparison=comparison,
                diagnosis=followup_diagnosis,
                recommendation=followup_recommendation,
            )
            attempts.append(attempt)

            if (
                candidate_report.overall_outcome is VerificationOutcome.PASSED
                and comparison.candidate_preferred
            ):
                return ScientificReplanningRun(
                    initial_request_sha256=request.fingerprint,
                    initial_report=initial_report,
                    initial_diagnosis=diagnosis,
                    initial_recommendation=recommendation,
                    attempts=tuple(attempts),
                    final_report=candidate_report,
                    disposition=ScientificReplanningRunDisposition.ACCEPTED_REPLAN,
                    accepted_plan_identifier=plan.candidate_plan_identifier,
                )

            if candidate_report.overall_outcome is VerificationOutcome.PASSED:
                return ScientificReplanningRun(
                    initial_request_sha256=request.fingerprint,
                    initial_report=initial_report,
                    initial_diagnosis=diagnosis,
                    initial_recommendation=recommendation,
                    attempts=tuple(attempts),
                    final_report=candidate_report,
                    disposition=ScientificReplanningRunDisposition.NO_IMPROVEMENT,
                )

            assert followup_diagnosis is not None
            assert followup_recommendation is not None

            next_terminal = self._terminal_for_recommendation(followup_recommendation)
            if next_terminal is not None:
                return ScientificReplanningRun(
                    initial_request_sha256=request.fingerprint,
                    initial_report=initial_report,
                    initial_diagnosis=diagnosis,
                    initial_recommendation=recommendation,
                    attempts=tuple(attempts),
                    final_report=candidate_report,
                    disposition=next_terminal,
                )

            if self.policy.stop_on_no_improvement and comparison.outcome in {
                EvidenceComparisonOutcome.UNCHANGED,
                EvidenceComparisonOutcome.REGRESSED,
            }:
                return ScientificReplanningRun(
                    initial_request_sha256=request.fingerprint,
                    initial_report=initial_report,
                    initial_diagnosis=diagnosis,
                    initial_recommendation=recommendation,
                    attempts=tuple(attempts),
                    final_report=candidate_report,
                    disposition=ScientificReplanningRunDisposition.NO_IMPROVEMENT,
                )

            current_request = candidate_request
            current_report = candidate_report
            current_diagnosis = followup_diagnosis
            current_recommendation = followup_recommendation
            parent_plan_identifier = plan.candidate_plan_identifier

        return ScientificReplanningRun(
            initial_request_sha256=request.fingerprint,
            initial_report=initial_report,
            initial_diagnosis=diagnosis,
            initial_recommendation=recommendation,
            attempts=tuple(attempts),
            final_report=current_report if not attempts else attempts[-1].report,
            disposition=ScientificReplanningRunDisposition.EXHAUSTED,
        )

    @staticmethod
    def _terminal_for_recommendation(
        recommendation: ReplanningRecommendation,
    ) -> str | None:
        if recommendation.disposition is ReplanningDisposition.REPLAN:
            return None
        if recommendation.disposition is ReplanningDisposition.REQUIRE_AUTHORIZATION:
            return ScientificReplanningRunDisposition.AUTHORIZATION_REQUIRED
        return ScientificReplanningRunDisposition.NOT_REPLANNABLE

    @staticmethod
    def _validate_candidate_request(
        *,
        plan: CandidatePlan,
        previous_request: ScientificVerificationRequestType,
        candidate_request: ScientificVerificationRequestType,
    ) -> None:
        if not isinstance(candidate_request, ScientificVerificationRequest):
            raise ScientificCandidateExecutionError(
                "Candidate executor must return scientific verification evidence."
            )
        if type(candidate_request) is not type(previous_request):
            raise ScientificCandidateExecutionError(
                "Candidate execution changed the verification request contract type."
            )
        if candidate_request.objective_family != previous_request.objective_family:
            raise ScientificCandidateExecutionError(
                "Candidate execution changed the objective family."
            )
        if candidate_request.subject_identifier != previous_request.subject_identifier:
            raise ScientificCandidateExecutionError(
                "Candidate execution changed the scientific subject."
            )
        if plan.baseline_request_sha256 != previous_request.fingerprint:
            raise ScientificCandidateExecutionError(
                "Candidate plan baseline does not match the previous request."
            )
        baseline_contract = verification_contract_fingerprint(previous_request)
        if plan.baseline_verification_contract_sha256 != baseline_contract:
            raise ScientificCandidateExecutionError(
                "Candidate plan verification contract does not match its baseline."
            )
        if verification_contract_fingerprint(candidate_request) != baseline_contract:
            raise ScientificCandidateExecutionError(
                "Candidate execution changed the scientific verification contract."
            )
