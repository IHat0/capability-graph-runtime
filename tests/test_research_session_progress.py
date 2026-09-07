"""Session visibility and concurrency around long scientific calculations."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import threading

import pytest

from cgr.pulsate_api.research_sessions import (
    ResearchSessionCreateRequest, ResearchSessionReplyRequest,
)
from test_research_sessions import _controller, _input
from cgr.science import ArtifactReference, CreationProvenance
from cgr.kernel.contracts import CapabilityVersion


def _planned(controller):
    payload = b"test coordinates"
    source = ArtifactReference(
        artifact_identifier=_input().artifact_identifier,
        schema_version=CapabilityVersion(major=1, minor=0, patch=0),
        artifact_type="molecular_structure",
        media_type="application/octet-stream",
        content_sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        provenance=CreationProvenance(
            producer="session-progress-tests",
            producer_version=CapabilityVersion(major=1, minor=0, patch=0),
        ),
    )
    tenant = hashlib.sha256(b"progress-tenant").hexdigest()
    session = controller.create(
        ResearchSessionCreateRequest(
            question="Which conformer is preferred: axial or equatorial in water?",
            input_references=(_input(),), artifact_references=(source,),
        ),
        tenant_identifier_sha256=tenant,
    )
    assert session.status == "planned"
    return session, tenant


def test_progress_is_readable_and_duplicate_work_is_rejected(tmp_path):
    controller = _controller(tmp_path)
    session, tenant = _planned(controller)
    entered, release = threading.Event(), threading.Event()

    class BlockingRuntime:
        def execute(self, identifier):
            repository = controller.execution_repository
            record = repository.get(identifier)
            node = record.node_executions[0].model_copy(update={
                "status": "running", "attempt_count": 1,
            })
            running = record.model_copy(update={
                "status": "running",
                "updated_at": record.updated_at + timedelta(microseconds=1),
                "node_executions": (node, *record.node_executions[1:]),
            })
            repository.replace(running, expected_updated_at=record.updated_at)
            entered.set()
            assert release.wait(10)
            failed = running.model_copy(update={
                "status": "failed",
                "updated_at": running.updated_at + timedelta(microseconds=1),
                "scientist_summary": "Controlled test failure.",
                "node_executions": (node.model_copy(update={
                    "status": "failed", "error_code": "test_failure",
                    "error_message": "Controlled test failure.",
                }), *record.node_executions[1:]),
            })
            return repository.replace(failed, expected_updated_at=running.updated_at)

    controller.runtime = BlockingRuntime()
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(controller.execute, session.session_identifier,
                             tenant_identifier_sha256=tenant)
        try:
            assert entered.wait(10)
            visible = controller.get(session.session_identifier, tenant_identifier_sha256=tenant)
            assert visible.status == "running"
            assert visible.execution_steps[0].status == "running"
            with pytest.raises(ValueError):
                controller.execute(session.session_identifier, tenant_identifier_sha256=tenant)
            with pytest.raises(ValueError, match="Research is running"):
                controller.reply(session.session_identifier,
                    ResearchSessionReplyRequest(message="Change the conditions."),
                    tenant_identifier_sha256=tenant)
            with pytest.raises(KeyError):
                controller.get(session.session_identifier,
                               tenant_identifier_sha256=hashlib.sha256(b"other").hexdigest())
        finally:
            release.set()
        failed = future.result(timeout=10)
    assert failed.status == "failed"
    assert failed.execution_steps[0].error_message == "Controlled test failure."
    assert failed.scientist_result is None
    assert controller.repository.get(session.session_identifier).status == "failed"


def test_preflight_exception_restores_plan_and_preserves_question(tmp_path):
    controller = _controller(tmp_path)
    session, tenant = _planned(controller)

    class UnavailableRuntime:
        def execute(self, identifier):
            raise RuntimeError("Runtime unavailable.")

    controller.runtime = UnavailableRuntime()
    with pytest.raises(RuntimeError, match="Runtime unavailable"):
        controller.execute(session.session_identifier, tenant_identifier_sha256=tenant)
    restored = controller.get(session.session_identifier, tenant_identifier_sha256=tenant)
    assert restored.status == "planned"
    assert restored.conversation == session.conversation



def test_missing_executor_is_not_reported_as_an_unclear_question(tmp_path):
    from cgr.pulsate_api.scientific_capability_catalogue import phase8_scientific_capability_catalogue
    from cgr.pulsate_api.scientific_capability_truth import ScientificCapabilityTruthCatalogue

    controller = _controller(tmp_path)
    controller.execution_repository.close()
    controller.execution_repository.bind_capability_truth(
        ScientificCapabilityTruthCatalogue.from_runtime(
            catalogue=phase8_scientific_capability_catalogue(),
            registered_capability_names=(),
        )
    )
    controller.execution_repository.start()
    session = controller.create(
        ResearchSessionCreateRequest(question="Compare axial and equatorial conformers in water."),
        tenant_identifier_sha256=hashlib.sha256(b"tenant").hexdigest(),
    )
    assert session.compilation is None
    assert session.next_questions[0].requirement_identifier == "requirement-execution-capability"
    assert "platform configuration limitation" in session.next_questions[0].question
    assert "not missing scientific information" in session.next_questions[0].question


@pytest.mark.parametrize("extra_operation,extra_output", [
    ("analyze_structure", "structure_analysis"),
    ("calculate_vqe_ground_state_energy", "variational_ground_state_energy"),
    ("generate_protein_candidates", "generated_protein_candidates"),
])
def test_mixed_operations_are_never_silently_dropped(extra_operation, extra_output):
    from cgr.pulsate_api.scientific_requirements import (
        ScientificRequirementProposal, validate_requirement_proposal,
    )
    proposal = ScientificRequirementProposal(
        proposal_identifier="requirement-proposal-mixed",
        requirements=(
            {"operation": "dock_candidates", "requested_output": "docking_evidence",
             "supporting_quote": "Dock the candidates"},
            {"operation": extra_operation, "requested_output": extra_output,
             "supporting_quote": "and perform the additional analysis"},
        ),
        summary="Two requested operations.",
        provider_kind="test", model_name="test",
    )
    with pytest.raises(ValueError, match="all requested outputs"):
        validate_requirement_proposal(proposal, available_input_types=())
