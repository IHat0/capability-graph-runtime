from __future__ import annotations

from cgr.discovery import (
    CandidateEvaluatorRegistry,
    CandidateGeneratorRegistry,
    CandidateValidityCheckerRegistry,
    DiscoveryCampaign,
    DiscoveryCandidate,
    DiscoveryObjective,
    ObjectiveDirection,
)
from cgr.science import ArtifactPointer


def _hash(character: str) -> str:
    return character * 64


def test_discovery_foundation_is_capability_routed_not_domain_pipeline() -> None:
    campaign = DiscoveryCampaign(
        campaign_identifier="campaign.dispatch",
        description="Universal discovery routing contract.",
        objectives=(
            DiscoveryObjective(
                objective_identifier="quality",
                metric_identifier="metric.quality",
                direction=ObjectiveDirection.MAXIMIZE,
            ),
        ),
        permitted_candidate_types=(
            "protein_sequence",
            "reaction_path",
            "future_scientific_candidate",
        ),
    )
    assert campaign.accepts_candidate_type("protein_sequence")
    assert campaign.accepts_candidate_type("reaction_path")
    assert campaign.accepts_candidate_type("future_scientific_candidate")

    candidate = DiscoveryCandidate(
        candidate_identifier="candidate.dispatch",
        candidate_type="future_scientific_candidate",
        generation=0,
        generated_by="generator.external",
        representation_artifacts=(
            ArtifactPointer(
                artifact_identifier="artifact.dispatch",
                content_sha256=_hash("a"),
            ),
        ),
        objective_identifiers=("quality",),
    )
    assert candidate.candidate_type == "future_scientific_candidate"
    assert candidate.authorizes_execution is False

    assert CandidateGeneratorRegistry().resolve(candidate.candidate_type) == ()
    assert CandidateValidityCheckerRegistry().resolve(candidate.candidate_type) == ()
    assert CandidateEvaluatorRegistry().resolve(candidate.candidate_type) == ()
