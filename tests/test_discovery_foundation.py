from __future__ import annotations

import subprocess
import sys
import pytest
from pydantic import ValidationError

from cgr.discovery import (
    CandidateAssessment,
    CandidateConstraintResult,
    CandidateEvaluator,
    CandidateEvaluatorRegistry,
    CandidateEvaluationResult,
    CandidateEvidenceRecord,
    CandidateGenerationRequest,
    CandidateGenerationResult,
    CandidateGenerator,
    CandidateGeneratorRegistry,
    CandidateLineageGraph,
    CandidateObjectiveScore,
    CandidateTransformation,
    CandidateValidityChecker,
    CandidateValidityCheckerRegistry,
    CandidateValidityResult,
    CandidateVerificationRecord,
    DiscoveryCampaign,
    DiscoveryCampaignBudget,
    DiscoveryCandidate,
    DiscoveryCandidateRecord,
    DiscoveryObjective,
    DiscoveryStoppingPolicy,
    DuplicateDiscoveryComponentError,
    ObjectiveDirection,
    PredictedImprovement,
    SelectionOutcome,
    SelectionPolicy,
    diagnosis_reference,
    pareto_dominates,
    rank_candidates,
    replanning_reference,
    verification_record,
)
from cgr.kernel.contracts import CapabilityVersion
from cgr.science import ArtifactPointer
from cgr.scientific_verification import (
    DimensionVerificationResult,
    ReplanningDisposition,
    ScientificFailureDiagnoser,
    ScientificReplanner,
    ScientificVerificationReport,
    VerificationDimension,
    VerificationOutcome,
    finding,
)


def _hash(character: str) -> str:
    return character * 64


def _artifact(identifier: str, character: str) -> ArtifactPointer:
    return ArtifactPointer(
        artifact_identifier=identifier,
        content_sha256=_hash(character),
    )


def _objective(
    identifier: str,
    *,
    direction: ObjectiveDirection = ObjectiveDirection.MAXIMIZE,
    weight: float = 1.0,
    hard: bool = False,
) -> DiscoveryObjective:
    return DiscoveryObjective(
        objective_identifier=identifier,
        metric_identifier=f"metric.{identifier}",
        direction=direction,
        weight=weight,
        hard_constraint=hard,
        description=f"Optimize {identifier}.",
    )


def _campaign(*objectives: DiscoveryObjective) -> DiscoveryCampaign:
    return DiscoveryCampaign(
        campaign_identifier="campaign.universal",
        description="Candidate-type-neutral discovery campaign.",
        objectives=objectives,
        budget=DiscoveryCampaignBudget(
            max_generations=5,
            max_candidates_total=100,
            max_candidates_per_generation=20,
            max_validity_checks=200,
            max_evaluations=100,
            max_expensive_evaluations=20,
        ),
        stopping_policy=DiscoveryStoppingPolicy(
            stagnation_generations=2,
            maximum_consecutive_invalid_generations=2,
        ),
    )


def _root_candidate(
    identifier: str,
    candidate_type: str,
    *,
    artifact_character: str = "a",
    objectives: tuple[str, ...] = ("objective-a", "objective-b"),
    generator: str = "generator.seed",
) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        candidate_identifier=identifier,
        candidate_type=candidate_type,
        generation=0,
        generated_by=generator,
        representation_artifacts=(
            _artifact(f"artifact.{identifier}", artifact_character),
        ),
        objective_identifiers=objectives,
    )


def _verification(
    candidate: DiscoveryCandidate, *, passed: bool = True
) -> CandidateVerificationRecord:
    return CandidateVerificationRecord(
        candidate_identifier=candidate.candidate_identifier,
        candidate_sha256=candidate.fingerprint,
        report_identifier=f"report.{candidate.candidate_identifier}",
        report_sha256=_hash("d"),
        objective_family="candidate_ranking",
        subject_identifier=candidate.candidate_identifier,
        request_sha256=_hash("e"),
        overall_outcome="passed" if passed else "failed",
        execution_integrity_passed=True,
        scientific_quality_passed=passed,
        authorization_passed=True,
    )


