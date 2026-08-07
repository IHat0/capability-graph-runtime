from __future__ import annotations

import hashlib
import subprocess
import sys

import pytest
from pydantic import ValidationError

from cgr.discovery import (
    CandidateAssessment,
    CandidateConstraintResult,
    CandidateEvaluationResult,
    CandidateEvaluatorRegistry,
    CandidateEvidenceRecord,
    CandidateGenerationRequest,
    CandidateGenerationResult,
    CandidateGeneratorRegistry,
    CandidateObjectiveScore,
    CandidateScientificVerifierRegistry,
    CandidateTransformation,
    CandidateValidityCheckerRegistry,
    CandidateValidityResult,
    CandidateVerificationRecord,
    DiscoveryAuthorizationDecision,
    DiscoveryBudgetLimit,
    DiscoveryCampaign,
    DiscoveryCampaignBudget,
    DiscoveryCampaignConfigurationError,
    DiscoveryCampaignDisposition,
    DiscoveryCampaignIntegrityError,
    DiscoveryCampaignRuntime,
    DiscoveryCandidate,
    DiscoveryObjective,
    DiscoveryResourceUsage,
    DiscoveryStoppingPolicy,
    ObjectiveDirection,
    SelectionPolicy,
)
from cgr.kernel.contracts import CapabilityVersion
from cgr.science import ArtifactPointer
from cgr.scientific_verification import (
    VERIFICATION_DIMENSION_ORDER,
    DimensionVerificationResult,
    ScientificVerificationFinding,
    ScientificVerificationReport,
    ScientificVerificationSeverity,
    VerificationDimension,
    VerificationOutcome,
)


FAMILY = "candidate_ranking"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _artifact(identifier: str) -> ArtifactPointer:
    return ArtifactPointer(
        artifact_identifier=f"artifact.{identifier}",
        content_sha256=_sha(identifier),
    )


def _objective() -> DiscoveryObjective:
    return DiscoveryObjective(
        objective_identifier="quality",
        metric_identifier="metric.quality",
        direction=ObjectiveDirection.MAXIMIZE,
        required_verifier_families=(FAMILY,),
    )


def _campaign(
    *,
    max_generations: int = 3,
    max_candidates_total: int = 20,
    max_evaluations: int = 20,
    max_expensive: int = 20,
    stop_when_satisfied: bool = True,
    invalid_generations: int = 2,
    stagnation_generations: int = 3,
    resource_ceilings: tuple[DiscoveryBudgetLimit, ...] = (),
) -> DiscoveryCampaign:
    return DiscoveryCampaign(
        campaign_identifier="campaign.runtime",
        description="Candidate-type-neutral runtime campaign.",
        objectives=(_objective(),),
        budget=DiscoveryCampaignBudget(
            max_generations=max_generations,
            max_candidates_total=max_candidates_total,
            max_candidates_per_generation=min(10, max_candidates_total),
            max_validity_checks=100,
            max_evaluations=max_evaluations,
            max_expensive_evaluations=max_expensive,
            resource_ceilings=resource_ceilings,
        ),
        stopping_policy=DiscoveryStoppingPolicy(
            stop_when_objectives_satisfied=stop_when_satisfied,
            stagnation_generations=stagnation_generations,
            maximum_consecutive_invalid_generations=invalid_generations,
        ),
    )


