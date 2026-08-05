"""Immutable execution-state snapshots for Phase 4 workflow graph runs."""

from __future__ import annotations

import math
from enum import Enum
from typing import Self

from pydantic import Field, field_validator, model_validator

from cgr.science.artifacts import ArtifactPointer, ArtifactReference
from cgr.science.canonical import CanonicalModel, validate_identifier, validate_sha256

from .contracts import (
    ApprovalStatus,
    FrozenDict,
    RetryOutcome,
    _MAX_BINDINGS,
    _MAX_RETRY_COUNT,
    _frozen_numeric_map,
    _frozen_string_map,
    _freeze_json,
)


class GraphStatus(str, Enum):
    """Closed graph-run lifecycle states."""

    DEFINED = "defined"
    VALIDATED = "validated"
    AWAITING_APPROVAL = "awaiting_approval"
    READY = "ready"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    RECOVERED = "recovered"


class NodeStatus(str, Enum):
    """Closed node-run lifecycle states."""

    PENDING = "pending"
    AWAITING_APPROVAL = "awaiting_approval"
    READY = "ready"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYABLE = "retryable"
    RETRY_SCHEDULED = "retry_scheduled"
    BLOCKED = "blocked"
    SKIPPED_BY_CONDITION = "skipped_by_condition"
    SKIPPED_BY_POLICY = "skipped_by_policy"
    CANCELLED = "cancelled"
    RECOVERED = "recovered"


class BudgetStatus(str, Enum):
    """Closed budget-enforcement states."""

    WITHIN_BUDGET = "within_budget"
    AT_CEILING = "at_ceiling"
    OVER_BUDGET = "over_budget"
    UNKNOWN = "unknown"


class ConditionOutcome(str, Enum):
    """Persisted declarative-condition outcome."""

    TRUE = "true"
    FALSE = "false"
    NOT_EVALUATED = "not_evaluated"


def _validate_epoch(value: float | None, *, label: str) -> float | None:
    if value is None:
        return None
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be a finite non-negative epoch.")
    return value


def _validate_optional_cost(value: float | None, *, label: str) -> float | None:
    if value is None:
        return None
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be finite and non-negative.")
    return value


def _bounded_text(value: str | None, *, label: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} cannot be blank.")
    if len(normalized) > 4096:
        raise ValueError(f"{label} exceeds the maximum length.")
    return normalized