def _score(
    candidate: DiscoveryCandidate,
    objective: str,
    value: float,
    normalized: float,
    *,
    satisfies: bool = True,
    uncertainty: float | None = None,
) -> CandidateObjectiveScore:
    return CandidateObjectiveScore(
        score_identifier=f"score.{candidate.candidate_identifier}.{objective}",
        candidate_identifier=candidate.candidate_identifier,
        candidate_sha256=candidate.fingerprint,
        objective_identifier=objective,
        value=value,
        normalized_value=normalized,
        uncertainty=uncertainty,
        satisfies_objective=satisfies,
    )


def _assessment(
    candidate: DiscoveryCandidate,
    scores: tuple[CandidateObjectiveScore, ...],
    *,
    verified: bool = True,
    hard_failure: bool = False,
) -> CandidateAssessment:
    constraints = ()
    if hard_failure:
        constraints = (
            CandidateConstraintResult(
                result_identifier=f"constraint-result.{candidate.candidate_identifier}",
                candidate_identifier=candidate.candidate_identifier,
                candidate_sha256=candidate.fingerprint,
                constraint_identifier="constraint.hard",
                passed=False,
                hard=True,
                rationale="Synthetic hard constraint failure.",
            ),
        )
    return CandidateAssessment(
        assessment_identifier=f"assessment.{candidate.candidate_identifier}",
        candidate_identifier=candidate.candidate_identifier,
        candidate_sha256=candidate.fingerprint,
        verification_records=(_verification(candidate, passed=verified),),
        constraint_results=constraints,
        objective_scores=scores,
    )


def _report(
    *,
    dimension: VerificationDimension = VerificationDimension.UNCERTAINTY,
) -> ScientificVerificationReport:
    blocking = finding(
        "verification.uncertainty.missing",
        dimension,
        "Synthetic scientific-quality failure for discovery integration.",
        subject_identifier="candidate-subject",
    )
    results = []
    quality_dimensions = {
        VerificationDimension.IDENTITY_INTEGRITY,
        VerificationDimension.NUMERICAL_INTEGRITY,
        VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
        VerificationDimension.CROSS_CALCULATION_COMPARABILITY,
        VerificationDimension.UNCERTAINTY,
    }
    for current in (
        VerificationDimension.EXECUTION_INTEGRITY,
        VerificationDimension.IDENTITY_INTEGRITY,
        VerificationDimension.NUMERICAL_INTEGRITY,
        VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
        VerificationDimension.CROSS_CALCULATION_COMPARABILITY,
        VerificationDimension.UNCERTAINTY,
        VerificationDimension.AUTHORIZATION,
    ):
        findings = (blocking,) if current is dimension else ()
        results.append(
            DimensionVerificationResult(
                dimension=current,
                outcome=(
                    VerificationOutcome.FAILED
                    if findings
                    else VerificationOutcome.PASSED
                ),
                findings=findings,
                summary=f"{current.value} result.",
                evidence_identifiers=("synthetic-evidence",),
            )
        )
    return ScientificVerificationReport(
        report_identifier="report.discovery-source",
        verifier_identifier="verifier.discovery-source",
        verifier_version=CapabilityVersion(major=1, minor=0, patch=0),
        objective_family="candidate_ranking",
        subject_identifier="candidate-subject",
        request_sha256=_hash("f"),
        dimension_results=tuple(results),
        overall_outcome=VerificationOutcome.FAILED,
        execution_integrity_passed=(
            dimension is not VerificationDimension.EXECUTION_INTEGRITY
        ),
        scientific_quality_passed=dimension not in quality_dimensions,
        authorization_passed=(dimension is not VerificationDimension.AUTHORIZATION),
    )


@pytest.mark.parametrize(
    "candidate_type",
    (
        "molecule",
        "fragment",
        "scaffold",
        "linker",
        "conformer",
        "pose",
        "mutation",
        "protein_sequence",
        "binding_pocket_design",
        "reaction_path",
        "future_scientific_candidate",
    ),
)
def test_candidate_type_is_extensible_and_not_molecule_centric(
    candidate_type: str,
) -> None:
    candidate = _root_candidate("candidate-1", candidate_type)
    assert candidate.candidate_type == candidate_type
    assert candidate.authorizes_execution is False
    assert candidate.authorization_carried_forward is False