class _Generator:
    def __init__(
        self,
        candidate_type: str,
        *,
        roots: int = 1,
        use_all_parents: bool = False,
        drift_objectives: bool = False,
    ) -> None:
        self.candidate_type = candidate_type
        self.roots = roots
        self.use_all_parents = use_all_parents
        self.drift_objectives = drift_objectives
        self.requests: list[CandidateGenerationRequest] = []

    @property
    def generator_identifier(self) -> str:
        return "generator.scripted"

    @property
    def supported_candidate_types(self) -> tuple[str, ...]:
        return ()

    def propose(self, request: CandidateGenerationRequest) -> CandidateGenerationResult:
        self.requests.append(request)
        count = self.roots if request.generation == 0 else 1
        count = min(count, request.max_candidates)
        candidates = []
        for index in range(count):
            identifier = f"candidate.g{request.generation}.{index}"
            objectives = (
                ("drifted-objective",)
                if self.drift_objectives
                else request.objective_identifiers
            )
            if request.generation == 0:
                candidates.append(
                    DiscoveryCandidate(
                        candidate_identifier=identifier,
                        candidate_type=self.candidate_type,
                        generation=0,
                        generated_by=self.generator_identifier,
                        representation_artifacts=(_artifact(identifier),),
                        objective_identifiers=objectives,
                    )
                )
                continue

            parents = (
                request.parent_candidates
                if self.use_all_parents
                else request.parent_candidates[:1]
            )
            diagnosis = (
                request.diagnosis_references[0]
                if request.diagnosis_references
                else None
            )
            replan = (
                request.replanning_references[0]
                if request.replanning_references
                else None
            )
            source_ids = tuple(item.candidate_identifier for item in parents)
            transformation = CandidateTransformation(
                transformation_identifier=f"transformation.g{request.generation}.{index}",
                transformation_kind="bounded_improvement",
                source_candidate_identifiers=source_ids,
                target_candidate_type=self.candidate_type,
                generator_identifier=self.generator_identifier,
                rationale=(
                    "Use immutable parent assessment and scientific diagnosis evidence "
                    "to propose a bounded descendant."
                ),
                diagnosis_reference=diagnosis,
                replanning_reference=replan,
            )
            candidates.append(
                DiscoveryCandidate(
                    candidate_identifier=identifier,
                    candidate_type=self.candidate_type,
                    generation=request.generation,
                    generated_by=self.generator_identifier,
                    representation_artifacts=(_artifact(identifier),),
                    objective_identifiers=objectives,
                    parent_candidate_identifiers=source_ids,
                    transformation=transformation,
                )
            )

        return CandidateGenerationResult(
            result_identifier=f"generation-result.{request.request_identifier}",
            generator_identifier=self.generator_identifier,
            request_sha256=request.fingerprint,
            candidates=tuple(candidates),
        )


class _Checker:
    def __init__(self, *, invalid_generation: int | None = None) -> None:
        self.invalid_generation = invalid_generation
        self.calls: list[str] = []

    @property
    def checker_identifier(self) -> str:
        return "checker.scripted"

    @property
    def supported_candidate_types(self) -> tuple[str, ...]:
        return ()

    def check(self, candidate: DiscoveryCandidate) -> CandidateValidityResult:
        self.calls.append(candidate.candidate_identifier)
        invalid = candidate.generation == self.invalid_generation
        findings = ()
        if invalid:
            findings = (
                CandidateConstraintResult(
                    result_identifier=f"constraint.{candidate.candidate_identifier}",
                    candidate_identifier=candidate.candidate_identifier,
                    candidate_sha256=candidate.fingerprint,
                    constraint_identifier="validity.synthetic",
                    passed=False,
                    hard=True,
                    rationale="Synthetic candidate validity failure.",
                ),
            )
        return CandidateValidityResult(
            result_identifier=f"validity.{candidate.candidate_identifier}",
            checker_identifier=self.checker_identifier,
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            valid=not invalid,
            findings=findings,
        )