class GraphState(CanonicalModel):
    """Immutable snapshot of one graph run."""

    graph_run_identifier: str
    graph_identifier: str
    graph_version: int = Field(ge=1)
    graph_definition_fingerprint: str
    status: GraphStatus = GraphStatus.DEFINED
    tenant_identifier: str
    created_by_principal: str
    created_at_epoch: float = Field(ge=0)
    started_at_epoch: float | None = None
    completed_at_epoch: float | None = None
    current_node_id: str | None = None
    total_nodes: int = Field(ge=1)
    completed_nodes: int = Field(default=0, ge=0)
    failed_nodes: int = Field(default=0, ge=0)
    skipped_nodes: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    budget_status: BudgetStatus = BudgetStatus.WITHIN_BUDGET
    estimated_cost: float | None = None
    actual_cost: float | None = None
    error_message: str | None = None
    recovery_evidence_fingerprint: str | None = None

    @field_validator(
        "graph_run_identifier",
        "graph_identifier",
        "tenant_identifier",
        "created_by_principal",
        "current_node_id",
    )
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @field_validator("graph_definition_fingerprint", "recovery_evidence_fingerprint")
    @classmethod
    def validate_fingerprints(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("created_at_epoch", "started_at_epoch", "completed_at_epoch")
    @classmethod
    def validate_epochs(cls, value: float | None) -> float | None:
        return _validate_epoch(value, label="Graph timestamp")

    @field_validator("estimated_cost", "actual_cost")
    @classmethod
    def validate_costs(cls, value: float | None) -> float | None:
        return _validate_optional_cost(value, label="Graph cost")

    @field_validator("error_message")
    @classmethod
    def validate_error_message(cls, value: str | None) -> str | None:
        return _bounded_text(value, label="Graph error message")

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.completed_nodes + self.failed_nodes + self.skipped_nodes > self.total_nodes:
            raise ValueError("Graph node counts exceed total_nodes.")
        if self.retry_count > self.total_nodes * _MAX_RETRY_COUNT:
            raise ValueError("Graph retry count exceeds the bounded graph maximum.")
        if self.started_at_epoch is not None and self.started_at_epoch < self.created_at_epoch:
            raise ValueError("Graph start time cannot precede creation.")
        if self.completed_at_epoch is not None:
            reference = self.started_at_epoch or self.created_at_epoch
            if self.completed_at_epoch < reference:
                raise ValueError("Graph completion time cannot precede start or creation.")
        terminal = {
            GraphStatus.SUCCEEDED,
            GraphStatus.FAILED,
            GraphStatus.BLOCKED,
            GraphStatus.CANCELLED,
        }
        if (self.status in terminal) != (self.completed_at_epoch is not None):
            raise ValueError("Terminal graph states require exactly one completion time.")
        if (
            self.status in {
                GraphStatus.RUNNING,
                GraphStatus.PAUSED,
                GraphStatus.RECOVERED,
            }
            and self.started_at_epoch is None
        ):
            raise ValueError("Active or recovered graph states require a start time.")
        if self.status is GraphStatus.SUCCEEDED and (
            self.completed_nodes + self.skipped_nodes != self.total_nodes
            or self.failed_nodes
        ):
            raise ValueError("Succeeded graph state has inconsistent node counts.")
        return self


class NodeState(CanonicalModel):
    """Immutable snapshot of one workflow-node attempt."""

    node_id: str
    graph_run_identifier: str
    graph_definition_fingerprint: str
    status: NodeStatus = NodeStatus.PENDING
    attempt_number: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=1, ge=1, le=_MAX_RETRY_COUNT)
    started_at_epoch: float | None = None
    completed_at_epoch: float | None = None
    input_bindings: FrozenDict = Field(default_factory=FrozenDict)
    input_values: FrozenDict = Field(default_factory=FrozenDict)
    output_artifacts: tuple[ArtifactReference, ...] = ()
    output_values: FrozenDict = Field(default_factory=FrozenDict)
    produced_artifact_types: tuple[str, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    retry_reason: str | None = None
    condition_outcome: ConditionOutcome | None = None
    approval_status: ApprovalStatus | None = None
    cost_consumed: float | None = None
    resources_consumed: FrozenDict = Field(default_factory=FrozenDict)
    execution_metadata: FrozenDict = Field(default_factory=FrozenDict)

    @field_validator("node_id", "graph_run_identifier", "error_code")
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @field_validator("graph_definition_fingerprint")
    @classmethod
    def validate_graph_fingerprint(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("started_at_epoch", "completed_at_epoch")
    @classmethod
    def validate_epochs(cls, value: float | None) -> float | None:
        return _validate_epoch(value, label="Node timestamp")

    @field_validator("input_bindings")
    @classmethod
    def validate_bindings(cls, value: FrozenDict) -> FrozenDict:
        if len(value) > _MAX_BINDINGS:
            raise ValueError("Node input bindings exceed the maximum.")
        normalized: dict[str, str] = {}
        for key, item in value.items():
            normalized_key = validate_identifier(key, label="input port identifier")
            normalized[normalized_key] = validate_identifier(
                str(item), label="binding or artifact identifier"
            )
        return FrozenDict(sorted(normalized.items()))

    @field_validator("input_values", mode="before")
    @classmethod
    def validate_input_values(cls, value: object) -> FrozenDict:
        frozen = _freeze_json(value)
        if not isinstance(frozen, FrozenDict):
            raise ValueError("Node input values must be a JSON object.")
        return frozen

    @field_validator("output_artifacts")
    @classmethod
    def order_artifacts(
        cls, value: tuple[ArtifactReference, ...]
    ) -> tuple[ArtifactReference, ...]:
        identifiers = [item.artifact_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Output artifact identifiers must be unique per node attempt.")
        return tuple(sorted(value, key=lambda item: item.artifact_identifier))

    @field_validator("output_values", mode="before")
    @classmethod
    def validate_output_values(cls, value: object) -> FrozenDict:
        frozen = _freeze_json(value)
        if not isinstance(frozen, FrozenDict):
            raise ValueError("Node output values must be a JSON object.")
        return frozen

    @field_validator("produced_artifact_types")
    @classmethod
    def order_artifact_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(validate_identifier(item) for item in value)
        if len(normalized) != len(set(normalized)):
            raise ValueError("Produced artifact types must be unique.")
        return tuple(sorted(normalized))

    @field_validator("error_message", "retry_reason")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        return _bounded_text(value, label="Node explanatory text")

    @field_validator("cost_consumed")
    @classmethod
    def validate_cost(cls, value: float | None) -> float | None:
        return _validate_optional_cost(value, label="Node cost")

    @field_validator("resources_consumed")
    @classmethod
    def validate_resources(cls, value: FrozenDict) -> FrozenDict:
        return _frozen_numeric_map(value, label="node resource consumption")

    @field_validator("execution_metadata")
    @classmethod
    def validate_metadata(cls, value: FrozenDict) -> FrozenDict:
        return _frozen_string_map(value, label="node execution metadata")

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.attempt_number > self.max_attempts:
            raise ValueError("attempt_number cannot exceed max_attempts.")
        if self.started_at_epoch is not None and self.completed_at_epoch is not None:
            if self.completed_at_epoch < self.started_at_epoch:
                raise ValueError("Node completion time cannot precede start time.")
        terminal = {
            NodeStatus.SUCCEEDED,
            NodeStatus.FAILED,
            NodeStatus.BLOCKED,
            NodeStatus.SKIPPED_BY_CONDITION,
            NodeStatus.SKIPPED_BY_POLICY,
            NodeStatus.CANCELLED,
        }
        if (self.status in terminal) != (self.completed_at_epoch is not None):
            raise ValueError("Terminal node states require exactly one completion time.")
        if (
            self.status in {NodeStatus.RUNNING, NodeStatus.RECOVERED}
            and self.started_at_epoch is None
        ):
            raise ValueError("Running or recovered node states require a start time.")
        attempted = {
            NodeStatus.RUNNING,
            NodeStatus.RETRYABLE,
            NodeStatus.RETRY_SCHEDULED,
            NodeStatus.SUCCEEDED,
            NodeStatus.FAILED,
            NodeStatus.RECOVERED,
        }
        if self.status in attempted and self.attempt_number < 1:
            raise ValueError("Attempted node states require attempt_number >= 1.")
        if self.status is NodeStatus.AWAITING_APPROVAL and self.approval_status not in {
            None,
            ApprovalStatus.PENDING,
        }:
            raise ValueError("Awaiting-approval node state has an invalid approval status.")
        if (
            self.status is NodeStatus.SKIPPED_BY_CONDITION
            and self.condition_outcome is not ConditionOutcome.FALSE
        ):
            raise ValueError("Condition-skipped nodes require a false condition outcome.")
        return self


class EdgeState(CanonicalModel):
    """Immutable runtime state for one graph edge."""

    edge_id: str
    graph_run_identifier: str
    source_node_id: str
    target_node_id: str
    activated: bool = False
    condition_evaluated: bool = False
    condition_result: bool | None = None
    activation_epoch: float | None = None

    @field_validator("edge_id", "graph_run_identifier", "source_node_id", "target_node_id")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("activation_epoch")
    @classmethod
    def validate_activation_epoch(cls, value: float | None) -> float | None:
        return _validate_epoch(value, label="Edge activation timestamp")

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.condition_evaluated != (self.condition_result is not None):
            raise ValueError("Condition result presence must match condition_evaluated.")
        if self.activated != (self.activation_epoch is not None):
            raise ValueError("Activated edges require exactly one activation timestamp.")
        if self.activated and self.condition_result is False:
            raise ValueError("An edge with a false condition cannot be activated.")
        return self


class BindingState(CanonicalModel):
    """Immutable transfer state for one port binding."""

    binding_id: str
    graph_run_identifier: str
    source_node_id: str
    source_port_id: str
    destination_node_id: str
    destination_port_id: str
    value_transferred: bool = False
    artifact_reference: ArtifactPointer | None = None
    transfer_epoch: float | None = None

    @field_validator(
        "binding_id",
        "graph_run_identifier",
        "source_node_id",
        "source_port_id",
        "destination_node_id",
        "destination_port_id",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("transfer_epoch")
    @classmethod
    def validate_transfer_epoch(cls, value: float | None) -> float | None:
        return _validate_epoch(value, label="Binding transfer timestamp")

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.value_transferred != (self.transfer_epoch is not None):
            raise ValueError("Transferred bindings require exactly one transfer timestamp.")
        if self.artifact_reference is not None and not self.value_transferred:
            raise ValueError("Artifact references require a completed transfer.")
        return self


class ApprovalState(CanonicalModel):
    """Immutable approval decision bound to an exact graph definition."""

    approval_id: str
    graph_run_identifier: str
    graph_identifier: str
    graph_version: int = Field(ge=1)
    graph_definition_fingerprint: str
    node_id: str | None = None
    operation_identity: str | None = None
    capability_identity: str | None = None
    input_binding_ids: tuple[str, ...] = ()
    cost_ceiling: float | None = None
    resource_ceilings: FrozenDict = Field(default_factory=FrozenDict)
    approver_principal: str | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at_epoch: float = Field(ge=0)
    decided_at_epoch: float | None = None
    expiry_epoch: float | None = None
    decision_reason: str | None = None
    correlation_identity: str | None = None

    @field_validator(
        "approval_id",
        "graph_run_identifier",
        "graph_identifier",
        "node_id",
        "operation_identity",
        "capability_identity",
        "approver_principal",
        "correlation_identity",
    )
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @field_validator("graph_definition_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("input_binding_ids")
    @classmethod
    def order_binding_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(validate_identifier(item) for item in value)
        if len(normalized) != len(set(normalized)):
            raise ValueError("Approval input binding identifiers must be unique.")
        return tuple(sorted(normalized))

    @field_validator("cost_ceiling")
    @classmethod
    def validate_cost_ceiling(cls, value: float | None) -> float | None:
        return _validate_optional_cost(value, label="Approval cost ceiling")

    @field_validator("resource_ceilings")
    @classmethod
    def validate_resource_ceilings(cls, value: FrozenDict) -> FrozenDict:
        return _frozen_numeric_map(value, label="approval resource ceilings")

    @field_validator("requested_at_epoch", "decided_at_epoch", "expiry_epoch")
    @classmethod
    def validate_epochs(cls, value: float | None) -> float | None:
        return _validate_epoch(value, label="Approval timestamp")

    @field_validator("decision_reason")
    @classmethod
    def validate_reason(cls, value: str | None) -> str | None:
        return _bounded_text(value, label="Approval decision reason")

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.node_id is None and self.operation_identity is None:
            raise ValueError("Approval state must bind a node or operation.")
        decided = self.status in {
            ApprovalStatus.GRANTED,
            ApprovalStatus.DENIED,
            ApprovalStatus.EXPIRED,
        }
        if decided != (self.decided_at_epoch is not None):
            raise ValueError("Decided approvals require exactly one decision timestamp.")
        if decided != (self.approver_principal is not None):
            raise ValueError("Decided approvals require exactly one approver principal.")
        if self.decided_at_epoch is not None and self.decided_at_epoch < self.requested_at_epoch:
            raise ValueError("Approval decision cannot precede its request.")
        if self.expiry_epoch is not None and self.expiry_epoch < self.requested_at_epoch:
            raise ValueError("Approval expiry cannot precede its request.")
        return self


class RetryState(CanonicalModel):
    """Immutable evidence for one scheduled retry."""

    retry_id: str
    graph_run_identifier: str
    node_id: str
    attempt_number: int = Field(ge=1, le=_MAX_RETRY_COUNT)
    original_failure_code: str
    original_failure_message: str
    scheduled_at_epoch: float = Field(ge=0)
    executed_at_epoch: float | None = None
    outcome: RetryOutcome | None = None
    retry_policy_fingerprint: str

    @field_validator(
        "retry_id", "graph_run_identifier", "node_id", "original_failure_code"
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("original_failure_message")
    @classmethod
    def validate_failure_message(cls, value: str) -> str:
        normalized = _bounded_text(value, label="Retry failure message")
        assert normalized is not None
        return normalized

    @field_validator("scheduled_at_epoch", "executed_at_epoch")
    @classmethod
    def validate_epochs(cls, value: float | None) -> float | None:
        return _validate_epoch(value, label="Retry timestamp")

    @field_validator("retry_policy_fingerprint")
    @classmethod
    def validate_retry_fingerprint(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if (self.outcome is not None) != (self.executed_at_epoch is not None):
            raise ValueError("Retry outcomes require exactly one execution timestamp.")
        if self.executed_at_epoch is not None and self.executed_at_epoch < self.scheduled_at_epoch:
            raise ValueError("Retry execution cannot precede scheduling.")
        return self


class BudgetState(CanonicalModel):
    """Immutable separation of estimates, reservations, known use, and unknown use."""

    graph_run_identifier: str
    planning_estimates: FrozenDict = Field(default_factory=FrozenDict)
    reserved_budget: FrozenDict = Field(default_factory=FrozenDict)
    actual_consumption: FrozenDict = Field(default_factory=FrozenDict)
    unknown_consumption: FrozenDict = Field(default_factory=FrozenDict)
    ceiling_values: FrozenDict = Field(default_factory=FrozenDict)
    remaining_budget: FrozenDict = Field(default_factory=FrozenDict)
    status: BudgetStatus = BudgetStatus.WITHIN_BUDGET
    terminal_over_budget: bool = False
    last_updated_epoch: float = Field(ge=0)

    @field_validator("graph_run_identifier")
    @classmethod
    def validate_graph_run_identifier(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator(
        "planning_estimates",
        "reserved_budget",
        "actual_consumption",
        "unknown_consumption",
        "ceiling_values",
        "remaining_budget",
    )
    @classmethod
    def validate_numeric_maps(cls, value: FrozenDict) -> FrozenDict:
        return _frozen_numeric_map(value, label="budget dimension", maximum=128)

    @field_validator("last_updated_epoch")
    @classmethod
    def validate_updated_epoch(cls, value: float) -> float:
        normalized = _validate_epoch(value, label="Budget update timestamp")
        assert normalized is not None
        return normalized

    @model_validator(mode="after")
    def validate_budget(self) -> Self:
        over_budget = False
        at_ceiling = False
        for key, ceiling in self.ceiling_values.items():
            known = self.actual_consumption.get(key, 0.0)
            reserved = self.reserved_budget.get(key, 0.0)
            if known > ceiling or known + reserved > ceiling:
                over_budget = True
            elif known == ceiling or known + reserved == ceiling:
                at_ceiling = True
            remaining = self.remaining_budget.get(key)
            if remaining is not None and not math.isclose(
                remaining,
                max(ceiling - known - reserved, 0.0),
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                raise ValueError("Remaining budget is inconsistent with known values.")
        expected_status = (
            BudgetStatus.OVER_BUDGET
            if over_budget
            else BudgetStatus.UNKNOWN
            if self.unknown_consumption
            else BudgetStatus.AT_CEILING
            if at_ceiling
            else BudgetStatus.WITHIN_BUDGET
        )
        if self.status is not expected_status:
            raise ValueError("Budget status is inconsistent with persisted consumption.")
        if self.terminal_over_budget != over_budget:
            raise ValueError("terminal_over_budget is inconsistent with persisted budget.")
        return self


class ExternalInputState(CanonicalModel):
    """Immutable external artifact or scalar supplied to a graph-run port."""

    input_identifier: str
    graph_run_identifier: str
    node_id: str
    port_id: str
    artifact_reference: ArtifactPointer | None = None
    value: object | None = None

    @field_validator(
        "input_identifier", "graph_run_identifier", "node_id", "port_id"
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: object | None) -> object | None:
        return _freeze_json(value) if value is not None else None

    @model_validator(mode="after")
    def validate_exact_value(self) -> Self:
        if (self.artifact_reference is None) == (self.value is None):
            raise ValueError(
                "External workflow inputs require exactly one artifact or scalar value."
            )
        return self


class DecisionKind(str, Enum):
    """Closed persisted orchestration-decision kinds."""

    CONDITION = "condition"
    COMPARISON = "comparison"
    SWEEP_EXPANSION = "sweep_expansion"
    FAILURE_PROPAGATION = "failure_propagation"
    BUDGET = "budget"
    APPROVAL = "approval"
    CANCELLATION = "cancellation"
    RECOVERY = "recovery"


class DecisionState(CanonicalModel):
    """Immutable, attributable branch or scheduling decision."""

    decision_identifier: str
    graph_run_identifier: str
    decision_kind: DecisionKind
    subject_identifier: str
    input_fingerprint: str
    outcome: str
    decided_at_epoch: float = Field(ge=0)
    related_node_ids: tuple[str, ...] = ()
    metadata: FrozenDict = Field(default_factory=FrozenDict)

    @field_validator(
        "decision_identifier",
        "graph_run_identifier",
        "subject_identifier",
        "outcome",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("input_fingerprint")
    @classmethod
    def validate_input_fingerprint(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("decided_at_epoch")
    @classmethod
    def validate_decided_at(cls, value: float) -> float:
        normalized = _validate_epoch(value, label="Decision timestamp")
        assert normalized is not None
        return normalized

    @field_validator("related_node_ids")
    @classmethod
    def order_related_nodes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(validate_identifier(item) for item in value)
        if len(normalized) != len(set(normalized)):
            raise ValueError("Decision related node identifiers must be unique.")
        return tuple(sorted(normalized))

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: FrozenDict) -> FrozenDict:
        return _frozen_string_map(value, label="decision metadata")


class WorkflowRunSnapshot(CanonicalModel):
    """Complete integrity-checked persisted snapshot of one graph run."""

    revision: int = Field(ge=0)
    graph_state: GraphState
    node_states: tuple[NodeState, ...]
    edge_states: tuple[EdgeState, ...] = ()
    binding_states: tuple[BindingState, ...] = ()
    approval_states: tuple[ApprovalState, ...] = ()
    retry_states: tuple[RetryState, ...] = ()
    decision_states: tuple[DecisionState, ...] = ()
    external_inputs: tuple[ExternalInputState, ...] = ()
    budget_state: BudgetState
    snapshot_fingerprint: str | None = None

    @field_validator("node_states")
    @classmethod
    def order_node_states(cls, value: tuple[NodeState, ...]) -> tuple[NodeState, ...]:
        identities = [(item.node_id, item.attempt_number) for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError("Node-state identities must be unique per attempt.")
        return tuple(sorted(value, key=lambda item: (item.node_id, item.attempt_number)))

    @field_validator("edge_states")
    @classmethod
    def order_edge_states(cls, value: tuple[EdgeState, ...]) -> tuple[EdgeState, ...]:
        identifiers = [item.edge_id for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Edge-state identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.edge_id))

    @field_validator("binding_states")
    @classmethod
    def order_binding_states(
        cls, value: tuple[BindingState, ...]
    ) -> tuple[BindingState, ...]:
        identifiers = [item.binding_id for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Binding-state identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.binding_id))

    @field_validator("approval_states")
    @classmethod
    def order_approval_states(
        cls, value: tuple[ApprovalState, ...]
    ) -> tuple[ApprovalState, ...]:
        identifiers = [item.approval_id for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Approval-state identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.approval_id))

    @field_validator("retry_states")
    @classmethod
    def order_retry_states(cls, value: tuple[RetryState, ...]) -> tuple[RetryState, ...]:
        identifiers = [item.retry_id for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Retry-state identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.retry_id))

    @field_validator("decision_states")
    @classmethod
    def order_decisions(
        cls, value: tuple[DecisionState, ...]
    ) -> tuple[DecisionState, ...]:
        identifiers = [item.decision_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Decision-state identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.decision_identifier))

    @field_validator("external_inputs")
    @classmethod
    def order_external_inputs(
        cls, value: tuple[ExternalInputState, ...]
    ) -> tuple[ExternalInputState, ...]:
        identifiers = [item.input_identifier for item in value]
        ports = [(item.node_id, item.port_id) for item in value]
        if len(identifiers) != len(set(identifiers)) or len(ports) != len(set(ports)):
            raise ValueError("External workflow inputs must be unique by identity and port.")
        return tuple(sorted(value, key=lambda item: item.input_identifier))

    @field_validator("snapshot_fingerprint")
    @classmethod
    def validate_snapshot_fingerprint(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        run_id = self.graph_state.graph_run_identifier
        graph_fingerprint = self.graph_state.graph_definition_fingerprint
        for state in self.node_states:
            if (
                state.graph_run_identifier != run_id
                or state.graph_definition_fingerprint != graph_fingerprint
            ):
                raise ValueError("Node state does not belong to the graph run.")
        for collection in (
            self.edge_states,
            self.binding_states,
            self.approval_states,
            self.retry_states,
            self.decision_states,
            self.external_inputs,
        ):
            if any(item.graph_run_identifier != run_id for item in collection):
                raise ValueError("Persisted workflow state crosses graph-run identity.")
        if self.budget_state.graph_run_identifier != run_id:
            raise ValueError("Budget state does not belong to the graph run.")
        expected = self.fingerprint_without_receipt()
        if self.snapshot_fingerprint is not None and self.snapshot_fingerprint != expected:
            raise ValueError("Workflow run snapshot fingerprint does not match content.")
        object.__setattr__(self, "snapshot_fingerprint", expected)
        return self

    def fingerprint_without_receipt(self) -> str:
        from cgr.science.canonical import sha256_fingerprint

        return sha256_fingerprint(
            self.model_dump(mode="json", exclude={"snapshot_fingerprint"})
        )