def test_campaign_can_be_unrestricted_or_explicitly_multi_domain() -> None:
    unrestricted = _campaign(_objective("objective-a"))
    assert unrestricted.accepts_candidate_type("protein_sequence")
    assert unrestricted.accepts_candidate_type("reaction_path")

    restricted = DiscoveryCampaign(
        campaign_identifier="campaign.cross-domain",
        description="Cross-domain campaign.",
        objectives=(_objective("objective-a"),),
        permitted_candidate_types=(
            "protein_sequence",
            "binding_pocket_design",
            "reaction_path",
        ),
    )
    assert restricted.accepts_candidate_type("protein_sequence")
    assert restricted.accepts_candidate_type("reaction_path")
    assert not restricted.accepts_candidate_type("molecule")


def test_transformation_requires_new_candidate_identity_and_exact_parent_lineage() -> (
    None
):
    parent = _root_candidate("protein-parent", "protein_sequence")
    transformation = CandidateTransformation(
        transformation_identifier="transform.pocket-redesign",
        transformation_kind="binding_pocket_redesign",
        source_candidate_identifiers=(parent.candidate_identifier,),
        target_candidate_type="binding_pocket_design",
        generator_identifier="generator.pocket-design",
        rationale="Redesign the pocket around the parent sequence evidence.",
        predicted_improvements=(
            PredictedImprovement(
                objective_identifier="objective-a",
                expected_direction=ObjectiveDirection.MAXIMIZE,
                expected_delta=0.1,
                confidence=0.7,
                rationale="Predicted structural improvement.",
            ),
        ),
    )
    child = DiscoveryCandidate(
        candidate_identifier="pocket-child",
        candidate_type="binding_pocket_design",
        generation=1,
        generated_by="generator.pocket-design",
        representation_artifacts=(_artifact("artifact.pocket-child", "b"),),
        objective_identifiers=("objective-a", "objective-b"),
        parent_candidate_identifiers=(parent.candidate_identifier,),
        transformation=transformation,
    )
    assert child.candidate_identifier != parent.candidate_identifier
    assert child.candidate_type != parent.candidate_type

    with pytest.raises(ValidationError):
        DiscoveryCandidate(
            candidate_identifier=parent.candidate_identifier,
            candidate_type=child.candidate_type,
            generation=child.generation,
            generated_by=child.generated_by,
            representation_artifacts=child.representation_artifacts,
            objective_identifiers=child.objective_identifiers,
            parent_candidate_identifiers=(parent.candidate_identifier,),
            transformation=child.transformation,
        )


def test_candidate_lineage_graph_supports_cross_type_dag_and_separate_fingerprints() -> (
    None
):
    parent = _root_candidate("sequence-1", "protein_sequence")
    transformation = CandidateTransformation(
        transformation_identifier="transform.sequence-to-pocket",
        transformation_kind="binding_pocket_redesign",
        source_candidate_identifiers=(parent.candidate_identifier,),
        target_candidate_type="binding_pocket_design",
        generator_identifier="generator.pocket-design",
        rationale="Construct a pocket design descendant.",
    )
    child = DiscoveryCandidate(
        candidate_identifier="pocket-1",
        candidate_type="binding_pocket_design",
        generation=1,
        generated_by="generator.pocket-design",
        representation_artifacts=(_artifact("artifact.pocket-1", "b"),),
        parent_candidate_identifiers=(parent.candidate_identifier,),
        transformation=transformation,
    )
    graph = CandidateLineageGraph().add_candidate(child, (parent,))
    assert len(graph.edges) == 1
    edge = graph.edges[0]
    assert edge.parent_candidate_sha256 == parent.fingerprint
    assert edge.child_candidate_sha256 == child.fingerprint
    assert graph.fingerprint not in {parent.fingerprint, child.fingerprint}