class _Evaluator:
    def __init__(
        self,
        identifier: str = "evaluator.scripted",
        *,
        stage: str = "evaluation",
        order: int = 0,
        expensive: bool = False,
        requires_authorization: bool = False,
        call_log: list[str] | None = None,
        normalized_offset: float = 0.0,
        generation_scale: float = 0.5,
        supported_objectives: tuple[str, ...] = (),
    ) -> None:
        self._identifier = identifier
        self._stage = stage
        self._order = order
        self.expensive = expensive
        self.requires_authorization = requires_authorization
        self.call_log = call_log if call_log is not None else []
        self.normalized_offset = normalized_offset
        self.generation_scale = generation_scale
        self.supported_objective_identifiers = supported_objectives

    @property
    def evaluator_identifier(self) -> str:
        return self._identifier

    @property
    def supported_candidate_types(self) -> tuple[str, ...]:
        return ()

    @property
    def stage_identifier(self) -> str:
        return self._stage

    @property
    def stage_order(self) -> int:
        return self._order

    def evaluate(self, candidate: DiscoveryCandidate) -> CandidateEvaluationResult:
        self.call_log.append(self._identifier)
        normalized = min(
            1.0,
            0.35
            + self.generation_scale * candidate.generation
            + self.normalized_offset,
        )
        evidence = CandidateEvidenceRecord(
            evidence_identifier=f"evidence.{self._identifier}.{candidate.candidate_identifier}",
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            stage_identifier=self.stage_identifier,
            stage_order=self.stage_order,
            evaluator_identifier=self.evaluator_identifier,
            artifacts=candidate.representation_artifacts,
        )
        score = CandidateObjectiveScore(
            score_identifier=f"score.{self._identifier}.{candidate.candidate_identifier}",
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            objective_identifier="quality",
            value=normalized,
            normalized_value=normalized,
            satisfies_objective=normalized >= 0.8,
            evidence_identifiers=(evidence.evidence_identifier,),
        )
        return CandidateEvaluationResult(
            result_identifier=f"evaluation.{self._identifier}.{candidate.candidate_identifier}",
            evaluator_identifier=self.evaluator_identifier,
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            stage_identifier=self.stage_identifier,
            stage_order=self.stage_order,
            evidence_records=(evidence,),
            objective_scores=(score,),
            expensive=self.expensive,
        )


class _ContextEvaluator(_Evaluator):
    def __init__(self, call_log: list[str]) -> None:
        super().__init__(
            "evaluator.contextual",
            stage="contextual",
            order=20,
            call_log=call_log,
            normalized_offset=0.55,
        )
        self.observed_evidence_counts: list[int] = []

    def evaluate_with_context(
        self,
        candidate: DiscoveryCandidate,
        assessment: CandidateAssessment,
    ) -> CandidateEvaluationResult:
        self.observed_evidence_counts.append(len(assessment.evidence_records))
        return super().evaluate(candidate)


def _finding(
    candidate: DiscoveryCandidate,
    dimension: VerificationDimension,
) -> ScientificVerificationFinding:
    return ScientificVerificationFinding(
        finding_identifier=f"finding.{dimension.value}.{candidate.candidate_identifier}",
        dimension=dimension,
        severity=ScientificVerificationSeverity.ERROR,
        message="Synthetic blocking verification finding.",
        blocking=True,
        subject_identifier=candidate.candidate_identifier,
    )


def _report(
    candidate: DiscoveryCandidate,
    verifier_identifier: str,
    family: str,
    *,
    failure_dimension: VerificationDimension | None = None,
    subject_override: str | None = None,
) -> ScientificVerificationReport:
    dimensions = []
    for dimension in VERIFICATION_DIMENSION_ORDER:
        failed = dimension is failure_dimension
        dimensions.append(
            DimensionVerificationResult(
                dimension=dimension,
                outcome=(
                    VerificationOutcome.FAILED if failed else VerificationOutcome.PASSED
                ),
                findings=(_finding(candidate, dimension),) if failed else (),
                summary="Synthetic Phase 6 dimension result.",
            )
        )
    scientific_dimensions = {
        VerificationDimension.IDENTITY_INTEGRITY,
        VerificationDimension.NUMERICAL_INTEGRITY,
        VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
        VerificationDimension.CROSS_CALCULATION_COMPARABILITY,
        VerificationDimension.UNCERTAINTY,
    }
    return ScientificVerificationReport(
        report_identifier=f"report.{verifier_identifier}.{candidate.candidate_identifier}",
        verifier_identifier=verifier_identifier,
        verifier_version=CapabilityVersion(major=1, minor=0, patch=0),
        objective_family=family,
        subject_identifier=subject_override or candidate.candidate_identifier,
        request_sha256=_sha(
            f"request:{candidate.fingerprint}:{family}:{failure_dimension}"
        ),
        dimension_results=tuple(dimensions),
        overall_outcome=(
            VerificationOutcome.FAILED
            if failure_dimension is not None
            else VerificationOutcome.PASSED
        ),
        execution_integrity_passed=(
            failure_dimension is not VerificationDimension.EXECUTION_INTEGRITY
        ),
        scientific_quality_passed=failure_dimension not in scientific_dimensions,
        authorization_passed=(
            failure_dimension is not VerificationDimension.AUTHORIZATION
        ),
    )


