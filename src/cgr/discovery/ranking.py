"""Deterministic Pareto ranking and transparent multi-objective selection."""

from __future__ import annotations

from collections.abc import Iterable

from cgr.science.canonical import sha256_fingerprint

from .contracts import (
    CandidateAssessment,
    CandidateObjectiveScore,
    CandidateSelectionDecision,
    DiscoveryCampaign,
    DiscoveryObjective,
    MultiObjectiveRanking,
    ObjectiveDirection,
    RankedDiscoveryCandidate,
    SelectionOutcome,
    SelectionPolicy,
)


def _objective_map(campaign: DiscoveryCampaign) -> dict[str, DiscoveryObjective]:
    return {item.objective_identifier: item for item in campaign.objectives}


def _score_map(assessment: CandidateAssessment) -> dict[str, CandidateObjectiveScore]:
    return {item.objective_identifier: item for item in assessment.objective_scores}


def pareto_dominates(
    left: CandidateAssessment,
    right: CandidateAssessment,
    objectives: tuple[DiscoveryObjective, ...],
) -> bool:
    """Return whether left is no worse on all objectives and better on at least one."""

    left_scores = _score_map(left)
    right_scores = _score_map(right)
    objective_ids = {item.objective_identifier for item in objectives}
    if not objective_ids.issubset(left_scores) or not objective_ids.issubset(
        right_scores
    ):
        raise ValueError("Pareto comparison requires complete objective score vectors.")

    strictly_better = False
    for objective in objectives:
        left_value = left_scores[objective.objective_identifier].value
        right_value = right_scores[objective.objective_identifier].value
        if objective.direction is ObjectiveDirection.MAXIMIZE:
            if left_value < right_value:
                return False
            strictly_better = strictly_better or left_value > right_value
        else:
            if left_value > right_value:
                return False
            strictly_better = strictly_better or left_value < right_value
    return strictly_better


def pareto_fronts(
    assessments: Iterable[CandidateAssessment],
    objectives: tuple[DiscoveryObjective, ...],
) -> tuple[tuple[CandidateAssessment, ...], ...]:
    """Return deterministic non-dominated fronts for complete candidate assessments."""

    materialized = tuple(assessments)
    remaining = {item.candidate_identifier: item for item in materialized}
    if len(remaining) != len(materialized):
        raise ValueError("Pareto ranking candidate identifiers must be unique.")
    fronts: list[tuple[CandidateAssessment, ...]] = []
    while remaining:
        current: list[CandidateAssessment] = []
        for candidate_id in sorted(remaining):
            candidate = remaining[candidate_id]
            dominated = any(
                other_id != candidate_id
                and pareto_dominates(other, candidate, objectives)
                for other_id, other in remaining.items()
            )
            if not dominated:
                current.append(candidate)
        if not current:
            raise RuntimeError("Pareto ranking could not make progress.")
        ordered = tuple(sorted(current, key=lambda item: item.candidate_identifier))
        fronts.append(ordered)
        for item in ordered:
            del remaining[item.candidate_identifier]
    return tuple(fronts)


def _composite_score(
    assessment: CandidateAssessment,
    objectives: tuple[DiscoveryObjective, ...],
) -> float | None:
    scores = _score_map(assessment)
    weighted = [
        (
            objective.weight,
            scores[objective.objective_identifier].normalized_value,
        )
        for objective in objectives
        if objective.weight > 0
    ]
    denominator = sum(weight for weight, _ in weighted)
    if denominator == 0:
        return None
    return sum(weight * value for weight, value in weighted) / denominator


def _eligible(
    campaign: DiscoveryCampaign,
    assessment: CandidateAssessment,
    policy: SelectionPolicy,
) -> tuple[bool, str]:
    if assessment.has_hard_constraint_failure:
        return False, "Candidate failed at least one explicit hard constraint."
    if policy.require_scientific_quality and not assessment.scientific_quality_passed:
        return False, "Candidate lacks passing Phase 6 scientific-quality evidence."

    objectives = _objective_map(campaign)
    scores = _score_map(assessment)
    objective_ids = {
        objective.objective_identifier for objective in campaign.objectives
    }
    if policy.require_complete_objectives and set(scores) != objective_ids:
        return (
            False,
            "Candidate does not provide the complete campaign objective vector.",
        )

    for objective_id, score in scores.items():
        objective = objectives.get(objective_id)
        if objective is None:
            return (
                False,
                "Candidate contains a score outside the campaign objective contract.",
            )
        if objective.hard_constraint and not score.satisfies_objective:
            return False, "Candidate failed a hard objective constraint."
        if objective.require_uncertainty and score.uncertainty is None:
            return (
                False,
                "Candidate is missing required objective uncertainty evidence.",
            )
        if (
            objective.maximum_uncertainty is not None
            and score.uncertainty is not None
            and score.uncertainty > objective.maximum_uncertainty
        ):
            return False, "Candidate objective uncertainty exceeds the campaign limit."
    return True, "Candidate satisfies ranking eligibility requirements."