def test_generation_zero_and_transformed_candidates_fail_closed_on_invalid_lineage() -> (
    None
):
    parent = _root_candidate("candidate-parent", "future_scientific_candidate")
    with pytest.raises(ValidationError):
        DiscoveryCandidate(
            candidate_identifier="bad-root",
            candidate_type="future_scientific_candidate",
            generation=0,
            generated_by="generator.seed",
            representation_artifacts=(_artifact("artifact.bad-root", "b"),),
            parent_candidate_identifiers=(parent.candidate_identifier,),
        )

    with pytest.raises(ValidationError):
        DiscoveryCandidate(
            candidate_identifier="bad-child",
            candidate_type="future_scientific_candidate",
            generation=1,
            generated_by="generator.seed",
            representation_artifacts=(_artifact("artifact.bad-child", "c"),),
        )


def test_evidence_lineage_is_an_overlay_and_does_not_change_candidate_identity() -> (
    None
):
    candidate = _root_candidate("candidate-evidence", "reaction_path")
    before = candidate.fingerprint
    evidence = CandidateEvidenceRecord(
        evidence_identifier="evidence.path-analysis",
        candidate_identifier=candidate.candidate_identifier,
        candidate_sha256=candidate.fingerprint,
        stage_identifier="electronic_analysis",
        stage_order=20,
        evaluator_identifier="evaluator.path-electronic",
        artifacts=(_artifact("artifact.path-analysis", "c"),),
    )
    assessment = CandidateAssessment(
        assessment_identifier="assessment.path",
        candidate_identifier=candidate.candidate_identifier,
        candidate_sha256=candidate.fingerprint,
        evidence_records=(evidence,),
    )
    assert candidate.fingerprint == before
    assert assessment.fingerprint != candidate.fingerprint
    assert assessment.evidence_records[0].candidate_sha256 == before


def test_assessment_rejects_evidence_for_a_different_candidate() -> None:
    one = _root_candidate("candidate-one", "pose")
    two = _root_candidate("candidate-two", "pose", artifact_character="b")
    evidence = CandidateEvidenceRecord(
        evidence_identifier="evidence.wrong",
        candidate_identifier=two.candidate_identifier,
        candidate_sha256=two.fingerprint,
        stage_identifier="structural",
        stage_order=1,
        evaluator_identifier="evaluator.structural",
        artifacts=(_artifact("artifact.wrong", "c"),),
    )
    with pytest.raises(ValidationError):
        CandidateAssessment(
            assessment_identifier="assessment.wrong",
            candidate_identifier=one.candidate_identifier,
            candidate_sha256=one.fingerprint,
            evidence_records=(evidence,),
        )


def test_candidate_record_exposes_full_evidence_without_mutating_candidate() -> None:
    campaign = _campaign(_objective("objective-a"), _objective("objective-b"))
    candidate = _root_candidate("candidate-record", "future_scientific_candidate")
    assessment = _assessment(
        candidate,
        (
            _score(candidate, "objective-a", 1.0, 0.8),
            _score(candidate, "objective-b", 2.0, 0.7),
        ),
    )
    ranking = rank_candidates(campaign, (assessment,))
    decision = ranking.decisions[0]
    record = DiscoveryCandidateRecord(
        candidate=candidate,
        assessment=assessment,
        selection_decision=decision,
    )
    assert record.candidate.fingerprint == candidate.fingerprint
    assert record.assessment is assessment
    assert record.selection_decision is decision
    assert record.selection_decision.authorizes_execution is False


def test_campaign_budget_is_bounded_and_rejects_inconsistent_limits() -> None:
    with pytest.raises(ValidationError):
        DiscoveryCampaignBudget(
            max_candidates_total=10,
            max_candidates_per_generation=11,
        )
    with pytest.raises(ValidationError):
        DiscoveryCampaignBudget(
            max_evaluations=5,
            max_expensive_evaluations=6,
        )


def test_phase6_diagnosis_and_replanning_are_preserved_as_non_authorizing_context() -> (
    None
):
    report = _report()
    diagnosis = ScientificFailureDiagnoser().diagnose(report)
    recommendation = ScientificReplanner().recommend(diagnosis)

    assert diagnosis.replannable
    assert recommendation.disposition is ReplanningDisposition.REPLAN
    diagnosis_ref = diagnosis_reference(diagnosis)
    replanning_ref = replanning_reference(recommendation)

    assert diagnosis_ref.diagnosis_sha256 == diagnosis.fingerprint
    assert replanning_ref.recommendation_sha256 == recommendation.fingerprint
    assert replanning_ref.diagnosis_identifier == diagnosis_ref.diagnosis_identifier
    assert replanning_ref.requires_fresh_authorization
    assert replanning_ref.authorizes_execution is False