class _Verifier:
    def __init__(
        self,
        *,
        failure_generation_zero: VerificationDimension | None = (
            VerificationDimension.SCIENTIFIC_PLAUSIBILITY
        ),
        subject_override: str | None = None,
        family_override: str | None = None,
    ) -> None:
        self.failure_generation_zero = failure_generation_zero
        self.subject_override = subject_override
        self.family_override = family_override

    @property
    def verifier_identifier(self) -> str:
        return "verifier.scripted"

    @property
    def supported_candidate_types(self) -> tuple[str, ...]:
        return ()

    @property
    def objective_families(self) -> tuple[str, ...]:
        return (FAMILY,)

    def verify(
        self,
        candidate: DiscoveryCandidate,
        assessment: CandidateAssessment,
        objective_family: str,
    ) -> ScientificVerificationReport:
        assert assessment.candidate_sha256 == candidate.fingerprint
        failure = self.failure_generation_zero if candidate.generation == 0 else None
        return _report(
            candidate,
            self.verifier_identifier,
            self.family_override or objective_family,
            failure_dimension=failure,
            subject_override=self.subject_override,
        )


class _Gate:
    def __init__(self, *, grant: bool = True) -> None:
        self.grant = grant
        self.calls: list[tuple[str, str]] = []

    def check(
        self,
        campaign: DiscoveryCampaign,
        candidate: DiscoveryCandidate,
        operation_identifier: str,
    ) -> DiscoveryAuthorizationDecision:
        self.calls.append((candidate.candidate_identifier, operation_identifier))
        return DiscoveryAuthorizationDecision(
            decision_identifier=(
                f"authorization.{candidate.candidate_identifier}.{operation_identifier}"
            ),
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            operation_identifier=operation_identifier,
            authorized=self.grant,
            rationale="Synthetic external authorization decision.",
            evidence_sha256=_sha(
                f"{campaign.fingerprint}:{candidate.fingerprint}:{operation_identifier}"
            ),
        )


class _Meter:
    def __init__(self, consumed: float) -> None:
        self.consumed = consumed

    def read(self) -> tuple[DiscoveryResourceUsage, ...]:
        return (
            DiscoveryResourceUsage(
                dimension="compute",
                unit="credits",
                consumed=self.consumed,
            ),
        )


def _runtime(
    candidate_type: str,
    *,
    generator: _Generator | None = None,
    checker: _Checker | None = None,
    evaluators: tuple[_Evaluator, ...] | None = None,
    verifier: _Verifier | None = None,
    gate: _Gate | None = None,
    selection_policy: SelectionPolicy | None = None,
    resource_meter: _Meter | None = None,
    clock=lambda: 0.0,
) -> tuple[DiscoveryCampaignRuntime, _Generator, _Checker]:
    generator = generator or _Generator(candidate_type)
    checker = checker or _Checker()
    evaluators = evaluators or (_Evaluator(),)
    verifier = verifier or _Verifier()
    runtime = DiscoveryCampaignRuntime(
        generators=CandidateGeneratorRegistry((generator,)),
        validity_checkers=CandidateValidityCheckerRegistry((checker,)),
        evaluators=CandidateEvaluatorRegistry(evaluators),
        scientific_verifiers=CandidateScientificVerifierRegistry((verifier,)),
        authorization_gate=gate,
        selection_policy=selection_policy,
        resource_meter=resource_meter,
        clock=clock,
    )
    return runtime, generator, checker


