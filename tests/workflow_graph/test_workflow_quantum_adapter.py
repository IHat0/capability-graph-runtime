"""Verified execution-spine adapter tests; no external quantum work is submitted."""

from __future__ import annotations

from typing import Any

from cgr.pulsate_api.workflow_quantum import RunCoordinatorWorkflowAdapter
from cgr.workflow_graph import (
    CapabilityInvocation,
    CapabilityInvocationStatus,
    FrozenDict,
    NodeKind,
)


class FakeCoordinator:
    def __init__(self, statuses: list[str]) -> None:
        self.statuses = statuses
        self.create_calls: list[dict[str, Any]] = []

    def create(
        self,
        preset_identifier: str | None,
        execution_target: str,
        idempotency_key: str | None,
        *,
        experiment_identifier: str | None = None,
        approved_experiment_identifier: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        self.create_calls.append(
            {
                "preset": preset_identifier,
                "target": execution_target,
                "idempotency": idempotency_key,
                "experiment": experiment_identifier,
                "approved": approved_experiment_identifier,
            }
        )
        return {"run_identifier": "run-" + "a" * 32, "status": self.statuses[0]}, True

    def get(self, run_identifier: str) -> dict[str, Any]:
        assert run_identifier == "run-" + "a" * 32
        if len(self.statuses) > 1:
            self.statuses.pop(0)
        return {"run_identifier": run_identifier, "status": self.statuses[0]}

    def artifact(self, run_identifier: str, name: str) -> dict[str, Any]:
        return {"run_identifier": run_identifier, "name": name, "verified": True}


def invocation(*, ibm: bool, approved: bool, parameters: FrozenDict) -> CapabilityInvocation:
    return CapabilityInvocation(
        invocation_identifier="invocation.quantum",
        idempotency_identifier="idempotency.quantum",
        graph_run_identifier="workflow-run.quantum",
        graph_definition_fingerprint="a" * 64,
        node_identifier="node.quantum",
        attempt_number=1,
        capability_identity="capability.quantum",
        node_kind=NodeKind.QUANTUM_IBM if ibm else NodeKind.QUANTUM_LOCAL,
        execution_target="ibm_quantum" if ibm else "local_simulator",
        approved=approved,
        parameters=parameters,
    )


def test_local_quantum_reuses_existing_spine_and_returns_evidence_fingerprints() -> None:
    coordinator = FakeCoordinator(["queued", "authorized"])
    adapter = RunCoordinatorWorkflowAdapter(
        coordinator,
        sleeper=lambda _seconds: None,
    )
    result = adapter.invoke(
        invocation(
            ibm=False,
            approved=False,
            parameters=FrozenDict({"preset_identifier": "h2-minimal"}),
        )
    )
    assert result.status is CapabilityInvocationStatus.SUCCEEDED
    assert result.verification_classification == "authorized"
    assert result.output_values["receipt_fingerprint"]
    assert coordinator.create_calls[0]["target"] == "local_simulator"


def test_ibm_fails_closed_before_coordinator_without_bound_approval() -> None:
    coordinator = FakeCoordinator(["authorized"])
    adapter = RunCoordinatorWorkflowAdapter(coordinator)
    result = adapter.invoke(
        invocation(
            ibm=True,
            approved=False,
            parameters=FrozenDict(
                {"approved_experiment_identifier": "approved-experiment.one"}
            ),
        )
    )
    assert result.status is CapabilityInvocationStatus.BLOCKED
    assert result.error_code == "ibm_workflow_approval_missing"
    assert coordinator.create_calls == []


def test_ibm_requires_approved_experiment_source_even_when_node_is_approved() -> None:
    coordinator = FakeCoordinator(["authorized"])
    adapter = RunCoordinatorWorkflowAdapter(coordinator)
    result = adapter.invoke(
        invocation(
            ibm=True,
            approved=True,
            parameters=FrozenDict({"preset_identifier": "h2-minimal"}),
        )
    )
    assert result.status is CapabilityInvocationStatus.BLOCKED
    assert result.error_code == "approved_experiment_required"
    assert coordinator.create_calls == []