def test_phase6_verification_projection_keeps_quality_and_authorization_separate() -> (
    None
):
    candidate = _root_candidate("candidate-phase6", "protein_sequence")
    report = _report()
    projected = verification_record(candidate, report)

    assert projected.candidate_sha256 == candidate.fingerprint
    assert projected.report_sha256 == report.fingerprint
    assert projected.execution_integrity_passed
    assert not projected.scientific_quality_passed
    assert projected.authorization_passed


def test_generation_request_accepts_phase6_context_but_never_authorizes() -> None:
    campaign = _campaign(_objective("objective-a"))
    report = _report()
    diagnosis = ScientificFailureDiagnoser().diagnose(report)
    recommendation = ScientificReplanner().recommend(diagnosis)
    parent = _root_candidate(
        "candidate-parent-request",
        "protein_sequence",
        objectives=("objective-a",),
    )
    request = CandidateGenerationRequest(
        request_identifier="generation-request-1",
        campaign_identifier=campaign.campaign_identifier,
        campaign_sha256=campaign.fingerprint,
        generation=1,
        parent_candidates=(parent,),
        objective_identifiers=("objective-a",),
        diagnosis_references=(diagnosis_reference(diagnosis),),
        replanning_references=(replanning_reference(recommendation),),
        max_candidates=5,
    )
    assert request.authorizes_execution is False


class _Generator:
    def __init__(self, identifier: str, supported: tuple[str, ...]) -> None:
        self._identifier = identifier
        self._supported = supported

    @property
    def generator_identifier(self) -> str:
        return self._identifier

    @property
    def supported_candidate_types(self) -> tuple[str, ...]:
        return self._supported

    def propose(self, request: CandidateGenerationRequest) -> CandidateGenerationResult:
        return CandidateGenerationResult(
            result_identifier=f"result.{self._identifier}",
            generator_identifier=self._identifier,
            request_sha256=request.fingerprint,
            candidates=(),
        )


class _Checker:
    def __init__(self, identifier: str, supported: tuple[str, ...]) -> None:
        self._identifier = identifier
        self._supported = supported

    @property
    def checker_identifier(self) -> str:
        return self._identifier

    @property
    def supported_candidate_types(self) -> tuple[str, ...]:
        return self._supported

    def check(self, candidate: DiscoveryCandidate) -> CandidateValidityResult:
        return CandidateValidityResult(
            result_identifier=f"result.{self._identifier}",
            checker_identifier=self._identifier,
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            valid=True,
        )


class _Evaluator:
    def __init__(
        self,
        identifier: str,
        supported: tuple[str, ...],
        stage: str,
        order: int,
    ) -> None:
        self._identifier = identifier
        self._supported = supported
        self._stage = stage
        self._order = order

    @property
    def evaluator_identifier(self) -> str:
        return self._identifier

    @property
    def supported_candidate_types(self) -> tuple[str, ...]:
        return self._supported

    @property
    def stage_identifier(self) -> str:
        return self._stage

    @property
    def stage_order(self) -> int:
        return self._order

    def evaluate(self, candidate: DiscoveryCandidate) -> CandidateEvaluationResult:
        return CandidateEvaluationResult(
            result_identifier=f"result.{self._identifier}",
            evaluator_identifier=self._identifier,
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            stage_identifier=self._stage,
            stage_order=self._order,
        )


def test_discovery_protocols_are_runtime_checkable() -> None:
    assert isinstance(_Generator("generator.any", ()), CandidateGenerator)
    assert isinstance(_Checker("checker.any", ()), CandidateValidityChecker)
    assert isinstance(
        _Evaluator("evaluator.any", (), "generic", 0),
        CandidateEvaluator,
    )