@pytest.mark.parametrize(
    "candidate_type",
    (
        "molecule",
        "protein_sequence",
        "reaction_path",
        "future_scientific_candidate",
    ),
)
def test_full_discovery_loop_is_candidate_type_neutral(candidate_type: str) -> None:
    gate = _Gate()
    runtime, generator, _ = _runtime(candidate_type, gate=gate)
    run = runtime.run(_campaign())

    assert run.completed
    assert run.state.disposition is DiscoveryCampaignDisposition.SUCCESS
    assert len(run.state.generations) == 2
    assert run.state.usage.candidates_generated == 2
    assert run.state.lineage.edges
    child = run.state.generations[-1].candidate_records[0].candidate
    assert child.candidate_type == candidate_type
    assert child.transformation is not None
    assert child.transformation.diagnosis_reference is not None
    assert child.transformation.replanning_reference is not None
    assert child.transformation.authorizes_execution is False
    assert gate.calls == [(child.candidate_identifier, "discovery.evaluate-candidate")]
    assert generator.requests[-1].parent_assessments
    assert not run.authorizes_execution
    assert not run.checkpoint.authorizes_execution


def test_candidate_verification_projection_rejects_inconsistent_summary_flags() -> None:
    candidate = DiscoveryCandidate(
        candidate_identifier="candidate.verification-integrity",
        candidate_type="candidate.any",
        generation=0,
        generated_by="generator.seed",
        representation_artifacts=(_artifact("verification-integrity"),),
        objective_identifiers=("quality",),
    )
    with pytest.raises(ValidationError):
        CandidateVerificationRecord(
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            report_identifier="report.inconsistent",
            report_sha256=_sha("report.inconsistent"),
            objective_family=FAMILY,
            subject_identifier="scientific.subject",
            request_sha256=_sha("request.inconsistent"),
            overall_outcome="passed",
            execution_integrity_passed=True,
            scientific_quality_passed=True,
            authorization_passed=False,
        )


def test_invalid_candidates_are_rejected_before_evaluation() -> None:
    checker = _Checker(invalid_generation=0)
    evaluator = _Evaluator()
    runtime, _, _ = _runtime(
        "candidate.any",
        checker=checker,
        evaluators=(evaluator,),
        verifier=_Verifier(failure_generation_zero=None),
    )
    run = runtime.run(_campaign(max_generations=3, invalid_generations=1))

    assert run.state.disposition is DiscoveryCampaignDisposition.NO_VALID_CANDIDATES
    assert evaluator.call_log == []
    record = run.state.generations[0].candidate_records[0]
    assert record.assessment is not None
    assert record.assessment.has_hard_constraint_failure
    assert record.selection_decision is not None
    assert record.selection_decision.outcome.value == "ineligible"


def test_staged_and_contextual_evaluators_receive_prior_evidence_in_order() -> None:
    calls: list[str] = []
    early = _Evaluator(
        "evaluator.early",
        stage="early",
        order=1,
        call_log=calls,
    )
    contextual = _ContextEvaluator(calls)
    runtime, _, _ = _runtime(
        "candidate.any",
        evaluators=(contextual, early),
        verifier=_Verifier(failure_generation_zero=None),
    )
    run = runtime.run(_campaign(max_generations=1))

    assert calls == ["evaluator.early", "evaluator.contextual"]
    assert contextual.observed_evidence_counts == [1]
    assessment = run.state.generations[0].candidate_records[0].assessment
    assert assessment is not None
    assert assessment.objective_scores[0].normalized_value == pytest.approx(0.9)
    assert len(run.state.generations[0].evaluation_results) == 2


def test_objective_specific_evaluator_routing_skips_irrelevant_component() -> None:
    calls: list[str] = []
    irrelevant = _Evaluator(
        "evaluator.irrelevant",
        call_log=calls,
        supported_objectives=("other-objective",),
    )
    relevant = _Evaluator("evaluator.relevant", call_log=calls)
    runtime, _, _ = _runtime(
        "candidate.any",
        evaluators=(irrelevant, relevant),
        verifier=_Verifier(failure_generation_zero=None),
    )
    runtime.run(_campaign(max_generations=1))
    assert calls == ["evaluator.relevant"]


def test_replanned_descendant_requires_external_authorization_before_any_check() -> (
    None
):
    checker = _Checker()
    evaluator = _Evaluator()
    runtime, _, _ = _runtime(
        "candidate.any",
        checker=checker,
        evaluators=(evaluator,),
        gate=None,
    )
    run = runtime.run(_campaign())

    assert run.state.disposition is DiscoveryCampaignDisposition.AUTHORIZATION_REQUIRED
    assert len(run.state.generations) == 2
    assert len(run.state.lineage.edges) == 1
    assert checker.calls == ["candidate.g0.0"]
    assert evaluator.call_log == ["evaluator.scripted"]
    child = run.state.generations[-1].candidate_records[0]
    assert child.assessment is not None
    assert any(
        item.constraint_identifier == "authorization.required"
        for item in child.assessment.constraint_results
    )


