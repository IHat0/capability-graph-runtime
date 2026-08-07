from __future__ import annotations

import subprocess
import sys

import pytest

from cgr.kernel.contracts import CapabilityVersion
from cgr.scientific_verification import (
    CANDIDATE_CHANGE_KIND_ORDER,
    CalculationEvidence,
    CandidateChangeKind,
    DimensionVerificationResult,
    EnergyComparisonRequest,
    EvidenceComparisonOutcome,
    ReplanningDisposition,
    ReplanningPolicy,
    ScientificCandidateExecutionError,
    ScientificFailureDiagnoser,
    ScientificReplanner,
    ScientificReplanningLoop,
    ScientificReplanningRunDisposition,
    ScientificVerificationReport,
    VerificationDimension,
    VerificationOutcome,
    compare_evidence,
    default_scientific_verifier_registry,
    verification_contract_fingerprint,
    finding,
)


def _hash(character: str) -> str:
    return character * 64


def _calculation(
    identifier: str,
    energy: float,
    *,
    uncertainty: float | None = None,
    authorized: bool = True,
    converged: bool = True,
    geometry_sha256: str | None = None,
    method_sha256: str | None = None,
) -> CalculationEvidence:
    return CalculationEvidence(
        calculation_identifier=identifier,
        artifact_sha256=_hash("a" if identifier == "calc-a" else "b"),
        molecule_identifier="molecule-1",
        geometry_sha256=geometry_sha256 or _hash("c"),
        method_identifier="method-1",
        method_sha256=method_sha256 or _hash("d"),
        execution_completed=True,
        execution_successful=True,
        identity_valid=True,
        numerically_converged=converged,
        values_finite=True,
        authorized=authorized,
        total_energy_hartree=energy,
        uncertainty_hartree=uncertainty,
    )


def _uncertain_energy_request(
    *,
    request_identifier: str = "energy-request-1",
    authorized: bool = True,
    uncertainty: float | None = None,
    gap: float = 5e-7,
) -> EnergyComparisonRequest:
    return EnergyComparisonRequest(
        request_identifier=request_identifier,
        subject_identifier="energy-subject",
        calculations=(
            _calculation(
                "calc-a",
                -1.0,
                uncertainty=uncertainty,
                authorized=authorized,
            ),
            _calculation(
                "calc-b",
                -1.0 + gap,
                uncertainty=uncertainty,
                authorized=authorized,
            ),
        ),
        require_uncertainty=True,
        minimum_resolvable_difference_hartree=1e-6,
    )


def _passing_energy_request(
    *,
    request_identifier: str = "energy-request-passing",
    authorized: bool = True,
) -> EnergyComparisonRequest:
    return EnergyComparisonRequest(
        request_identifier=request_identifier,
        subject_identifier="energy-subject",
        calculations=(
            _calculation(
                "calc-a",
                -1.0,
                uncertainty=1e-8,
                authorized=authorized,
            ),
            _calculation(
                "calc-b",
                -0.99,
                uncertainty=1e-8,
                authorized=authorized,
            ),
        ),
        require_uncertainty=True,
        minimum_resolvable_difference_hartree=1e-6,
    )


def _single_finding_report(
    finding_identifier: str,
    dimension: VerificationDimension,
) -> ScientificVerificationReport:
    blocking = finding(
        finding_identifier,
        dimension,
        "Synthetic blocking finding for deterministic replanning tests.",
        subject_identifier="synthetic-subject",
    )
    results: list[DimensionVerificationResult] = []
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
                summary=f"{current.value} synthetic result.",
                evidence_identifiers=("synthetic-evidence",),
            )
        )

    quality_dimensions = {
        VerificationDimension.IDENTITY_INTEGRITY,
        VerificationDimension.NUMERICAL_INTEGRITY,
        VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
        VerificationDimension.CROSS_CALCULATION_COMPARABILITY,
        VerificationDimension.UNCERTAINTY,
    }
    return ScientificVerificationReport(
        report_identifier=f"synthetic-report-{dimension.value}",
        verifier_identifier="verifier.synthetic",
        verifier_version=CapabilityVersion(major=1, minor=0, patch=0),
        objective_family="energy_comparison",
        subject_identifier="synthetic-subject",
        request_sha256=_hash("e"),
        dimension_results=tuple(results),
        overall_outcome=VerificationOutcome.FAILED,
        execution_integrity_passed=(
            dimension is not VerificationDimension.EXECUTION_INTEGRITY
        ),
        scientific_quality_passed=dimension not in quality_dimensions,
        authorization_passed=(dimension is not VerificationDimension.AUTHORIZATION),
    )


