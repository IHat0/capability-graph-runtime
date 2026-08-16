"""Deterministic scientific compute-routing tests."""

from __future__ import annotations

from cgr.pulsate_api.scientific_compute_routing import (
    HPC_BATCH,
    IBM_QUANTUM,
    LOCAL_CPU,
    LOCAL_GPU,
    LOCAL_SIMULATOR,
    ScientificComputeRouter,
)
from cgr.pulsate_api.scientific_executions import (
    ScientificExecutionRepository,
    ScientificObjectiveCompileRequest,
)
from cgr.pulsate_api.scientific_objectives import ScientificInputReference


def test_default_router_claims_only_real_local_targets() -> None:
    router = ScientificComputeRouter()
    snapshot = router.resource_snapshot()

    assert snapshot.available_execution_targets == (LOCAL_CPU, LOCAL_SIMULATOR)
    assert LOCAL_GPU in snapshot.unavailable_reasons
    assert HPC_BATCH in snapshot.unavailable_reasons
    assert IBM_QUANTUM in snapshot.unavailable_reasons

    route = router.route(
        "molecular.structure_analyze",
        requested_quantum_target="none",
        authorization_required=False,
        resources=snapshot,
    )
    assert route.execution_target == LOCAL_CPU
    assert route.available is True
    assert "local CPU" in route.reason


def test_configured_external_engine_selects_its_declared_target() -> None:
    router = ScientificComputeRouter(
        capability_execution_targets={
            "protein.backbone_generate": (LOCAL_GPU, HPC_BATCH),
        }
    )
    snapshot = router.resource_snapshot()
    route = router.route(
        "protein.backbone_generate",
        requested_quantum_target="none",
        authorization_required=False,
        resources=snapshot,
    )

    assert route.execution_target == LOCAL_GPU
    assert route.compatible_targets == (LOCAL_GPU, HPC_BATCH)
    assert route.available is True


def test_ibm_route_uses_live_availability_but_remains_an_authorization_boundary(
    tmp_path,
) -> None:
    router = ScientificComputeRouter(
        quantum_capability_provider=lambda: {
            "execution_targets": [LOCAL_SIMULATOR, IBM_QUANTUM],
            "ibm_quantum": {"available": True},
        }
    )
    repository = ScientificExecutionRepository(
        tmp_path / "science",
        compute_router=router,
    )
    repository.start()
    record = repository.create(
        ScientificObjectiveCompileRequest(
            question=(
                "Study the metal active site of this protein with VQE on IBM Quantum."
            ),
            input_references=(
                ScientificInputReference(
                    reference_identifier="protein-1",
                    artifact_type="protein_structure",
                    artifact_identifier="protein-artifact-1",
                ),
            ),
        )
    )

    assignments = {
        item.capability_name: item
        for item in record.canonical_plan.selected_assignments
    }
    assert assignments["quantum.vqe_execute"].execution_target == LOCAL_SIMULATOR
    authorization = assignments["quantum.ibm_execution_prepare"]
    assert authorization.execution_target == IBM_QUANTUM
    assert authorization.feasibility.status.value == "conditional"
    assert authorization.feasibility.required_approvals == (
        "approval.ibm-quantum-submission",
    )
    assert record.status == "authorization_required"
    assert record.plan.ibm_job_submission_planned is False


def test_compute_routing_cannot_be_rebound_after_start(tmp_path) -> None:
    repository = ScientificExecutionRepository(tmp_path / "science")
    repository.start()

    try:
        repository.bind_compute_router(ScientificComputeRouter())
    except RuntimeError as exc:
        assert "cannot change" in str(exc)
    else:
        raise AssertionError("A started repository accepted a new compute router.")