def test_rejected_descendant_retains_lineage_and_assessment_evidence() -> None:
    checker = _Checker(invalid_generation=1)
    runtime, _, _ = _runtime(
        "candidate.any",
        checker=checker,
        verifier=_Verifier(failure_generation_zero=None),
    )
    run = runtime.run(
        _campaign(
            max_generations=3,
            stop_when_satisfied=False,
            invalid_generations=1,
        )
    )

    assert run.state.disposition is DiscoveryCampaignDisposition.NO_VALID_CANDIDATES
    assert len(run.state.generations) == 2
    child_record = run.state.generations[1].candidate_records[0]
    child = child_record.candidate
    assert child.generation == 1
    assert child_record.assessment is not None
    assert child_record.assessment.has_hard_constraint_failure
    assert any(
        edge.child_candidate_identifier == child.candidate_identifier
        for edge in run.state.lineage.edges
    )


def test_phase6_authorization_failure_is_never_selected_or_replanned_around() -> None:
    verifier = _Verifier(
        failure_generation_zero=VerificationDimension.AUTHORIZATION,
    )
    runtime, _, _ = _runtime("candidate.any", verifier=verifier)
    run = runtime.run(_campaign())

    assert run.state.disposition is DiscoveryCampaignDisposition.AUTHORIZATION_REQUIRED
    generation = run.state.generations[0]
    assert generation.ranking is not None
    decision = generation.ranking.decisions[0]
    assert decision.outcome.value == "ineligible"
    assert "authorization" in decision.rationale
    assert not run.state.next_generation_contexts


def test_phase6_execution_failure_is_not_misclassified_as_candidate_replanning() -> (
    None
):
    verifier = _Verifier(
        failure_generation_zero=VerificationDimension.EXECUTION_INTEGRITY,
    )
    runtime, generator, _ = _runtime("candidate.any", verifier=verifier)
    run = runtime.run(_campaign())

    assert (
        run.state.disposition is DiscoveryCampaignDisposition.NO_SELECTABLE_CANDIDATES
    )
    assert len(generator.requests) == 1
    generation = run.state.generations[0]
    assert generation.ranking is not None
    assert "execution-integrity" in generation.ranking.decisions[0].rationale
    assert not run.state.next_generation_contexts


def test_expensive_evaluation_budget_is_checked_before_call() -> None:
    evaluator = _Evaluator(expensive=True)
    runtime, _, _ = _runtime(
        "candidate.any",
        evaluators=(evaluator,),
        verifier=_Verifier(failure_generation_zero=None),
    )
    run = runtime.run(_campaign(max_generations=2, max_evaluations=5, max_expensive=0))

    assert run.state.disposition is DiscoveryCampaignDisposition.BUDGET_EXHAUSTED
    assert evaluator.call_log == []
    assert run.state.usage.expensive_evaluations == 0


def test_candidate_limit_stops_campaign_without_exceeding_limit() -> None:
    runtime, _, _ = _runtime("candidate.any")
    run = runtime.run(_campaign(max_generations=4, max_candidates_total=1))

    assert run.state.disposition is DiscoveryCampaignDisposition.CANDIDATE_LIMIT_REACHED
    assert run.state.usage.candidates_generated == 1


def test_stagnation_stops_repeated_selected_descendants() -> None:
    evaluator = _Evaluator(generation_scale=0.0)
    runtime, generator, _ = _runtime(
        "candidate.any",
        evaluators=(evaluator,),
        verifier=_Verifier(failure_generation_zero=None),
    )
    run = runtime.run(
        _campaign(
            max_generations=4,
            stop_when_satisfied=False,
            stagnation_generations=1,
        )
    )

    assert run.state.disposition is DiscoveryCampaignDisposition.STAGNATED
    assert len(run.state.generations) == 2
    assert len(generator.requests) == 2


