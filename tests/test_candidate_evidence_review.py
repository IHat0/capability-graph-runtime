"""No statistical certainty is manufactured from point docking scores."""
from cgr.pulsate_api.scientific_candidate_evidence import docking_confidence


def test_point_scores_have_no_invented_ranking_confidence():
    document = {"candidates": [
        {"selection": {"rank": rank}, "properties_and_calculations": [
            {"objective_identifier": "vina_pose_score", "value": value, "uncertainty": None}]}
        for rank, value in enumerate((-4.8, -4.1, -4.0), 1)
    ]}
    answer = " ".join(docking_confidence(document))
    assert "0.1000 kcal/mol" in answer
    assert "No calibrated ranking confidence" in answer
    assert "not measured affinity" in answer
    assert "have not been quantified" in answer


def test_non_docking_evidence_does_not_get_docking_claims():
    assert docking_confidence({"candidates": []}) == ()


import json
from types import SimpleNamespace
from cgr.pulsate_api.scientific_candidate_evidence import review_candidate_evidence


def _review(question, ranks=(), metrics=(), previous=None, reuse=False, extra=None):
    document = {"candidates": [
        {"candidate_identifier": f"candidate-{rank}", "display_name": f"label-{rank}",
         "selection": {"rank": rank}, "properties_and_calculations": [
             {"objective_identifier": "vina_pose_score", "value": energy, "uncertainty": None},
             {"objective_identifier": "drug_likeness", "value": descriptor, "uncertainty": None}]}
        for rank, energy, descriptor in ((1, -6.0, 0.4), (2, -5.0, 0.5), (3, -4.0, 0.9))
    ]}
    campaign = {"objectives": [
        {"objective_identifier": "vina_pose_score", "weight": 1, "direction": "minimize"},
        {"objective_identifier": "drug_likeness", "weight": 0, "direction": "maximize"}]}
    query = {"action": "compare", "candidate_ranks": ranks, "reuse_previous_selection": reuse,
             "metric_identifiers": metrics, "supporting_quote": question, **(extra or {})}
    provider = SimpleNamespace(complete=lambda messages: json.dumps(query))
    return review_candidate_evidence(document=document, campaign=campaign, question=question,
        provider=provider, execution_identifier="execution-original",
        references=(SimpleNamespace(artifact_identifier="trace-original", content_sha256="a"*64),),
        previous=previous)


def test_ordinal_comparison_and_persistent_subset_use_only_existing_scores():
    first = _review("Explain the difference between ranks one and three.", ranks=(1, 3))
    assert first.candidate_identifiers == ("candidate-1", "candidate-3")
    assert first.favored_candidate_identifiers == ("candidate-1",)
    assert "+2.0000 kcal/mol" in first.response
    assert "zero weight to drug_likeness" in first.response
    second = _review("Reconsider that subset using docking scores alone.",
                     metrics=("vina_pose_score",), previous=first, reuse=True)
    assert second.candidate_identifiers == first.candidate_identifiers
    assert second.conclusion_changed is False
    assert second.new_scientific_computation is False
    assert second.source_artifact_sha256 == ("a"*64,)


def test_nonexistent_candidate_reference_asks_instead_of_inventing():
    result = _review("Compare an unavailable rank.", ranks=(99,))
    assert not result.candidate_identifiers
    assert "could not resolve" in result.response


def test_model_cannot_inject_a_scientific_answer():
    result = _review("Compare the candidates.", extra={"answer": "invented outcome"})
    assert not result.candidate_identifiers
    assert "invented outcome" not in result.response


def test_previous_subset_is_required_for_anaphoric_reference():
    result = _review("Compare those candidates.", reuse=True)
    assert "could not resolve" in result.response


def test_a_different_requested_metric_can_change_the_favored_candidate():
    original = _review("Compare the outer ranks.", ranks=(1, 3))
    changed = _review("Use the descriptor alone for that subset.",
                      metrics=("drug_likeness",), previous=original, reuse=True)
    assert changed.favored_candidate_identifiers == ("candidate-3",)
    assert changed.conclusion_changed is True


def test_multiple_metrics_report_tradeoffs_instead_of_inventing_weights():
    result = _review("Compare all recorded objectives.",
                     metrics=("vina_pose_score", "drug_likeness"))
    assert result.candidate_identifiers == ("candidate-1", "candidate-2", "candidate-3")
    assert len(result.favored_candidate_identifiers) == 3
    assert "do not identify a unique winner" in result.response


def test_unrecorded_metric_cannot_be_invented():
    result = _review("Compare an absent measurement.", metrics=("measured_affinity",))
    assert not result.candidate_identifiers
    assert "could not resolve" in result.response

def test_completed_session_reviews_preserve_execution_result_and_tenant(tmp_path):
    import hashlib
    from datetime import timedelta
    import pytest
    from test_research_sessions import _controller
    from test_research_session_progress import _planned
    from cgr.pulsate_api.research_sessions import ResearchSessionReplyRequest
    from cgr.pulsate_api.scientific_executions import ScientistFacingResult
    controller = _controller(tmp_path)
    session, tenant = _planned(controller)
    result = ScientistFacingResult(
        original_request="Original computation", resolved_interpretation="Original interpretation",
        structures_and_entities=(), methods=(), assumptions=(), scientific_result="Original answer",
        verification_status="passed", uncertainty=(), replanning_history=(),
        important_limitations=(), evidence_artifact_identifiers=(), scene_identifiers=())
    original = controller.execution_repository.get(session.compilation.execution_identifier)
    original = controller.execution_repository.replace(original.model_copy(update={
        "status": "succeeded", "verified": True, "scientist_result": result,
        "result_artifact_identifiers": (session.input_references[0].artifact_identifier,),
        "updated_at": original.updated_at + timedelta(microseconds=1),
    }), expected_updated_at=original.updated_at)
    completed = controller.repository.replace(session.model_copy(update={
        "status": "completed", "scientist_result": result, "revision": session.revision + 1,
        "updated_at": session.updated_at + timedelta(microseconds=1),
    }), expected_revision=session.revision)
    reviews = []
    def review(record, question, previous):
        reviews.append(previous)
        return _review(question, ranks=(1, 3) if previous is None else (),
                       previous=previous, reuse=previous is not None)
    controller.runtime = SimpleNamespace(capability_registry=SimpleNamespace(
        get=lambda name: SimpleNamespace(review_candidate_evidence=review)))
    for question in ("Explain the rank difference.", "Reconsider that subset."):
        latest = controller.reply(session.session_identifier,
            ResearchSessionReplyRequest(message=question), tenant_identifier_sha256=tenant)
        assert latest.status == "completed"
        assert latest.scientist_result == result
        assert latest.compilation == completed.compilation
        assert controller.execution_repository.get(original.execution_identifier) == original
    reopened = controller.get(session.session_identifier, tenant_identifier_sha256=tenant)
    assert len(reopened.evidence_reviews) == 2
    assert reviews[0] is None
    assert reviews[1].candidate_identifiers == ("candidate-1", "candidate-3")
    assert reopened.conversation[-1].role == "pulsate"
    with pytest.raises(KeyError):
        controller.reply(session.session_identifier, ResearchSessionReplyRequest(message="Explain"),
                         tenant_identifier_sha256=hashlib.sha256(b"other").hexdigest())