def test_candidate_change_classes_are_exact_and_complete() -> None:
    assert tuple(item.value for item in CANDIDATE_CHANGE_KIND_ORDER) == (
        "active_space",
        "geometry",
        "solver",
        "sampling",
        "scientific_method",
    )
    assert set(CANDIDATE_CHANGE_KIND_ORDER) == set(CandidateChangeKind)


@pytest.mark.parametrize(
    ("finding_identifier", "dimension", "expected_kind"),
    (
        (
            "verification.identity.metal_active_space_missing",
            VerificationDimension.IDENTITY_INTEGRITY,
            CandidateChangeKind.ACTIVE_SPACE,
        ),
        (
            "verification.plausibility.metal_ligand_distance",
            VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
            CandidateChangeKind.GEOMETRY,
        ),
        (
            "verification.numerical.not_converged",
            VerificationDimension.NUMERICAL_INTEGRITY,
            CandidateChangeKind.SOLVER,
        ),
        (
            "verification.uncertainty.missing",
            VerificationDimension.UNCERTAINTY,
            CandidateChangeKind.SAMPLING,
        ),
        (
            "verification.plausibility.energy_order_mismatch",
            VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
            CandidateChangeKind.SCIENTIFIC_METHOD,
        ),
    ),
)
def test_replanner_supports_every_required_change_class(
    finding_identifier: str,
    dimension: VerificationDimension,
    expected_kind: CandidateChangeKind,
) -> None:
    report = _single_finding_report(finding_identifier, dimension)
    diagnosis = ScientificFailureDiagnoser().diagnose(report)
    recommendation = ScientificReplanner().recommend(diagnosis)

    assert diagnosis.replannable
    assert recommendation.disposition is ReplanningDisposition.REPLAN
    assert tuple(item.change_kind for item in recommendation.changes) == (
        expected_kind,
    )
    assert recommendation.requires_fresh_authorization
    assert recommendation.authorizes_execution is False


def test_execution_can_pass_while_scientific_quality_fails_and_replans() -> None:
    request = _uncertain_energy_request()
    report = default_scientific_verifier_registry().verify(request)

    assert report.execution_integrity_passed
    assert not report.scientific_quality_passed
    assert report.authorization_passed
    assert report.overall_outcome is VerificationOutcome.FAILED

    diagnosis = ScientificFailureDiagnoser().diagnose(report)
    recommendation = ScientificReplanner().recommend(diagnosis)

    assert diagnosis.replannable
    assert recommendation.disposition is ReplanningDisposition.REPLAN
    assert {item.change_kind for item in recommendation.changes} == {
        CandidateChangeKind.SAMPLING
    }


def test_candidate_plan_is_non_authorizing_and_deterministic() -> None:
    request = _uncertain_energy_request()
    registry = default_scientific_verifier_registry()
    report = registry.verify(request)
    diagnoser = ScientificFailureDiagnoser()
    replanner = ScientificReplanner()
    diagnosis = diagnoser.diagnose(report)
    recommendation = replanner.recommend(diagnosis)

    first = replanner.build_candidate_plan(
        request=request,
        report=report,
        diagnosis=diagnosis,
        recommendation=recommendation,
        generation=1,
    )
    second = replanner.build_candidate_plan(
        request=request,
        report=report,
        diagnosis=diagnosis,
        recommendation=recommendation,
        generation=1,
    )

    assert first == second
    assert first.fingerprint == second.fingerprint
    assert first.authorization_carried_forward is False
    assert first.authorizes_execution is False
    assert first.baseline_request_sha256 == request.fingerprint
    assert first.baseline_verification_contract_sha256 == (
        verification_contract_fingerprint(request)
    )