def test_checkpoint_resume_matches_uninterrupted_deterministic_state() -> None:
    campaign = _campaign()

    gate_a = _Gate()
    uninterrupted, _, _ = _runtime("candidate.any", gate=gate_a)
    completed = uninterrupted.run(campaign)
    assert completed.state.disposition is DiscoveryCampaignDisposition.SUCCESS

    gate_b = _Gate()
    resumable, _, _ = _runtime("candidate.any", gate=gate_b)
    paused = resumable.run(campaign, maximum_cycles=1)
    assert paused.state.disposition is DiscoveryCampaignDisposition.PAUSED
    assert not paused.completed

    resumed = resumable.resume(campaign, paused.checkpoint)
    assert resumed.state.disposition is DiscoveryCampaignDisposition.SUCCESS
    assert resumed.state.fingerprint == completed.state.fingerprint


def test_multi_parent_descendant_creates_branching_lineage_edges() -> None:
    generator = _Generator(
        "candidate.any",
        roots=2,
        use_all_parents=True,
    )
    runtime, _, _ = _runtime(
        "candidate.any",
        generator=generator,
        verifier=_Verifier(failure_generation_zero=None),
        selection_policy=SelectionPolicy(max_selected_candidates=2),
    )
    run = runtime.run(
        _campaign(
            max_generations=2,
            stop_when_satisfied=False,
            stagnation_generations=10,
        )
    )

    assert (
        run.state.disposition is DiscoveryCampaignDisposition.GENERATION_LIMIT_REACHED
    )
    second = run.state.generations[1]
    child = second.candidate_records[0].candidate
    assert len(child.parent_candidate_identifiers) == 2
    assert len(run.state.lineage.edges) == 2
    assert {
        edge.parent_candidate_identifier for edge in run.state.lineage.edges
    } == set(child.parent_candidate_identifiers)


def test_generator_cannot_change_campaign_objective_contract() -> None:
    generator = _Generator("candidate.any", drift_objectives=True)
    runtime, _, _ = _runtime("candidate.any", generator=generator)
    with pytest.raises(DiscoveryCampaignIntegrityError):
        runtime.run(_campaign())


def test_scientific_verifier_cannot_drift_required_objective_family() -> None:
    verifier = _Verifier(
        failure_generation_zero=None,
        family_override="interaction_energy",
    )
    runtime, _, _ = _runtime("candidate.any", verifier=verifier)
    with pytest.raises(DiscoveryCampaignIntegrityError):
        runtime.run(_campaign())


def test_generic_resource_ceilings_require_external_meter_and_stop_when_reached() -> (
    None
):
    campaign = _campaign(
        resource_ceilings=(
            DiscoveryBudgetLimit(
                dimension="compute",
                unit="credits",
                maximum=10,
            ),
        )
    )
    runtime, _, _ = _runtime("candidate.any")
    with pytest.raises(DiscoveryCampaignConfigurationError):
        runtime.run(campaign)

    metered, _, _ = _runtime("candidate.any", resource_meter=_Meter(10))
    run = metered.run(campaign)
    assert run.state.disposition is DiscoveryCampaignDisposition.BUDGET_EXHAUSTED
    assert not run.state.generations


def test_external_stop_is_explicit_and_checkpoint_bound() -> None:
    runtime, _, _ = _runtime("candidate.any", gate=_Gate())
    campaign = _campaign()
    paused = runtime.run(campaign, maximum_cycles=1)
    stopped = runtime.stop(
        campaign,
        paused.checkpoint,
        rationale="Operator stopped the bounded campaign.",
    )

    assert stopped.completed
    assert stopped.state.disposition is DiscoveryCampaignDisposition.EXTERNALLY_STOPPED
    assert "Operator stopped" in stopped.state.rationale


def test_public_runtime_import_does_not_load_native_scientific_engines() -> None:
    program = """
import sys
import cgr.discovery

prefixes = (
    "openmm",
    "pyscf",
    "qiskit",
    "qiskit_nature",
    "qiskit_aer",
    "rdkit",
)
loaded = sorted(
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)
)
if loaded:
    raise SystemExit("native engines loaded: " + ", ".join(loaded))
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