def test_generator_registry_routes_specific_and_universal_candidate_types() -> None:
    registry = CandidateGeneratorRegistry(
        (
            _Generator("generator.universal", ()),
            _Generator("generator.sequence", ("protein_sequence",)),
            _Generator("generator.reaction", ("reaction_path",)),
        )
    )
    assert tuple(
        item.generator_identifier for item in registry.resolve("protein_sequence")
    ) == (
        "generator.sequence",
        "generator.universal",
    )
    assert tuple(
        item.generator_identifier for item in registry.resolve("reaction_path")
    ) == (
        "generator.reaction",
        "generator.universal",
    )
    assert tuple(
        item.generator_identifier
        for item in registry.resolve("future_scientific_candidate")
    ) == ("generator.universal",)


def test_validity_registry_routes_without_domain_privilege() -> None:
    registry = CandidateValidityCheckerRegistry(
        (
            _Checker("checker.universal", ()),
            _Checker("checker.pose", ("pose",)),
        )
    )
    assert tuple(item.checker_identifier for item in registry.resolve("pose")) == (
        "checker.pose",
        "checker.universal",
    )
    assert tuple(
        item.checker_identifier for item in registry.resolve("protein_sequence")
    ) == ("checker.universal",)


def test_evaluator_registry_orders_stages_without_fixing_domain_pipeline() -> None:
    registry = CandidateEvaluatorRegistry(
        (
            _Evaluator("evaluator.late", (), "expensive", 20),
            _Evaluator("evaluator.sequence", ("protein_sequence",), "sequence", 5),
            _Evaluator("evaluator.fast", (), "fast", 1),
        )
    )
    assert tuple(
        item.evaluator_identifier for item in registry.resolve("protein_sequence")
    ) == (
        "evaluator.fast",
        "evaluator.sequence",
        "evaluator.late",
    )
    assert tuple(
        item.evaluator_identifier for item in registry.resolve("reaction_path")
    ) == (
        "evaluator.fast",
        "evaluator.late",
    )


def test_registries_reject_duplicate_component_identity() -> None:
    registry = CandidateGeneratorRegistry((_Generator("generator.same", ()),))
    with pytest.raises(DuplicateDiscoveryComponentError):
        registry.register(_Generator("generator.same", ("molecule",)))


def test_validity_result_fails_exactly_with_hard_failure() -> None:
    candidate = _root_candidate("candidate-validity", "fragment")
    hard_failure = CandidateConstraintResult(
        result_identifier="validity.failure",
        candidate_identifier=candidate.candidate_identifier,
        candidate_sha256=candidate.fingerprint,
        constraint_identifier="chemical_or_domain_validity",
        passed=False,
        hard=True,
        rationale="Synthetic invalid candidate.",
    )
    invalid = CandidateValidityResult(
        result_identifier="validity.result",
        checker_identifier="checker.validity",
        candidate_identifier=candidate.candidate_identifier,
        candidate_sha256=candidate.fingerprint,
        valid=False,
        findings=(hard_failure,),
    )
    assert not invalid.valid
    assert invalid.authorizes_execution is False

    with pytest.raises(ValidationError):
        CandidateValidityResult(
            result_identifier="validity.inconsistent",
            checker_identifier="checker.validity",
            candidate_identifier=candidate.candidate_identifier,
            candidate_sha256=candidate.fingerprint,
            valid=True,
            findings=(hard_failure,),
        )


def test_pareto_dominance_respects_minimize_and_maximize_objectives() -> None:
    affinity = _objective("affinity", direction=ObjectiveDirection.MAXIMIZE)
    burden = _objective("burden", direction=ObjectiveDirection.MINIMIZE)
    left = _root_candidate(
        "candidate-left",
        "protein_sequence",
        objectives=("affinity", "burden"),
    )
    right = _root_candidate(
        "candidate-right",
        "reaction_path",
        artifact_character="b",
        objectives=("affinity", "burden"),
    )
    left_assessment = _assessment(
        left,
        (
            _score(left, "affinity", 10.0, 0.9),
            _score(left, "burden", 2.0, 0.9),
        ),
    )
    right_assessment = _assessment(
        right,
        (
            _score(right, "affinity", 9.0, 0.8),
            _score(right, "burden", 3.0, 0.8),
        ),
    )
    assert pareto_dominates(left_assessment, right_assessment, (affinity, burden))
    assert not pareto_dominates(right_assessment, left_assessment, (affinity, burden))