def test_policy_can_forbid_an_otherwise_valid_change() -> None:
    request = _uncertain_energy_request()
    report = default_scientific_verifier_registry().verify(request)
    diagnosis = ScientificFailureDiagnoser().diagnose(report)
    policy = ReplanningPolicy(allowed_change_kinds=(CandidateChangeKind.SOLVER,))

    recommendation = ScientificReplanner().recommend(
        diagnosis,
        policy=policy,
    )

    assert recommendation.disposition is ReplanningDisposition.STOP
    assert recommendation.changes == ()
    assert recommendation.authorizes_execution is False


class _ExecutionFailureNeverCalled:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, plan, previous_request):
        del plan, previous_request
        self.calls += 1
        raise AssertionError("Execution failures are outside scientific replanning.")


def test_execution_failure_is_not_misclassified_as_scientific_replanning() -> None:
    request = _uncertain_energy_request().model_copy(
        update={
            "calculations": tuple(
                item.model_copy(update={"execution_successful": False})
                for item in _uncertain_energy_request().calculations
            )
        }
    )
    executor = _ExecutionFailureNeverCalled()

    run = ScientificReplanningLoop().run(request, executor=executor)

    assert executor.calls == 0
    assert run.disposition is ScientificReplanningRunDisposition.NOT_REPLANNABLE
    assert run.initial_diagnosis is not None
    assert not run.initial_diagnosis.execution_integrity_passed


class _PassingExecutor:
    def __init__(self) -> None:
        self.plans = []

    def execute(self, plan, previous_request):
        self.plans.append(plan)
        assert plan.baseline_request_sha256 == previous_request.fingerprint
        return _passing_energy_request()


def test_full_loop_verifies_diagnoses_replans_executes_and_compares() -> None:
    executor = _PassingExecutor()
    run = ScientificReplanningLoop().run(
        _uncertain_energy_request(),
        executor=executor,
    )

    assert run.disposition is ScientificReplanningRunDisposition.ACCEPTED_REPLAN
    assert len(executor.plans) == 1
    assert len(run.attempts) == 1
    attempt = run.attempts[0]
    assert attempt.comparison.outcome is EvidenceComparisonOutcome.PASSED
    assert attempt.comparison.candidate_preferred
    assert attempt.comparison.evidence_changed
    assert "verification.uncertainty.missing" in (
        attempt.comparison.resolved_finding_identifiers
    )
    assert run.final_report.overall_outcome is VerificationOutcome.PASSED
    assert run.accepted_plan_identifier == attempt.plan.candidate_plan_identifier


class _NeverCalledExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, plan, previous_request):
        del plan, previous_request
        self.calls += 1
        raise AssertionError("Authorization failure must stop before execution.")


def test_authorization_failure_never_executes_a_candidate() -> None:
    executor = _NeverCalledExecutor()
    request = _passing_energy_request(authorized=False)

    run = ScientificReplanningLoop().run(request, executor=executor)

    assert executor.calls == 0
    assert run.disposition is ScientificReplanningRunDisposition.AUTHORIZATION_REQUIRED
    assert run.initial_recommendation is not None
    assert (
        run.initial_recommendation.disposition
        is ReplanningDisposition.REQUIRE_AUTHORIZATION
    )
    assert run.initial_recommendation.authorizes_execution is False


class _SameEvidenceExecutor:
    def execute(self, plan, previous_request):
        del plan
        return previous_request


def test_loop_stops_when_new_candidate_does_not_improve_evidence() -> None:
    run = ScientificReplanningLoop().run(
        _uncertain_energy_request(),
        executor=_SameEvidenceExecutor(),
    )

    assert run.disposition is ScientificReplanningRunDisposition.NO_IMPROVEMENT
    assert len(run.attempts) == 1
    comparison = run.attempts[0].comparison
    assert comparison.outcome is EvidenceComparisonOutcome.UNCHANGED
    assert not comparison.candidate_preferred
    assert not comparison.evidence_changed


