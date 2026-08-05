"""Adapter from Phase 4 workflow nodes to the existing verified run coordinator."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from cgr.science.canonical import sha256_fingerprint
from cgr.workflow_graph import (
    CapabilityInvocation,
    CapabilityInvocationResult,
    CapabilityInvocationStatus,
    FrozenDict,
    NodeKind,
)


class WorkflowRunCoordinator(Protocol):
    """Narrow existing-execution-spine interface used by the workflow adapter."""

    def create(
        self,
        preset_identifier: str | None,
        execution_target: str,
        idempotency_key: str | None,
        *,
        experiment_identifier: str | None = None,
        approved_experiment_identifier: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        ...

    def get(self, run_identifier: str) -> dict[str, Any]:
        ...

    def artifact(
        self, run_identifier: str, name: str
    ) -> dict[str, Any]:
        ...


class RunCoordinatorWorkflowAdapter:
    """Synchronous bounded wrapper around Pulsate's existing verified run spine.

    This adapter does not bypass experiment resolution, local/IBM preflight,
    approval provenance, idempotency, verification, receipts, or worker
    isolation. It merely lets an already-authorized workflow node reuse that
    existing boundary.
    """

    _TERMINAL = frozenset({"authorized", "rejected", "failed", "interrupted"})

    def __init__(
        self,
        coordinator: WorkflowRunCoordinator,
        *,
        poll_interval_seconds: float = 0.05,
        default_timeout_seconds: float = 180.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if poll_interval_seconds <= 0 or poll_interval_seconds > 5:
            raise ValueError("Workflow run polling interval is outside its bounds.")
        if default_timeout_seconds <= 0 or default_timeout_seconds > 3600:
            raise ValueError("Workflow run timeout is outside its bounds.")
        self.coordinator = coordinator
        self.poll_interval_seconds = poll_interval_seconds
        self.default_timeout_seconds = default_timeout_seconds
        self.monotonic = monotonic
        self.sleeper = sleeper

    def invoke(self, invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        if invocation.cancellation_requested:
            return self._failure(
                invocation,
                status=CapabilityInvocationStatus.CANCELLED,
                code="workflow_invocation_cancelled",
                message="Workflow quantum invocation was cancelled before submission.",
            )
        if invocation.node_kind not in {NodeKind.QUANTUM_LOCAL, NodeKind.QUANTUM_IBM}:
            return self._failure(
                invocation,
                status=CapabilityInvocationStatus.BLOCKED,
                code="invalid_quantum_node_kind",
                message="The run-coordinator adapter only accepts quantum workflow nodes.",
            )
        target = (
            "ibm_quantum"
            if invocation.node_kind is NodeKind.QUANTUM_IBM
            else "local_simulator"
        )
        if invocation.execution_target is not None and invocation.execution_target != target:
            return self._failure(
                invocation,
                status=CapabilityInvocationStatus.BLOCKED,
                code="execution_target_mismatch",
                message="Workflow node target does not match the verified execution spine.",
            )
        if invocation.node_kind is NodeKind.QUANTUM_IBM and not invocation.approved:
            return self._failure(
                invocation,
                status=CapabilityInvocationStatus.BLOCKED,
                code="ibm_workflow_approval_missing",
                message="IBM Quantum workflow execution requires bound approval evidence.",
            )

        parameters = dict(invocation.parameters)
        preset = self._optional_identifier(parameters, "preset_identifier")
        experiment = self._optional_identifier(parameters, "experiment_identifier")
        approved = self._optional_identifier(
            parameters, "approved_experiment_identifier"
        )
        if sum(item is not None for item in (preset, experiment, approved)) != 1:
            return self._failure(
                invocation,
                status=CapabilityInvocationStatus.BLOCKED,
                code="experiment_source_invalid",
                message="Quantum workflow nodes require exactly one experiment source.",
            )
        if invocation.node_kind is NodeKind.QUANTUM_IBM and approved is None:
            return self._failure(
                invocation,
                status=CapabilityInvocationStatus.BLOCKED,
                code="approved_experiment_required",
                message="IBM Quantum workflow nodes require an approved experiment source.",
            )

        try:
            state, _created = self.coordinator.create(
                preset,
                target,
                invocation.idempotency_identifier,
                experiment_identifier=experiment,
                approved_experiment_identifier=approved,
            )
            run_identifier = state.get("run_identifier")
            if not isinstance(run_identifier, str):
                raise ValueError("missing run identity")
            timeout = invocation.timeout_seconds or self.default_timeout_seconds
            deadline = self.monotonic() + timeout
            while state.get("status") not in self._TERMINAL:
                if self.monotonic() >= deadline:
                    return self._failure(
                        invocation,
                        status=CapabilityInvocationStatus.FAILED,
                        code="quantum_workflow_timeout",
                        message="Verified quantum execution did not finish within its timeout.",
                        retryable=True,
                    )
                self.sleeper(self.poll_interval_seconds)
                state = self.coordinator.get(run_identifier)
        except Exception:
            return self._failure(
                invocation,
                status=CapabilityInvocationStatus.FAILED,
                code="quantum_execution_spine_failure",
                message="The verified quantum execution spine failed without trusted result evidence.",
                retryable=False,
            )

        status = state.get("status")
        if status == "authorized":
            evidence: dict[str, str] = {
                "run_identifier": run_identifier,
                "run_status": "authorized",
                "run_state_fingerprint": sha256_fingerprint(state),
            }
            for name in ("results", "verification", "receipt"):
                try:
                    artifact = self.coordinator.artifact(run_identifier, name)
                except Exception:
                    return self._failure(
                        invocation,
                        status=CapabilityInvocationStatus.FAILED,
                        code=f"quantum_{name}_unavailable",
                        message="Verified quantum execution evidence is incomplete.",
                    )
                evidence[f"{name}_fingerprint"] = sha256_fingerprint(artifact)
            return CapabilityInvocationResult(
                invocation_identifier=invocation.invocation_identifier,
                status=CapabilityInvocationStatus.SUCCEEDED,
                output_values=FrozenDict(evidence),
                verification_classification="authorized",
                unknown_consumption=("quantum_execution_cost",),
                execution_metadata=FrozenDict(
                    {
                        "execution_spine": "pulsate_run_coordinator",
                        "run_identifier": run_identifier,
                        "execution_target": target,
                    }
                ),
            )
        if status == "interrupted":
            return self._failure(
                invocation,
                status=CapabilityInvocationStatus.FAILED,
                code="quantum_execution_interrupted",
                message="Verified quantum execution was interrupted.",
                retryable=True,
            )
        if status == "rejected":
            return self._failure(
                invocation,
                status=CapabilityInvocationStatus.FAILED,
                code="quantum_verification_rejected",
                message="Quantum execution evidence did not pass verification.",
            )
        return self._failure(
            invocation,
            status=CapabilityInvocationStatus.FAILED,
            code="quantum_execution_failed",
            message="Verified quantum execution failed.",
        )

    @staticmethod
    def _optional_identifier(parameters: Mapping[str, Any], key: str) -> str | None:
        value = parameters.get(key)
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _failure(
        invocation: CapabilityInvocation,
        *,
        status: CapabilityInvocationStatus,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> CapabilityInvocationResult:
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=status,
            retryable=retryable,
            error_code=code,
            error_message=message,
        )


class WorkflowCapabilityRouter:
    """Route existing quantum nodes to the verified spine and all others generically."""

    def __init__(self, *, classical_adapter: Any, quantum_adapter: Any) -> None:
        if not callable(getattr(classical_adapter, "invoke", None)):
            raise TypeError("The classical workflow adapter must implement invoke().")
        if not callable(getattr(quantum_adapter, "invoke", None)):
            raise TypeError("The quantum workflow adapter must implement invoke().")
        self.classical_adapter = classical_adapter
        self.quantum_adapter = quantum_adapter

    def invoke(self, invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        adapter = (
            self.quantum_adapter
            if invocation.node_kind in {NodeKind.QUANTUM_LOCAL, NodeKind.QUANTUM_IBM}
            else self.classical_adapter
        )
        result = adapter.invoke(invocation)
        if not isinstance(result, CapabilityInvocationResult):
            raise TypeError("Workflow capability adapters must return a capability result.")
        if result.invocation_identifier != invocation.invocation_identifier:
            raise ValueError("Workflow capability result identity does not match its request.")
        return result