def rank_candidates(
    campaign: DiscoveryCampaign,
    assessments: tuple[CandidateAssessment, ...],
    *,
    policy: SelectionPolicy | None = None,
) -> MultiObjectiveRanking:
    """Rank one immutable campaign cohort without authorizing candidate execution."""

    policy = policy or SelectionPolicy()
    if len({item.candidate_identifier for item in assessments}) != len(assessments):
        raise ValueError(
            "Discovery ranking assessments require unique candidate identifiers."
        )

    eligible: list[CandidateAssessment] = []
    ineligible: list[tuple[CandidateAssessment, str]] = []
    for assessment in assessments:
        accepted, rationale = _eligible(campaign, assessment, policy)
        if accepted:
            eligible.append(assessment)
        else:
            ineligible.append((assessment, rationale))

    objectives = campaign.objectives
    # Pareto comparison itself requires all campaign objectives, not merely required ones.
    for assessment in eligible:
        score_ids = {item.objective_identifier for item in assessment.objective_scores}
        objective_ids = {item.objective_identifier for item in objectives}
        if score_ids != objective_ids:
            raise ValueError(
                "Eligible Pareto candidates require exactly one score for every campaign objective."
            )

    fronts = pareto_fronts(tuple(eligible), objectives) if eligible else ()
    front_by_candidate = {
        assessment.candidate_identifier: index
        for index, front in enumerate(fronts)
        for assessment in front
    }
    composite_by_candidate = {
        assessment.candidate_identifier: _composite_score(assessment, objectives)
        for assessment in eligible
    }

    def order_key(assessment: CandidateAssessment) -> tuple[int, int, float, str]:
        composite = composite_by_candidate[assessment.candidate_identifier]
        if policy.use_composite_tiebreak and composite is not None:
            return (
                front_by_candidate[assessment.candidate_identifier],
                0,
                -composite,
                assessment.candidate_identifier,
            )
        return (
            front_by_candidate[assessment.candidate_identifier],
            1,
            0.0,
            assessment.candidate_identifier,
        )

    ordered = sorted(eligible, key=order_key)
    ranked: list[RankedDiscoveryCandidate] = []
    for index, assessment in enumerate(ordered, start=1):
        ranked.append(
            RankedDiscoveryCandidate(
                candidate_identifier=assessment.candidate_identifier,
                candidate_sha256=assessment.candidate_sha256,
                assessment_identifier=assessment.assessment_identifier,
                assessment_sha256=assessment.fingerprint,
                pareto_front=front_by_candidate[assessment.candidate_identifier],
                rank=index,
                composite_score=composite_by_candidate[assessment.candidate_identifier],
                objective_scores=assessment.objective_scores,
            )
        )

    selectable = [
        item
        for item in ranked
        if not policy.select_only_first_pareto_front or item.pareto_front == 0
    ][: policy.max_selected_candidates]
    selected_ids = {item.candidate_identifier for item in selectable}

    decisions: list[CandidateSelectionDecision] = []
    for item in ranked:
        assessment = next(
            value
            for value in eligible
            if value.candidate_identifier == item.candidate_identifier
        )
        selected = item.candidate_identifier in selected_ids
        decisions.append(
            CandidateSelectionDecision(
                decision_identifier="selection-"
                + sha256_fingerprint(
                    {
                        "campaign": campaign.fingerprint,
                        "candidate": item.candidate_sha256,
                        "assessment": assessment.fingerprint,
                        "rank": item.rank,
                        "selected": selected,
                    }
                )[:32],
                campaign_identifier=campaign.campaign_identifier,
                campaign_sha256=campaign.fingerprint,
                candidate_identifier=item.candidate_identifier,
                candidate_sha256=item.candidate_sha256,
                assessment_identifier=assessment.assessment_identifier,
                assessment_sha256=assessment.fingerprint,
                outcome=(
                    SelectionOutcome.SELECTED if selected else SelectionOutcome.REJECTED
                ),
                rank=item.rank,
                pareto_front=item.pareto_front,
                composite_score=item.composite_score,
                rationale=(
                    "Candidate selected from deterministic Pareto ranking."
                    if selected
                    else "Candidate was eligible but not selected under the selection policy."
                ),
            )
        )

    for assessment, rationale in sorted(
        ineligible,
        key=lambda item: item[0].candidate_identifier,
    ):
        decisions.append(
            CandidateSelectionDecision(
                decision_identifier="selection-"
                + sha256_fingerprint(
                    {
                        "campaign": campaign.fingerprint,
                        "candidate": assessment.candidate_sha256,
                        "assessment": assessment.fingerprint,
                        "outcome": SelectionOutcome.INELIGIBLE.value,
                    }
                )[:32],
                campaign_identifier=campaign.campaign_identifier,
                campaign_sha256=campaign.fingerprint,
                candidate_identifier=assessment.candidate_identifier,
                candidate_sha256=assessment.candidate_sha256,
                assessment_identifier=assessment.assessment_identifier,
                assessment_sha256=assessment.fingerprint,
                outcome=SelectionOutcome.INELIGIBLE,
                rationale=rationale,
            )
        )

    ranking_identifier = (
        "discovery-ranking-"
        + sha256_fingerprint(
            {
                "campaign": campaign.fingerprint,
                "assessments": tuple(sorted(item.fingerprint for item in assessments)),
                "policy": policy.fingerprint,
            }
        )[:32]
    )

    return MultiObjectiveRanking(
        ranking_identifier=ranking_identifier,
        campaign_identifier=campaign.campaign_identifier,
        campaign_sha256=campaign.fingerprint,
        ranked_candidates=tuple(ranked),
        pareto_fronts=tuple(
            tuple(item.candidate_identifier for item in front) for front in fronts
        ),
        decisions=tuple(sorted(decisions, key=lambda item: item.candidate_identifier)),
        ineligible_candidate_identifiers=tuple(
            item.candidate_identifier for item, _ in ineligible
        ),
    )