def test_zero_weight_descriptor_does_not_change_pareto_order() -> None:
    affinity = _objective("affinity", direction=ObjectiveDirection.MAXIMIZE)
    descriptor = _objective("descriptor", direction=ObjectiveDirection.MAXIMIZE, weight=0)
    left = _root_candidate("candidate-left", "small_molecule", objectives=("affinity", "descriptor"))
    right = _root_candidate("candidate-right", "small_molecule", artifact_character="b",
                            objectives=("affinity", "descriptor"))
    left_assessment = _assessment(left, (_score(left, "affinity", 10.0, 0.9),
                                        _score(left, "descriptor", 1.0, 0.1)))
    right_assessment = _assessment(right, (_score(right, "affinity", 9.0, 0.8),
                                          _score(right, "descriptor", 10.0, 0.9)))
    assert pareto_dominates(left_assessment, right_assessment, (affinity, descriptor))
    assert not pareto_dominates(right_assessment, left_assessment, (affinity, descriptor))
    assert left_assessment.objective_scores[1].value == 1.0


def test_multiobjective_ranking_preserves_score_vectors_and_pareto_fronts() -> None:
    campaign = _campaign(
        _objective("affinity", direction=ObjectiveDirection.MAXIMIZE, weight=2.0),
        _objective("burden", direction=ObjectiveDirection.MINIMIZE, weight=1.0),
    )
    a = _root_candidate(
        "candidate-a",
        "molecule",
        objectives=("affinity", "burden"),
    )
    b = _root_candidate(
        "candidate-b",
        "protein_sequence",
        artifact_character="b",
        objectives=("affinity", "burden"),
    )
    c = _root_candidate(
        "candidate-c",
        "reaction_path",
        artifact_character="c",
        objectives=("affinity", "burden"),
    )
    assessments = (
        _assessment(
            a,
            (
                _score(a, "affinity", 10.0, 0.95),
                _score(a, "burden", 5.0, 0.70),
            ),
        ),
        _assessment(
            b,
            (
                _score(b, "affinity", 9.0, 0.80),
                _score(b, "burden", 4.0, 0.95),
            ),
        ),
        _assessment(
            c,
            (
                _score(c, "affinity", 8.0, 0.60),
                _score(c, "burden", 6.0, 0.50),
            ),
        ),
    )
    ranking = rank_candidates(
        campaign,
        assessments,
        policy=SelectionPolicy(max_selected_candidates=2),
    )
    assert ranking.pareto_fronts == (
        ("candidate-a", "candidate-b"),
        ("candidate-c",),
    )
    assert tuple(item.candidate_identifier for item in ranking.ranked_candidates) == (
        "candidate-a",
        "candidate-b",
        "candidate-c",
    )
    assert tuple(
        score.objective_identifier
        for score in ranking.ranked_candidates[0].objective_scores
    ) == ("affinity", "burden")
    selected = {
        item.candidate_identifier
        for item in ranking.decisions
        if item.outcome is SelectionOutcome.SELECTED
    }
    assert selected == {"candidate-a", "candidate-b"}
    assert ranking.authorizes_execution is False
    assert all(not item.authorizes_execution for item in ranking.decisions)


def test_hard_constraint_failure_is_ineligible_not_hidden_in_composite_score() -> None:
    campaign = _campaign(_objective("objective-a"), _objective("objective-b"))
    candidate = _root_candidate("candidate-hard-fail", "scaffold")
    assessment = _assessment(
        candidate,
        (
            _score(candidate, "objective-a", 10.0, 1.0),
            _score(candidate, "objective-b", 10.0, 1.0),
        ),
        hard_failure=True,
    )
    ranking = rank_candidates(campaign, (assessment,))
    assert not ranking.ranked_candidates
    assert ranking.ineligible_candidate_identifiers == (candidate.candidate_identifier,)
    assert ranking.decisions[0].outcome is SelectionOutcome.INELIGIBLE


