"""Bridges from Phase 6 verification and replanning evidence into discovery records."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .contracts import (
    CandidateVerificationRecord,
    DiscoveryCandidate,
    DiscoveryDiagnosisReference,
    DiscoveryReplanningReference,
)

if TYPE_CHECKING:
    from cgr.scientific_verification import (
        ReplanningRecommendation,
        ScientificFailureDiagnosis,
        ScientificVerificationReport,
    )


def diagnosis_reference(
    diagnosis: ScientificFailureDiagnosis,
) -> DiscoveryDiagnosisReference:
    """Project one immutable Phase 6 diagnosis into candidate-generation context."""

    return DiscoveryDiagnosisReference(
        diagnosis_identifier=diagnosis.diagnosis_identifier,
        diagnosis_sha256=diagnosis.fingerprint,
        report_identifier=diagnosis.report_identifier,
        report_sha256=diagnosis.report_sha256,
        replannable=diagnosis.replannable,
        summary=diagnosis.summary,
    )


def replanning_reference(
    recommendation: ReplanningRecommendation,
) -> DiscoveryReplanningReference:
    """Project one Phase 6 recommendation without inheriting authorization."""

    return DiscoveryReplanningReference(
        recommendation_identifier=recommendation.recommendation_identifier,
        recommendation_sha256=recommendation.fingerprint,
        diagnosis_identifier=recommendation.diagnosis_identifier,
        disposition=recommendation.disposition.value,
        change_identifiers=tuple(
            change.change_identifier for change in recommendation.changes
        ),
        requires_fresh_authorization=recommendation.requires_fresh_authorization,
    )


def verification_record(
    candidate: DiscoveryCandidate,
    report: ScientificVerificationReport,
) -> CandidateVerificationRecord:
    """Attach Phase 6 verification evidence to a candidate without mutating it."""

    return CandidateVerificationRecord(
        candidate_identifier=candidate.candidate_identifier,
        candidate_sha256=candidate.fingerprint,
        report_identifier=report.report_identifier,
        report_sha256=report.fingerprint,
        objective_family=report.objective_family,
        subject_identifier=report.subject_identifier,
        request_sha256=report.request_sha256,
        overall_outcome=report.overall_outcome.value,
        execution_integrity_passed=report.execution_integrity_passed,
        scientific_quality_passed=report.scientific_quality_passed,
        authorization_passed=report.authorization_passed,
    )