class _PersistentFailureExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, plan, previous_request):
        self.calls += 1
        return previous_request.model_copy(
            update={
                "request_identifier": f"persistent-request-{self.calls}",
            }
        )


def test_loop_is_bounded_and_chains_candidate_plans() -> None:
    executor = _PersistentFailureExecutor()
    loop = ScientificReplanningLoop(
        policy=ReplanningPolicy(
            max_candidate_generations=2,
            stop_on_no_improvement=False,
        )
    )

    run = loop.run(_uncertain_energy_request(), executor=executor)

    assert run.disposition is ScientificReplanningRunDisposition.EXHAUSTED
    assert executor.calls == 2
    assert len(run.attempts) == 2
    assert run.attempts[0].plan.parent_plan_identifier is None
    assert (
        run.attempts[1].plan.parent_plan_identifier
        == run.attempts[0].plan.candidate_plan_identifier
    )


class _WrongSubjectExecutor:
    def execute(self, plan, previous_request):
        del plan
        return previous_request.model_copy(
            update={"subject_identifier": "different-subject"}
        )


def test_candidate_executor_cannot_change_scientific_subject() -> None:
    with pytest.raises(
        ScientificCandidateExecutionError,
        match="scientific subject",
    ):
        ScientificReplanningLoop().run(
            _uncertain_energy_request(),
            executor=_WrongSubjectExecutor(),
        )


def test_evidence_comparison_rejects_authorization_regression() -> None:
    registry = default_scientific_verifier_registry()
    baseline_request = _uncertain_energy_request()
    candidate_request = _passing_energy_request(
        request_identifier="unauthorized-candidate",
        authorized=False,
    )
    baseline = registry.verify(baseline_request)
    unauthorized = registry.verify(candidate_request)

    comparison = compare_evidence(
        baseline_request,
        baseline,
        candidate_request,
        unauthorized,
    )

    assert comparison.outcome is EvidenceComparisonOutcome.REGRESSED
    assert not comparison.candidate_preferred
    assert comparison.evidence_changed
    assert "verification.authorization.missing" in (
        comparison.introduced_finding_identifiers
    )


class _RelaxedThresholdExecutor:
    def execute(self, plan, previous_request):
        del plan
        return previous_request.model_copy(
            update={
                "request_identifier": "relaxed-threshold-request",
                "minimum_resolvable_difference_hartree": 1e-9,
            }
        )


def test_candidate_executor_cannot_relax_verification_contract() -> None:
    with pytest.raises(
        ScientificCandidateExecutionError,
        match="verification contract",
    ):
        ScientificReplanningLoop().run(
            _uncertain_energy_request(),
            executor=_RelaxedThresholdExecutor(),
        )


def test_request_identifier_change_is_not_new_scientific_evidence() -> None:
    registry = default_scientific_verifier_registry()
    baseline_request = _uncertain_energy_request()
    candidate_request = baseline_request.model_copy(
        update={"request_identifier": "renamed-request-only"}
    )
    baseline_report = registry.verify(baseline_request)
    candidate_report = registry.verify(candidate_request)

    comparison = compare_evidence(
        baseline_request,
        baseline_report,
        candidate_request,
        candidate_report,
    )

    assert not comparison.evidence_changed
    assert comparison.outcome is EvidenceComparisonOutcome.UNCHANGED
    assert not comparison.candidate_preferred


def test_public_replanning_import_does_not_load_native_scientific_engines() -> None:
    program = """
import sys

import cgr.scientific_verification

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
    if any(
        name == prefix or name.startswith(prefix + ".")
        for prefix in prefixes
    )
)

if loaded:
    raise SystemExit(
        "native scientific engines loaded by public import: "
        + ", ".join(loaded)
    )
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