def test_failed_phase6_scientific_quality_is_ineligible_even_when_execution_passed() -> (
    None
):
    campaign = _campaign(_objective("objective-a"), _objective("objective-b"))
    candidate = _root_candidate("candidate-quality-fail", "binding_pocket_design")
    assessment = _assessment(
        candidate,
        (
            _score(candidate, "objective-a", 1.0, 1.0),
            _score(candidate, "objective-b", 1.0, 1.0),
        ),
        verified=False,
    )
    ranking = rank_candidates(campaign, (assessment,))
    assert ranking.decisions[0].outcome is SelectionOutcome.INELIGIBLE
    assert "scientific-quality" in ranking.decisions[0].rationale


def test_hard_objective_failure_is_ineligible() -> None:
    campaign = _campaign(
        _objective("must-pass", hard=True),
        _objective("optimize"),
    )
    candidate = _root_candidate(
        "candidate-objective-fail",
        "mutation",
        objectives=("must-pass", "optimize"),
    )
    assessment = _assessment(
        candidate,
        (
            _score(candidate, "must-pass", 0.1, 0.1, satisfies=False),
            _score(candidate, "optimize", 1.0, 1.0),
        ),
    )
    ranking = rank_candidates(campaign, (assessment,))
    assert ranking.decisions[0].outcome is SelectionOutcome.INELIGIBLE


def test_composite_score_cannot_replace_underlying_objective_vector() -> None:
    campaign = _campaign(_objective("objective-a"), _objective("objective-b"))
    candidate = _root_candidate("candidate-vector", "linker")
    assessment = _assessment(
        candidate,
        (
            _score(candidate, "objective-a", 2.0, 0.7),
            _score(candidate, "objective-b", 3.0, 0.8),
        ),
    )
    ranking = rank_candidates(campaign, (assessment,))
    ranked = ranking.ranked_candidates[0]
    assert ranked.composite_score == pytest.approx(0.75)
    assert tuple(item.value for item in ranked.objective_scores) == (2.0, 3.0)


def test_objective_uncertainty_requirement_is_explicit_and_enforced() -> None:
    uncertain_objective = DiscoveryObjective(
        objective_identifier="uncertain-objective",
        metric_identifier="metric.uncertain",
        direction=ObjectiveDirection.MAXIMIZE,
        require_uncertainty=True,
        maximum_uncertainty=0.05,
    )
    campaign = _campaign(uncertain_objective)
    candidate = _root_candidate(
        "candidate-uncertainty",
        "future_scientific_candidate",
        objectives=("uncertain-objective",),
    )
    missing = _assessment(
        candidate,
        (_score(candidate, "uncertain-objective", 1.0, 1.0),),
    )
    ranking = rank_candidates(campaign, (missing,))
    assert ranking.decisions[0].outcome is SelectionOutcome.INELIGIBLE
    assert "uncertainty" in ranking.decisions[0].rationale

    acceptable = _assessment(
        candidate,
        (
            _score(
                candidate,
                "uncertain-objective",
                1.0,
                1.0,
                uncertainty=0.01,
            ),
        ),
    )
    ranking = rank_candidates(campaign, (acceptable,))
    assert ranking.decisions[0].outcome is SelectionOutcome.SELECTED

    with pytest.raises(ValidationError):
        DiscoveryObjective(
            objective_identifier="bad-uncertainty",
            metric_identifier="metric.bad-uncertainty",
            direction=ObjectiveDirection.MAXIMIZE,
            maximum_uncertainty=0.1,
        )


def test_public_discovery_import_does_not_load_native_scientific_engines() -> None:
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
    raise SystemExit("native scientific engines loaded: " + ", ".join(loaded))
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_core_candidate_contract_has_no_small_molecule_specific_fields() -> None:
    fields = set(DiscoveryCandidate.model_fields)
    prohibited = {
        "smiles",
        "inchi",
        "ligand",
        "scaffold",
        "molecular_weight",
        "docking_score",
        "protein_sequence",
        "reaction_path",
    }
    assert not fields.intersection(prohibited)
    assert "candidate_type" in fields
    assert "representation_artifacts" in fields
