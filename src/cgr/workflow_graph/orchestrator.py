"""Persistent, bounded Phase 4 workflow orchestration."""

from __future__ import annotations

import concurrent.futures
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from cgr.science.artifacts import ArtifactPointer
from cgr.science.canonical import sha256_fingerprint, validate_identifier

from .capability_adapter import (
    CapabilityAdapter,
    CapabilityInvocation,
    CapabilityInvocationResult,
    CapabilityInvocationStatus,
)
from .contracts import (
    ApprovalRequirement,
    ComparisonKind,
    ConditionKind,
    EdgeKind,
    FailureAction,
    FrozenDict,
    NodeKind,
    PortBinding,
    PortType,
    WorkflowEdge,
    WorkflowGraphDefinition,
    WorkflowNode,
)
from .repository import GraphRunRepository, WorkflowGraphRepository
from .state import (
    ApprovalState,
    ApprovalStatus,
    BindingState,
    BudgetState,
    BudgetStatus,
    ConditionOutcome,
    DecisionKind,
    DecisionState,
    EdgeState,
    ExternalInputState,
    GraphState,
    GraphStatus,
    NodeState,
    NodeStatus,
    RetryOutcome,
    RetryState,
    WorkflowRunSnapshot,
)
from .validation import WorkflowGraphValidator


class WorkflowOrchestrationError(RuntimeError):
    """Controlled orchestration failure."""


class WorkflowRunTerminalError(WorkflowOrchestrationError):
    """Mutation was requested after a graph run became terminal."""


class WorkflowApprovalError(WorkflowOrchestrationError):
    """Approval request or decision was invalid."""


class WorkflowStateIntegrityError(WorkflowOrchestrationError):
    """Persisted run state is inconsistent with its graph definition."""


class WorkflowObserver(Protocol):
    """Bounded event boundary used by API observability and audit integration."""

    def __call__(self, event: str, fields: Mapping[str, str | int | float | bool]) -> None:
        ...


Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class _RuntimeGraph:
    definition: WorkflowGraphDefinition
    expansion_decisions: tuple[tuple[str, str, tuple[str, ...]], ...]


_TERMINAL_NODE_STATUSES = frozenset(
    {
        NodeStatus.SUCCEEDED,
        NodeStatus.FAILED,
        NodeStatus.BLOCKED,
        NodeStatus.SKIPPED_BY_CONDITION,
        NodeStatus.SKIPPED_BY_POLICY,
        NodeStatus.CANCELLED,
    }
)
_TERMINAL_GRAPH_STATUSES = frozenset(
    {
        GraphStatus.SUCCEEDED,
        GraphStatus.FAILED,
        GraphStatus.BLOCKED,
        GraphStatus.CANCELLED,
    }
)


class WorkflowOrchestrator:
    """Supervise generic capabilities through deterministic persisted graph runs."""

    def __init__(
        self,
        *,
        graph_repository: WorkflowGraphRepository,
        run_repository: GraphRunRepository,
        capability_adapter: CapabilityAdapter,
        maximum_parallelism: int = 8,
        clock: Clock = time.time,
        observer: WorkflowObserver | None = None,
    ) -> None:
        if maximum_parallelism < 1 or maximum_parallelism > 64:
            raise ValueError("Workflow maximum_parallelism must be between 1 and 64.")
        self.graph_repository = graph_repository
        self.run_repository = run_repository
        self.capability_adapter = capability_adapter
        self.maximum_parallelism = maximum_parallelism
        self.clock = clock
        self.observer = observer

    def create_run(
        self,
        *,
        graph_identifier: str,
        graph_version: int,
        tenant_identifier: str,
        created_by_principal: str,
        graph_run_identifier: str | None = None,
        external_inputs: tuple[ExternalInputState, ...] = (),
    ) -> WorkflowRunSnapshot:
        graph = self.graph_repository.get(graph_identifier, graph_version)
        validation = WorkflowGraphValidator(graph).validate()
        if not validation.valid:
            raise WorkflowStateIntegrityError(
                "A statically invalid workflow graph cannot be started."
            )
        run_id = graph_run_identifier or self._stable_identifier(
            "workflow-run",
            {
                "graph": graph.definition_fingerprint,
                "tenant": tenant_identifier,
                "principal": created_by_principal,
                "nonce": f"{self.clock():.9f}",
            },
        )
        run_id = validate_identifier(run_id, label="workflow run identifier")
        tenant_identifier = validate_identifier(
            tenant_identifier, label="workflow tenant identifier"
        )
        if (
            graph.metadata is not None
            and graph.metadata.tenant_identifier is not None
            and graph.metadata.tenant_identifier != tenant_identifier
        ):
            raise WorkflowStateIntegrityError(
                "Workflow graph belongs to another tenant."
            )
        created_by_principal = validate_identifier(
            created_by_principal, label="workflow creator principal"
        )
        runtime = self._expand_graph(graph)
        now = self.clock()
        normalized_inputs = self._normalize_external_inputs(
            external_inputs,
            run_id=run_id,
            runtime_graph=runtime.definition,
        )
        approvals = self._initial_approval_states(
            runtime.definition, run_id=run_id, now=now
        )
        approval_by_node = {
            node_id
            for state in approvals
            if (node_id := state.node_id) is not None
        }
        node_states = tuple(
            NodeState(
                node_id=node.node_identifier,
                graph_run_identifier=run_id,
                graph_definition_fingerprint=graph.definition_fingerprint,
                status=(
                    NodeStatus.AWAITING_APPROVAL
                    if node.node_identifier in approval_by_node
                    else NodeStatus.PENDING
                ),
                max_attempts=self._maximum_attempts(node, graph),
                approval_status=(
                    ApprovalStatus.PENDING
                    if node.node_identifier in approval_by_node
                    else None
                ),
            )
            for node in runtime.definition.nodes
        )
        budget = self._initial_budget(runtime.definition, run_id=run_id, now=now)
        decisions = tuple(
            DecisionState(
                decision_identifier=self._stable_identifier(
                    "decision",
                    {
                        "run": run_id,
                        "kind": DecisionKind.SWEEP_EXPANSION.value,
                        "subject": sweep_id,
                        "nodes": generated,
                    },
                ),
                graph_run_identifier=run_id,
                decision_kind=DecisionKind.SWEEP_EXPANSION,
                subject_identifier=sweep_id,
                input_fingerprint=input_fingerprint,
                outcome="expanded",
                decided_at_epoch=now,
                related_node_ids=generated,
            )
            for sweep_id, input_fingerprint, generated in runtime.expansion_decisions
        )
        graph_status = (
            GraphStatus.AWAITING_APPROVAL if approvals else GraphStatus.READY
        )
        graph_state = GraphState(
            graph_run_identifier=run_id,
            graph_identifier=graph.graph_identifier,
            graph_version=graph.version,
            graph_definition_fingerprint=graph.definition_fingerprint,
            status=graph_status,
            tenant_identifier=tenant_identifier,
            created_by_principal=created_by_principal,
            created_at_epoch=now,
            total_nodes=len(runtime.definition.nodes),
            estimated_cost=runtime.definition.metadata.estimated_cost
            if runtime.definition.metadata
            else None,
        )
        snapshot = WorkflowRunSnapshot(
            revision=0,
            graph_state=graph_state,
            node_states=node_states,
            edge_states=tuple(
                EdgeState(
                    edge_id=edge.edge_identifier,
                    graph_run_identifier=run_id,
                    source_node_id=edge.source_node_id,
                    target_node_id=edge.target_node_id,
                )
                for edge in runtime.definition.edges
            ),
            binding_states=tuple(
                BindingState(
                    binding_id=binding.binding_identifier,
                    graph_run_identifier=run_id,
                    source_node_id=binding.source_node_id,
                    source_port_id=binding.source_port_id,
                    destination_node_id=binding.destination_node_id,
                    destination_port_id=binding.destination_port_id,
                )
                for binding in runtime.definition.bindings
            ),
            approval_states=approvals,
            decision_states=decisions,
            external_inputs=normalized_inputs,
            budget_state=budget,
        )
        created = self.run_repository.create(snapshot)
        self._emit(
            "workflow.run.created",
            run_identifier=run_id,
            graph_identifier=graph.graph_identifier,
            node_count=len(runtime.definition.nodes),
        )
        return created

    def approve(
        self,
        *,
        graph_run_identifier: str,
        approval_identifier: str,
        approver_principal: str,
        granted: bool,
        correlation_identifier: str,
        decision_reason: str | None = None,
    ) -> WorkflowRunSnapshot:
        snapshot = self.run_repository.get(graph_run_identifier)
        self._require_nonterminal(snapshot)
        approval_identifier = validate_identifier(approval_identifier)
        approver_principal = validate_identifier(approver_principal)
        correlation_identifier = validate_identifier(correlation_identifier)
        now = self.clock()
        found = False
        approvals: list[ApprovalState] = []
        for approval in snapshot.approval_states:
            if approval.approval_id != approval_identifier:
                approvals.append(approval)
                continue
            found = True
            if approval.status is not ApprovalStatus.PENDING:
                raise WorkflowApprovalError("Approval has already been decided.")
            if approval.expiry_epoch is not None and now >= approval.expiry_epoch:
                status = ApprovalStatus.EXPIRED
                reason = "Approval validity expired before decision."
            else:
                status = ApprovalStatus.GRANTED if granted else ApprovalStatus.DENIED
                reason = decision_reason
            approvals.append(
                ApprovalState.model_validate(
                    {
                        **approval.model_dump(mode="json"),
                        "status": status.value,
                        "approver_principal": approver_principal,
                        "decided_at_epoch": now,
                        "decision_reason": reason,
                        "correlation_identity": correlation_identifier,
                    }
                )
            )
        if not found:
            raise WorkflowApprovalError("Approval was not found.")
        decision = DecisionState(
            decision_identifier=self._stable_identifier(
                "decision",
                {
                    "run": graph_run_identifier,
                    "approval": approval_identifier,
                    "status": (
                        ApprovalStatus.GRANTED.value
                        if granted
                        else ApprovalStatus.DENIED.value
                    ),
                    "revision": snapshot.revision + 1,
                },
            ),
            graph_run_identifier=graph_run_identifier,
            decision_kind=DecisionKind.APPROVAL,
            subject_identifier=approval_identifier,
            input_fingerprint=sha256_fingerprint(
                {
                    "graph": snapshot.graph_state.graph_definition_fingerprint,
                    "approver": approver_principal,
                    "correlation": correlation_identifier,
                }
            ),
            outcome=("granted" if granted else "denied"),
            decided_at_epoch=now,
            related_node_ids=tuple(
                item.node_id
                for item in approvals
                if item.approval_id == approval_identifier and item.node_id is not None
            ),
        )
        updated = self._snapshot(
            snapshot,
            approval_states=tuple(approvals),
            decision_states=(*snapshot.decision_states, decision),
        )
        updated = self._refresh_statuses(updated)
        revised = WorkflowRunSnapshot.model_validate(
            {
                **updated.model_dump(mode="json", exclude={"snapshot_fingerprint"}),
                "revision": snapshot.revision + 1,
                "snapshot_fingerprint": None,
            }
        )
        persisted = self.run_repository.update(
            revised, expected_revision=snapshot.revision
        )
        self._emit(
            "workflow.approval.decided",
            run_identifier=graph_run_identifier,
            approval_identifier=approval_identifier,
            granted=granted,
        )
        return persisted

    def cancel(
        self,
        *,
        graph_run_identifier: str,
        requested_by_principal: str,
        correlation_identifier: str | None = None,
        reason: str | None = None,
    ) -> WorkflowRunSnapshot:
        snapshot = self.run_repository.get(graph_run_identifier)
        self._require_nonterminal(snapshot)
        requested_by_principal = validate_identifier(requested_by_principal)
        correlation_identifier = (
            validate_identifier(correlation_identifier)
            if correlation_identifier is not None
            else None
        )
        if reason is not None:
            reason = reason.strip()
            if not reason or len(reason) > 4096:
                raise ValueError("Workflow cancellation reason must be nonempty and bounded.")
        now = self.clock()
        states: list[NodeState] = []
        latest = self._latest_states(snapshot)
        for state in snapshot.node_states:
            if state is not latest[state.node_id]:
                states.append(state)
                continue
            if state.status in _TERMINAL_NODE_STATUSES:
                states.append(state)
                continue
            states.append(
                self._replace_node_state(
                    state,
                    status=NodeStatus.CANCELLED,
                    completed_at_epoch=now,
                    error_code="cancelled",
                    error_message="Workflow node execution was cancelled.",
                )
            )
        graph_state = self._graph_state(
            snapshot.graph_state,
            status=GraphStatus.CANCELLED,
            completed_at_epoch=now,
            counts=self._counts(tuple(states)),
            retry_count=len(snapshot.retry_states),
        )
        decision = DecisionState(
            decision_identifier=self._stable_identifier(
                "decision",
                {
                    "run": graph_run_identifier,
                    "kind": DecisionKind.CANCELLATION.value,
                    "revision": snapshot.revision + 1,
                },
            ),
            graph_run_identifier=graph_run_identifier,
            decision_kind=DecisionKind.CANCELLATION,
            subject_identifier=graph_run_identifier,
            input_fingerprint=sha256_fingerprint(
                {
                    "principal": requested_by_principal,
                    "correlation": correlation_identifier,
                    "reason": reason,
                }
            ),
            outcome="cancelled",
            decided_at_epoch=now,
            related_node_ids=tuple(sorted(latest)),
            metadata=FrozenDict(
                {
                    "requested_by_principal": requested_by_principal,
                    **(
                        {"correlation_identifier": correlation_identifier}
                        if correlation_identifier is not None
                        else {}
                    ),
                    **({"reason": reason} if reason is not None else {}),
                }
            ),
        )
        updated = self._snapshot(
            snapshot,
            graph_state=graph_state,
            node_states=tuple(states),
            decision_states=(*snapshot.decision_states, decision),
        )
        revised = WorkflowRunSnapshot.model_validate(
            {
                **updated.model_dump(mode="json", exclude={"snapshot_fingerprint"}),
                "revision": snapshot.revision + 1,
                "snapshot_fingerprint": None,
            }
        )
        persisted = self.run_repository.update(
            revised, expected_revision=snapshot.revision
        )
        self._emit(
            "workflow.run.cancelled",
            run_identifier=graph_run_identifier,
            principal=requested_by_principal,
        )
        return persisted

    def resume(
        self,
        graph_run_identifier: str,
        *,
        correlation_identifier: str | None = None,
        maximum_cycles: int = 1000,
    ) -> WorkflowRunSnapshot:
        if maximum_cycles < 1 or maximum_cycles > 10_000:
            raise ValueError("maximum_cycles is outside the bounded range.")
        snapshot = self.run_repository.get(graph_run_identifier)
        if snapshot.graph_state.status in _TERMINAL_GRAPH_STATUSES:
            return snapshot
        graph = self.graph_repository.get(
            snapshot.graph_state.graph_identifier,
            snapshot.graph_state.graph_version,
        )
        if graph.definition_fingerprint != snapshot.graph_state.graph_definition_fingerprint:
            raise WorkflowStateIntegrityError(
                "Graph-run state no longer matches its immutable graph definition."
            )
        runtime = self._expand_graph(graph).definition
        self._verify_runtime_identity(snapshot, runtime)
        correlation_identifier = (
            validate_identifier(correlation_identifier)
            if correlation_identifier is not None
            else None
        )
        before_recovery = snapshot.snapshot_fingerprint
        snapshot = self._recover_interrupted_nodes(snapshot)
        snapshot = self._persist_if_changed(before_recovery, snapshot)

        for _ in range(maximum_cycles):
            before = snapshot.snapshot_fingerprint
            snapshot = self._expire_approvals(snapshot)
            snapshot = self._evaluate_conditions(snapshot, runtime)
            snapshot = self._refresh_statuses(snapshot, runtime)
            if snapshot.graph_state.status in _TERMINAL_GRAPH_STATUSES:
                return self._persist_if_changed(before, snapshot)
            ready = self._ready_nodes(snapshot)
            if not ready:
                snapshot = self._finalize_or_pause(snapshot)
                return self._persist_if_changed(before, snapshot)

            selected, snapshot = self._reserve_ready_nodes(snapshot, runtime, ready)
            if not selected:
                snapshot = self._finalize_or_pause(snapshot)
                return self._persist_if_changed(before, snapshot)
            snapshot = self._mark_running(snapshot, selected)
            snapshot = self._persist_if_changed(before, snapshot)
            results = self._invoke_nodes(
                snapshot,
                runtime,
                selected,
                correlation_identifier=correlation_identifier,
            )
            before_results = snapshot.snapshot_fingerprint
            snapshot = self._apply_results(snapshot, runtime, results)
            snapshot = self._transfer_bindings(snapshot, runtime)
            snapshot = self._refresh_statuses(snapshot, runtime)
            snapshot = self._persist_if_changed(before_results, snapshot)
            if snapshot.graph_state.status in _TERMINAL_GRAPH_STATUSES:
                return snapshot
        raise WorkflowOrchestrationError(
            "Workflow orchestration exceeded its bounded cycle limit."
        )

    def _expand_graph(self, graph: WorkflowGraphDefinition) -> _RuntimeGraph:
        if not graph.parameter_sweeps:
            return _RuntimeGraph(graph, ())
        nodes = {item.node_identifier: item for item in graph.nodes}
        edges = list(graph.edges)
        bindings = list(graph.bindings)
        roots = set(graph.root_node_ids)
        terminals = set(graph.terminal_node_ids)
        groups = list(graph.comparison_groups)
        decisions: list[tuple[str, str, tuple[str, ...]]] = []

        for sweep in graph.parameter_sweeps:
            base = nodes.get(sweep.base_node_id)
            if base is None:
                raise WorkflowStateIntegrityError(
                    "Parameter sweep references an unavailable base node."
                )
            generated: list[str] = []
            generated_nodes: list[WorkflowNode] = []
            for index, value in enumerate(sweep.parameter_values):
                generated_id = self._stable_identifier(
                    "sweep-node",
                    {
                        "graph": graph.definition_fingerprint,
                        "sweep": sweep.sweep_identifier,
                        "index": index,
                        "value": value,
                    },
                )
                generated.append(generated_id)
                instance_kind = base.node_kind
                if instance_kind is NodeKind.PARAMETER_SWEEP:
                    requested = base.metadata.get("instance_node_kind", "classical")
                    try:
                        instance_kind = NodeKind(str(requested))
                    except ValueError:
                        instance_kind = NodeKind.CLASSICAL
                parameters = dict(base.parameters)
                parameters[sweep.parameter_name] = value
                metadata = dict(base.metadata)
                metadata.update(
                    {
                        "sweep_identifier": sweep.sweep_identifier,
                        "sweep_base_node": base.node_identifier,
                        "sweep_index": str(index),
                    }
                )
                generated_nodes.append(
                    WorkflowNode.model_validate(
                        {
                            **base.model_dump(mode="json"),
                            "node_identifier": generated_id,
                            "node_kind": instance_kind.value,
                            "parameters": parameters,
                            "metadata": metadata,
                        }
                    )
                )
            del nodes[base.node_identifier]
            nodes.update({item.node_identifier: item for item in generated_nodes})
            edges = self._expand_edges(edges, base.node_identifier, tuple(generated))
            bindings = self._expand_bindings(
                bindings, base.node_identifier, tuple(generated), nodes
            )
            if base.node_identifier in roots:
                roots.remove(base.node_identifier)
                roots.update(generated)
            if base.node_identifier in terminals:
                terminals.remove(base.node_identifier)
                terminals.update(generated)
            groups = [
                group.model_copy(
                    update={
                        "member_node_ids": tuple(
                            sorted(
                                generated
                                if item == base.node_identifier
                                else (item,)
                                for item in group.member_node_ids
                            )
                        )
                    }
                )
                if base.node_identifier in group.member_node_ids
                else group
                for group in groups
            ]
            # model_copy above creates nested tuples; normalize safely.
            normalized_groups = []
            for group in groups:
                members: list[str] = []
                for item in group.member_node_ids:
                    if isinstance(item, tuple):
                        members.extend(item)
                    else:
                        members.append(item)
                normalized_groups.append(
                    group.__class__.model_validate(
                        {**group.model_dump(mode="json"), "member_node_ids": members}
                    )
                )
            groups = normalized_groups
            decisions.append(
                (
                    sweep.sweep_identifier,
                    sweep.fingerprint,
                    tuple(generated),
                )
            )

        # Comparison aggregation has an implicit dependency on every member.
        existing_pairs = {(item.source_node_id, item.target_node_id) for item in edges}
        for group in groups:
            if group.aggregation_node_id is None:
                continue
            for member in group.member_node_ids:
                pair = (member, group.aggregation_node_id)
                if pair in existing_pairs:
                    continue
                edges.append(
                    WorkflowEdge(
                        edge_identifier=self._stable_identifier(
                            "comparison-edge",
                            {
                                "group": group.group_identifier,
                                "source": member,
                                "target": group.aggregation_node_id,
                            },
                        ),
                        edge_kind=EdgeKind.COMPARISON_MEMBER,
                        source_node_id=member,
                        target_node_id=group.aggregation_node_id,
                    )
                )
                existing_pairs.add(pair)

        runtime = WorkflowGraphDefinition(
            graph_identifier=graph.graph_identifier,
            version=graph.version,
            nodes=tuple(nodes.values()),
            edges=tuple(edges),
            bindings=tuple(bindings),
            dependencies=(),
            root_node_ids=tuple(sorted(roots)),
            terminal_node_ids=tuple(sorted(terminals)),
            parameter_sweeps=(),
            comparison_groups=tuple(groups),
            global_execution_policy=graph.global_execution_policy,
            global_retry_policy=graph.global_retry_policy,
            global_failure_recovery_policy=graph.global_failure_recovery_policy,
            cost_ceilings=graph.cost_ceilings,
            resource_ceilings=graph.resource_ceilings,
            metadata=graph.metadata,
        )
        validation = WorkflowGraphValidator(runtime).validate()
        if not validation.valid:
            codes = ",".join(item.code for item in validation.errors[:8])
            raise WorkflowStateIntegrityError(
                f"Expanded workflow graph is invalid: {codes}"
            )
        return _RuntimeGraph(runtime, tuple(decisions))

    def _expand_edges(
        self,
        edges: list[WorkflowEdge],
        base_node_id: str,
        generated: tuple[str, ...],
    ) -> list[WorkflowEdge]:
        expanded: list[WorkflowEdge] = []
        for edge in edges:
            sources = generated if edge.source_node_id == base_node_id else (edge.source_node_id,)
            targets = generated if edge.target_node_id == base_node_id else (edge.target_node_id,)
            if sources == (edge.source_node_id,) and targets == (edge.target_node_id,):
                expanded.append(edge)
                continue
            for source in sources:
                for target in targets:
                    condition = None
                    if edge.condition is not None:
                        condition = edge.condition.__class__.model_validate(
                            {
                                **edge.condition.model_dump(mode="json"),
                                "edge_identifier": self._stable_identifier(
                                    "expanded-edge",
                                    {
                                        "edge": edge.edge_identifier,
                                        "source": source,
                                        "target": target,
                                    },
                                ),
                                "source_node_id": source,
                                "target_node_id": target,
                            }
                        )
                    edge_identifier = (
                        condition.edge_identifier
                        if condition is not None
                        else self._stable_identifier(
                            "expanded-edge",
                            {
                                "edge": edge.edge_identifier,
                                "source": source,
                                "target": target,
                            },
                        )
                    )
                    expanded.append(
                        WorkflowEdge.model_validate(
                            {
                                **edge.model_dump(mode="json"),
                                "edge_identifier": edge_identifier,
                                "source_node_id": source,
                                "target_node_id": target,
                                "condition": (
                                    condition.model_dump(mode="json")
                                    if condition is not None
                                    else None
                                ),
                            }
                        )
                    )
        return expanded

    def _expand_bindings(
        self,
        bindings: list[PortBinding],
        base_node_id: str,
        generated: tuple[str, ...],
        nodes: Mapping[str, WorkflowNode],
    ) -> list[PortBinding]:
        expanded: list[PortBinding] = []
        for binding in bindings:
            sources = generated if binding.source_node_id == base_node_id else (binding.source_node_id,)
            targets = generated if binding.destination_node_id == base_node_id else (binding.destination_node_id,)
            if len(sources) > 1 and binding.destination_node_id != base_node_id:
                target = nodes.get(binding.destination_node_id)
                if target is not None:
                    port = next(
                        (
                            item
                            for item in target.input_ports
                            if item.port_identifier == binding.destination_port_id
                        ),
                        None,
                    )
                    if port is not None and not port.multiple:
                        raise WorkflowStateIntegrityError(
                            "Sweep fan-out requires a multiple destination input port."
                        )
            if sources == (binding.source_node_id,) and targets == (binding.destination_node_id,):
                expanded.append(binding)
                continue
            for source in sources:
                for target in targets:
                    expanded.append(
                        PortBinding.model_validate(
                            {
                                **binding.model_dump(mode="json"),
                                "binding_identifier": self._stable_identifier(
                                    "expanded-binding",
                                    {
                                        "binding": binding.binding_identifier,
                                        "source": source,
                                        "target": target,
                                    },
                                ),
                                "source_node_id": source,
                                "destination_node_id": target,
                            }
                        )
                    )
        return expanded

    def _normalize_external_inputs(
        self,
        inputs: tuple[ExternalInputState, ...],
        *,
        run_id: str,
        runtime_graph: WorkflowGraphDefinition,
    ) -> tuple[ExternalInputState, ...]:
        nodes = {item.node_identifier: item for item in runtime_graph.nodes}
        normalized: list[ExternalInputState] = []
        for supplied in inputs:
            node = nodes.get(supplied.node_id)
            if node is None:
                raise WorkflowStateIntegrityError(
                    "External input references an unknown workflow node."
                )
            port = next(
                (
                    item
                    for item in node.input_ports
                    if item.port_identifier == supplied.port_id
                ),
                None,
            )
            if port is None or not port.external:
                raise WorkflowStateIntegrityError(
                    "External input references a non-external workflow port."
                )
            if port.port_type is PortType.ARTIFACT and supplied.artifact_reference is None:
                raise WorkflowStateIntegrityError(
                    "Artifact ports require external artifact references."
                )
            if port.port_type is not PortType.ARTIFACT and supplied.value is None:
                raise WorkflowStateIntegrityError(
                    "Scalar ports require external scalar values."
                )
            normalized.append(
                ExternalInputState.model_validate(
                    {
                        **supplied.model_dump(mode="json"),
                        "graph_run_identifier": run_id,
                    }
                )
            )
        supplied_ports = {(item.node_id, item.port_id) for item in normalized}
        for node in runtime_graph.nodes:
            for port in node.input_ports:
                if (
                    port.external
                    and port.required
                    and port.default_value is None
                    and (node.node_identifier, port.port_identifier) not in supplied_ports
                ):
                    raise WorkflowStateIntegrityError(
                        "A required external workflow input was not supplied."
                    )
        return tuple(normalized)

    def _initial_approval_states(
        self,
        graph: WorkflowGraphDefinition,
        *,
        run_id: str,
        now: float,
    ) -> tuple[ApprovalState, ...]:
        requirements: dict[str, ApprovalRequirement] = {}
        for node in graph.nodes:
            policy = node.execution_policy or graph.global_execution_policy
            for requirement in policy.approval_requirements if policy else ():
                previous = requirements.get(requirement.approval_identifier)
                if previous is not None and previous != requirement:
                    raise WorkflowStateIntegrityError(
                        "Approval identifiers cannot bind multiple contexts."
                    )
                requirements[requirement.approval_identifier] = requirement
        policy = graph.global_execution_policy
        for requirement in policy.approval_requirements if policy else ():
            previous = requirements.get(requirement.approval_identifier)
            if previous is not None and previous != requirement:
                raise WorkflowStateIntegrityError(
                    "Approval identifiers cannot bind multiple contexts."
                )
            requirements[requirement.approval_identifier] = requirement
        return tuple(
            ApprovalState(
                approval_id=requirement.approval_identifier,
                graph_run_identifier=run_id,
                graph_identifier=graph.graph_identifier,
                graph_version=graph.version,
                graph_definition_fingerprint=graph.definition_fingerprint,
                node_id=requirement.node_identifier,
                operation_identity=requirement.operation_identity,
                capability_identity=requirement.capability_identity,
                input_binding_ids=requirement.input_binding_ids,
                cost_ceiling=requirement.cost_ceiling,
                resource_ceilings=requirement.resource_ceilings,
                status=ApprovalStatus.PENDING,
                requested_at_epoch=now,
                expiry_epoch=requirement.validity_epoch,
            )
            for requirement in sorted(
                requirements.values(), key=lambda item: item.approval_identifier
            )
        )

    def _initial_budget(
        self, graph: WorkflowGraphDefinition, *, run_id: str, now: float
    ) -> BudgetState:
        estimates: dict[str, float] = {}
        for node in graph.nodes:
            if node.estimated_cost is not None:
                key = f"cost.{node.estimated_cost_currency}"
                estimates[key] = estimates.get(key, 0.0) + node.estimated_cost
            for key, value in node.estimated_resources.items():
                estimates[key] = estimates.get(key, 0.0) + float(value)
        ceilings = {
            **{f"cost.{item.currency}": item.amount for item in graph.cost_ceilings},
            **{
                f"{item.resource_type}.{item.unit}": item.quantity
                for item in graph.resource_ceilings
            },
        }
        remaining = dict(ceilings)
        return BudgetState(
            graph_run_identifier=run_id,
            planning_estimates=FrozenDict(sorted(estimates.items())),
            ceiling_values=FrozenDict(sorted(ceilings.items())),
            remaining_budget=FrozenDict(sorted(remaining.items())),
            status=BudgetStatus.WITHIN_BUDGET,
            terminal_over_budget=False,
            last_updated_epoch=now,
        )

    def _recover_interrupted_nodes(
        self, snapshot: WorkflowRunSnapshot
    ) -> WorkflowRunSnapshot:
        """Requeue persisted in-flight attempts through their stable invocation identities."""

        latest = self._latest_states(snapshot)
        interrupted = tuple(
            sorted(
                node_id
                for node_id, state in latest.items()
                if state.status is NodeStatus.RUNNING
            )
        )
        if not interrupted:
            return snapshot
        now = self.clock()
        states: list[NodeState] = []
        interrupted_set = set(interrupted)
        for state in snapshot.node_states:
            if (
                state.node_id in interrupted_set
                and state is latest[state.node_id]
                and state.status is NodeStatus.RUNNING
            ):
                metadata = dict(state.execution_metadata)
                metadata["recovery_action"] = "stable_invocation_replay"
                states.append(
                    self._replace_node_state(
                        state,
                        status=NodeStatus.RECOVERED,
                        execution_metadata=FrozenDict(metadata),
                    )
                )
            else:
                states.append(state)
        evidence_fingerprint = sha256_fingerprint(
            {
                "run": snapshot.graph_state.graph_run_identifier,
                "graph": snapshot.graph_state.graph_definition_fingerprint,
                "interrupted_nodes": interrupted,
                "invocations": tuple(
                    self._invocation_identifier(snapshot, latest[node_id])
                    for node_id in interrupted
                ),
            }
        )
        decision = DecisionState(
            decision_identifier=self._stable_identifier(
                "decision",
                {
                    "run": snapshot.graph_state.graph_run_identifier,
                    "kind": DecisionKind.RECOVERY.value,
                    "revision": snapshot.revision + 1,
                    "evidence": evidence_fingerprint,
                },
            ),
            graph_run_identifier=snapshot.graph_state.graph_run_identifier,
            decision_kind=DecisionKind.RECOVERY,
            subject_identifier=snapshot.graph_state.graph_run_identifier,
            input_fingerprint=evidence_fingerprint,
            outcome="interrupted_nodes_requeued",
            decided_at_epoch=now,
            related_node_ids=interrupted,
            metadata=FrozenDict({"replay_identity": "stable_invocation"}),
        )
        graph_state = GraphState.model_validate(
            {
                **snapshot.graph_state.model_dump(mode="json"),
                "status": GraphStatus.RECOVERED.value,
                "started_at_epoch": snapshot.graph_state.started_at_epoch or now,
                "current_node_id": None,
                "recovery_evidence_fingerprint": evidence_fingerprint,
            }
        )
        return self._snapshot(
            snapshot,
            graph_state=graph_state,
            node_states=tuple(states),
            decision_states=(*snapshot.decision_states, decision),
        )

    def _expire_approvals(self, snapshot: WorkflowRunSnapshot) -> WorkflowRunSnapshot:
        now = self.clock()
        changed = False
        approvals: list[ApprovalState] = []
        for approval in snapshot.approval_states:
            if (
                approval.status is ApprovalStatus.PENDING
                and approval.expiry_epoch is not None
                and now >= approval.expiry_epoch
            ):
                changed = True
                approvals.append(
                    ApprovalState.model_validate(
                        {
                            **approval.model_dump(mode="json"),
                            "status": ApprovalStatus.EXPIRED.value,
                            "approver_principal": "system.workflow",
                            "decided_at_epoch": now,
                            "decision_reason": "Approval validity expired.",
                        }
                    )
                )
            else:
                approvals.append(approval)
        return (
            self._snapshot(snapshot, approval_states=tuple(approvals))
            if changed
            else snapshot
        )

    def _evaluate_conditions(
        self, snapshot: WorkflowRunSnapshot, graph: WorkflowGraphDefinition
    ) -> WorkflowRunSnapshot:
        latest = self._latest_states(snapshot)
        edge_states = {item.edge_id: item for item in snapshot.edge_states}
        decisions = list(snapshot.decision_states)
        changed = False
        now = self.clock()
        for edge in graph.edges:
            if edge.edge_kind is not EdgeKind.CONDITIONAL or edge.condition is None:
                continue
            state = edge_states[edge.edge_identifier]
            if state.condition_evaluated:
                continue
            source = latest[edge.source_node_id]
            if source.status not in _TERMINAL_NODE_STATUSES:
                continue
            outcome = self._condition_result(
                edge.condition.condition_kind,
                edge.condition.condition_expression,
                source,
                snapshot,
            )
            changed = True
            edge_states[edge.edge_identifier] = EdgeState(
                edge_id=edge.edge_identifier,
                graph_run_identifier=snapshot.graph_state.graph_run_identifier,
                source_node_id=edge.source_node_id,
                target_node_id=edge.target_node_id,
                condition_evaluated=True,
                condition_result=outcome,
                activated=outcome,
                activation_epoch=now if outcome else None,
            )
            input_fingerprint = sha256_fingerprint(
                {
                    "condition": edge.condition.model_dump(mode="json"),
                    "source_state": source.model_dump(mode="json"),
                }
            )
            decisions.append(
                DecisionState(
                    decision_identifier=self._stable_identifier(
                        "decision",
                        {
                            "run": snapshot.graph_state.graph_run_identifier,
                            "edge": edge.edge_identifier,
                            "outcome": outcome,
                        },
                    ),
                    graph_run_identifier=snapshot.graph_state.graph_run_identifier,
                    decision_kind=DecisionKind.CONDITION,
                    subject_identifier=edge.edge_identifier,
                    input_fingerprint=input_fingerprint,
                    outcome="true" if outcome else "false",
                    decided_at_epoch=now,
                    related_node_ids=(edge.source_node_id, edge.target_node_id),
                )
            )
        if not changed:
            return snapshot
        return self._snapshot(
            snapshot,
            edge_states=tuple(edge_states.values()),
            decision_states=tuple(decisions),
        )

    def _condition_result(
        self,
        kind: ConditionKind,
        expression: Mapping[str, Any],
        source: NodeState,
        snapshot: WorkflowRunSnapshot,
    ) -> bool:
        if kind is ConditionKind.SUCCESS:
            return source.status is NodeStatus.SUCCEEDED
        if kind is ConditionKind.FAILURE:
            return source.status in {NodeStatus.FAILED, NodeStatus.BLOCKED}
        if kind is ConditionKind.ARTIFACT_PRESENT:
            artifact_identifier = expression.get("artifact_identifier")
            artifact_type = expression.get("artifact_type")
            return any(
                (artifact_identifier is None or item.artifact_identifier == artifact_identifier)
                and (artifact_type is None or item.artifact_type == artifact_type)
                for item in source.output_artifacts
            )
        if kind is ConditionKind.VERIFICATION_CLASS:
            return source.execution_metadata.get("verification_classification") == expression.get(
                "equals"
            )
        if kind is ConditionKind.FEASIBILITY_CLASS:
            return source.execution_metadata.get("feasibility_status") == expression.get(
                "equals"
            )
        if kind is ConditionKind.APPROVAL_RESULT:
            approval_id = expression.get("approval_identifier")
            expected = expression.get("status")
            return any(
                item.approval_id == approval_id and item.status.value == expected
                for item in snapshot.approval_states
            )
        if kind is ConditionKind.BUDGET_REMAINING:
            dimension = expression.get("dimension")
            value = snapshot.budget_state.remaining_budget.get(dimension)
            return self._numeric_compare(value, expression)
        if kind is ConditionKind.CANDIDATE_SELECTED:
            selected = source.output_values.get("selected_node_identifier")
            return selected == expression.get("node_identifier")
        if kind is ConditionKind.NUMERIC_COMPARE:
            source_key = expression.get("source")
            value: Any = None
            if source_key == "cost":
                value = source.cost_consumed
            elif isinstance(source_key, str) and source_key.startswith("resource."):
                value = source.resources_consumed.get(source_key.removeprefix("resource."))
            elif isinstance(source_key, str) and source_key.startswith("output."):
                value = source.output_values.get(source_key.removeprefix("output."))
            return self._numeric_compare(value, expression)
        raise WorkflowStateIntegrityError("Unsupported workflow condition kind.")

    @staticmethod
    def _numeric_compare(value: Any, expression: Mapping[str, Any]) -> bool:
        threshold = expression.get("threshold")
        operator = expression.get("operator")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not math.isfinite(float(value))
            or not math.isfinite(float(threshold))
        ):
            return False
        left, right = float(value), float(threshold)
        return {
            "lt": left < right,
            "le": left <= right,
            "eq": left == right,
            "ne": left != right,
            "ge": left >= right,
            "gt": left > right,
        }.get(str(operator), False)

    def _refresh_statuses(
        self,
        snapshot: WorkflowRunSnapshot,
        graph: WorkflowGraphDefinition | None = None,
    ) -> WorkflowRunSnapshot:
        if graph is None:
            stored = self.graph_repository.get(
                snapshot.graph_state.graph_identifier,
                snapshot.graph_state.graph_version,
            )
            graph = self._expand_graph(stored).definition
        latest = self._latest_states(snapshot)
        approvals_by_node: dict[str, list[ApprovalState]] = {}
        global_approvals: list[ApprovalState] = []
        for approval in snapshot.approval_states:
            if approval.node_id is None:
                global_approvals.append(approval)
            else:
                approvals_by_node.setdefault(approval.node_id, []).append(approval)
        incoming: dict[str, list[WorkflowEdge]] = {
            item.node_identifier: [] for item in graph.nodes
        }
        for edge in graph.edges:
            incoming[edge.target_node_id].append(edge)
        edge_states = {item.edge_id: item for item in snapshot.edge_states}
        now = self.clock()
        replaced: dict[str, NodeState] = {}
        for node in graph.nodes:
            state = latest[node.node_identifier]
            if state.status in _TERMINAL_NODE_STATUSES or state.status is NodeStatus.RUNNING:
                continue
            approvals = (*global_approvals, *approvals_by_node.get(node.node_identifier, ()))
            if any(item.status in {ApprovalStatus.DENIED, ApprovalStatus.EXPIRED} for item in approvals):
                replaced[node.node_identifier] = self._replace_node_state(
                    state,
                    status=NodeStatus.BLOCKED,
                    completed_at_epoch=now,
                    approval_status=next(
                        item.status
                        for item in approvals
                        if item.status in {ApprovalStatus.DENIED, ApprovalStatus.EXPIRED}
                    ),
                    error_code="approval_denied",
                    error_message="Workflow approval was denied or expired.",
                )
                continue
            if any(item.status is ApprovalStatus.PENDING for item in approvals):
                replaced[node.node_identifier] = self._replace_node_state(
                    state,
                    status=NodeStatus.AWAITING_APPROVAL,
                    approval_status=ApprovalStatus.PENDING,
                )
                continue
            if state.status is NodeStatus.RETRY_SCHEDULED:
                # Preserve the persisted not-before state. _ready_nodes releases the
                # retry only when its RetryState scheduling timestamp is due.
                continue
            node_incoming = incoming[node.node_identifier]
            conditional = [item for item in node_incoming if item.edge_kind is EdgeKind.CONDITIONAL]
            ordinary = [item for item in node_incoming if item.edge_kind is not EdgeKind.CONDITIONAL]
            if any(
                latest[item.source_node_id].status in {NodeStatus.FAILED, NodeStatus.BLOCKED, NodeStatus.CANCELLED}
                for item in ordinary
            ):
                replaced[node.node_identifier] = self._replace_node_state(
                    state,
                    status=NodeStatus.BLOCKED,
                    completed_at_epoch=now,
                    error_code="upstream_failed",
                    error_message="An upstream workflow dependency did not succeed.",
                )
                continue
            ordinary_ready = all(
                latest[item.source_node_id].status
                in {
                    NodeStatus.SUCCEEDED,
                    NodeStatus.SKIPPED_BY_CONDITION,
                    NodeStatus.SKIPPED_BY_POLICY,
                }
                for item in ordinary
            )
            conditional_states = [edge_states[item.edge_identifier] for item in conditional]
            if conditional and all(item.condition_evaluated for item in conditional_states):
                active = [item for item in conditional_states if item.activated]
                if not active:
                    replaced[node.node_identifier] = self._replace_node_state(
                        state,
                        status=NodeStatus.SKIPPED_BY_CONDITION,
                        completed_at_epoch=now,
                        condition_outcome=ConditionOutcome.FALSE,
                    )
                    continue
                conditional_ready = all(
                    latest[item.source_node_id].status is NodeStatus.SUCCEEDED
                    for item in conditional
                    if edge_states[item.edge_identifier].activated
                )
            else:
                conditional_ready = not conditional
            if ordinary_ready and conditional_ready:
                replaced[node.node_identifier] = self._replace_node_state(
                    state,
                    status=NodeStatus.READY,
                    approval_status=(ApprovalStatus.GRANTED if approvals else None),
                )
            elif state.status not in {NodeStatus.RETRY_SCHEDULED, NodeStatus.PENDING}:
                replaced[node.node_identifier] = self._replace_node_state(
                    state, status=NodeStatus.PENDING
                )
        if not replaced:
            return self._update_graph_state(snapshot)
        states = tuple(
            replaced.get(state.node_id, state)
            if state is latest[state.node_id]
            else state
            for state in snapshot.node_states
        )
        return self._update_graph_state(self._snapshot(snapshot, node_states=states))

    def _reserve_ready_nodes(
        self,
        snapshot: WorkflowRunSnapshot,
        graph: WorkflowGraphDefinition,
        ready: tuple[str, ...],
    ) -> tuple[tuple[str, ...], WorkflowRunSnapshot]:
        nodes = {item.node_identifier: item for item in graph.nodes}
        budget = snapshot.budget_state
        reserved = dict(budget.reserved_budget)
        actual = dict(budget.actual_consumption)
        ceilings = dict(budget.ceiling_values)
        selected: list[str] = []
        blocked: dict[str, NodeState] = {}
        latest = self._latest_states(snapshot)
        decisions = list(snapshot.decision_states)
        now = self.clock()
        for node_id in ready:
            node = nodes[node_id]
            estimates = self._node_estimates(node)
            exceeded = [
                key
                for key, value in estimates.items()
                if key in ceilings
                and actual.get(key, 0.0) + reserved.get(key, 0.0) + value > ceilings[key]
            ]
            if exceeded:
                blocked[node_id] = self._replace_node_state(
                    latest[node_id],
                    status=NodeStatus.BLOCKED,
                    completed_at_epoch=now,
                    error_code="budget_exhausted",
                    error_message="Workflow budget ceiling would be exceeded.",
                )
                decisions.append(
                    DecisionState(
                        decision_identifier=self._stable_identifier(
                            "decision",
                            {
                                "run": snapshot.graph_state.graph_run_identifier,
                                "node": node_id,
                                "budget": exceeded,
                                "revision": snapshot.revision,
                            },
                        ),
                        graph_run_identifier=snapshot.graph_state.graph_run_identifier,
                        decision_kind=DecisionKind.BUDGET,
                        subject_identifier=node_id,
                        input_fingerprint=sha256_fingerprint(
                            {
                                "estimates": estimates,
                                "reserved": reserved,
                                "actual": actual,
                                "ceilings": ceilings,
                            }
                        ),
                        outcome="blocked",
                        decided_at_epoch=now,
                        related_node_ids=(node_id,),
                    )
                )
                continue
            selected.append(node_id)
            for key, value in estimates.items():
                reserved[key] = reserved.get(key, 0.0) + value
        if len(selected) > self._parallelism(graph):
            selected = selected[: self._parallelism(graph)]
            # Release reservations for nodes not selected in this cycle.
            reserved = dict(budget.reserved_budget)
            for node_id in selected:
                for key, value in self._node_estimates(nodes[node_id]).items():
                    reserved[key] = reserved.get(key, 0.0) + value
        states = tuple(
            blocked.get(state.node_id, state)
            if state is latest[state.node_id]
            else state
            for state in snapshot.node_states
        )
        budget_state = self._rebuild_budget(
            budget,
            reserved=reserved,
            actual=actual,
            unknown=dict(budget.unknown_consumption),
            now=now,
        )
        updated = self._snapshot(
            snapshot,
            node_states=states,
            budget_state=budget_state,
            decision_states=tuple(decisions),
        )
        return tuple(selected), self._update_graph_state(updated)

    def _mark_running(
        self, snapshot: WorkflowRunSnapshot, selected: tuple[str, ...]
    ) -> WorkflowRunSnapshot:
        now = self.clock()
        latest = self._latest_states(snapshot)
        selected_set = set(selected)
        states = tuple(
            self._replace_node_state(
                state,
                status=NodeStatus.RUNNING,
                attempt_number=max(1, state.attempt_number),
                started_at_epoch=state.started_at_epoch or now,
                completed_at_epoch=None,
                error_code=None,
                error_message=None,
                retry_reason=None,
            )
            if state is latest[state.node_id] and state.node_id in selected_set
            else state
            for state in snapshot.node_states
        )
        graph_state = self._graph_state(
            snapshot.graph_state,
            status=GraphStatus.RUNNING,
            started_at_epoch=snapshot.graph_state.started_at_epoch or now,
            current_node_id=selected[0] if selected else None,
            completed_at_epoch=None,
            counts=self._counts(states),
        )
        return self._snapshot(snapshot, graph_state=graph_state, node_states=states)

    def _invoke_nodes(
        self,
        snapshot: WorkflowRunSnapshot,
        graph: WorkflowGraphDefinition,
        selected: tuple[str, ...],
        *,
        correlation_identifier: str | None,
    ) -> dict[str, CapabilityInvocationResult]:
        nodes = {item.node_identifier: item for item in graph.nodes}
        latest = self._latest_states(snapshot)
        intrinsic: dict[str, CapabilityInvocationResult] = {}
        invocations: dict[str, CapabilityInvocation] = {}
        for node_id in selected:
            node = nodes[node_id]
            state = latest[node_id]
            intrinsic_result = self._intrinsic_result(snapshot, graph, node, state)
            if intrinsic_result is not None:
                intrinsic[node_id] = intrinsic_result
                continue
            if node.capability_identity is None:
                intrinsic[node_id] = CapabilityInvocationResult(
                    invocation_identifier=self._invocation_identifier(snapshot, state),
                    status=CapabilityInvocationStatus.BLOCKED,
                    error_code="capability_identity_missing",
                    error_message="Workflow node does not declare a capability identity.",
                )
                continue
            if node.node_kind is NodeKind.QUANTUM_IBM:
                policy = node.quantum_escalation_policy
                if policy is None or not policy.allow_ibm_submission:
                    intrinsic[node_id] = CapabilityInvocationResult(
                        invocation_identifier=self._invocation_identifier(snapshot, state),
                        status=CapabilityInvocationStatus.BLOCKED,
                        error_code="ibm_submission_not_authorized",
                        error_message="IBM Quantum submission is not authorized by policy.",
                    )
                    continue
                granted = {
                    item.approval_id
                    for item in snapshot.approval_states
                    if item.status is ApprovalStatus.GRANTED
                }
                if not set(policy.required_approvals).issubset(granted):
                    intrinsic[node_id] = CapabilityInvocationResult(
                        invocation_identifier=self._invocation_identifier(snapshot, state),
                        status=CapabilityInvocationStatus.BLOCKED,
                        error_code="ibm_approval_missing",
                        error_message="IBM Quantum submission approval is incomplete.",
                    )
                    continue
            invocations[node_id] = self._build_invocation(
                snapshot,
                graph,
                node,
                state,
                correlation_identifier=correlation_identifier,
            )
        results = dict(intrinsic)
        if not invocations:
            return results
        workers = min(self._parallelism(graph), len(invocations))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                node_id: pool.submit(self.capability_adapter.invoke, invocation)
                for node_id, invocation in invocations.items()
            }
            for node_id in sorted(futures):
                future = futures[node_id]
                timeout = invocations[node_id].timeout_seconds
                try:
                    results[node_id] = future.result(timeout=timeout)
                except concurrent.futures.TimeoutError:
                    future.cancel()
                    results[node_id] = CapabilityInvocationResult(
                        invocation_identifier=invocations[node_id].invocation_identifier,
                        status=CapabilityInvocationStatus.FAILED,
                        retryable=True,
                        error_code="capability_timeout",
                        error_message="Capability invocation exceeded its bounded timeout.",
                    )
                except Exception:
                    results[node_id] = CapabilityInvocationResult(
                        invocation_identifier=invocations[node_id].invocation_identifier,
                        status=CapabilityInvocationStatus.FAILED,
                        retryable=False,
                        error_code="capability_adapter_failure",
                        error_message="Capability adapter failed without trusted result evidence.",
                    )
        return results

    def _intrinsic_result(
        self,
        snapshot: WorkflowRunSnapshot,
        graph: WorkflowGraphDefinition,
        node: WorkflowNode,
        state: NodeState,
    ) -> CapabilityInvocationResult | None:
        invocation_id = self._invocation_identifier(snapshot, state)
        if node.capability_identity is not None:
            return None
        if node.node_kind in {
            NodeKind.APPROVAL_GATE,
            NodeKind.CONDITIONAL_BRANCH,
            NodeKind.DATA_SOURCE,
            NodeKind.DATA_SINK,
        }:
            return CapabilityInvocationResult(
                invocation_identifier=invocation_id,
                status=CapabilityInvocationStatus.SUCCEEDED,
                output_values=FrozenDict(dict(node.parameters)),
                execution_metadata=FrozenDict({"intrinsic": node.node_kind.value}),
            )
        if node.node_kind in {NodeKind.COMPARISON, NodeKind.AGGREGATION}:
            selected = self._comparison_selection(snapshot, graph, node.node_identifier)
            return CapabilityInvocationResult(
                invocation_identifier=invocation_id,
                status=CapabilityInvocationStatus.SUCCEEDED,
                output_values=FrozenDict(
                    {
                        "selected_node_identifier": selected[0] if len(selected) == 1 else None,
                        "selected_node_identifiers": selected,
                    }
                ),
                execution_metadata=FrozenDict({"intrinsic": "comparison"}),
            )
        return None

    def _comparison_selection(
        self,
        snapshot: WorkflowRunSnapshot,
        graph: WorkflowGraphDefinition,
        aggregation_node_id: str,
    ) -> tuple[str, ...]:
        group = next(
            (
                item
                for item in graph.comparison_groups
                if item.aggregation_node_id == aggregation_node_id
            ),
            None,
        )
        if group is None:
            return ()
        latest = self._latest_states(snapshot)
        succeeded = [
            latest[item]
            for item in group.member_node_ids
            if latest[item].status is NodeStatus.SUCCEEDED
        ]
        if not succeeded:
            return ()
        if group.comparison_kind is ComparisonKind.ALL_COMPLETE:
            return tuple(sorted(item.node_id for item in succeeded))
        if group.comparison_kind is ComparisonKind.FIRST_SUCCESS:
            return (min(item.node_id for item in succeeded),)
        criteria = group.selection_criteria or FrozenDict()
        metric = criteria.get("metric")
        direction = criteria.get("direction", "min")
        candidates: list[tuple[float, str]] = []
        if isinstance(metric, str):
            for state in succeeded:
                value = state.output_values.get(metric)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    candidates.append((float(value), state.node_id))
        if not candidates:
            return (min(item.node_id for item in succeeded),)
        candidates.sort(key=lambda item: (item[0], item[1]))
        return ((candidates[-1] if direction == "max" else candidates[0])[1],)

    def _build_invocation(
        self,
        snapshot: WorkflowRunSnapshot,
        graph: WorkflowGraphDefinition,
        node: WorkflowNode,
        state: NodeState,
        *,
        correlation_identifier: str | None,
    ) -> CapabilityInvocation:
        artifacts: list[ArtifactPointer] = []
        values: dict[str, Any] = {}
        for external in snapshot.external_inputs:
            if external.node_id != node.node_identifier:
                continue
            if external.artifact_reference is not None:
                artifacts.append(external.artifact_reference)
            else:
                values[external.port_id] = external.value
        latest = self._latest_states(snapshot)
        for binding in graph.bindings:
            if binding.destination_node_id != node.node_identifier:
                continue
            source = latest[binding.source_node_id]
            source_node = graph.get_node(binding.source_node_id)
            if source_node is None:
                continue
            port = next(
                (
                    item
                    for item in source_node.output_ports
                    if item.port_identifier == binding.source_port_id
                ),
                None,
            )
            if port is None:
                continue
            if port.port_type is PortType.ARTIFACT:
                matches = [
                    item.pointer
                    for item in source.output_artifacts
                    if item.artifact_type == port.artifact_kind
                ]
                artifacts.extend(matches)
            else:
                if binding.source_port_id in source.output_values:
                    values[binding.destination_port_id] = source.output_values[
                        binding.source_port_id
                    ]
        policy = node.execution_policy or graph.global_execution_policy
        invocation_id = self._invocation_identifier(snapshot, state)
        return CapabilityInvocation(
            invocation_identifier=invocation_id,
            idempotency_identifier=self._stable_identifier(
                "idempotency",
                {
                    "run": snapshot.graph_state.graph_run_identifier,
                    "node": node.node_identifier,
                    "attempt": state.attempt_number,
                    "graph": snapshot.graph_state.graph_definition_fingerprint,
                },
            ),
            graph_run_identifier=snapshot.graph_state.graph_run_identifier,
            graph_definition_fingerprint=snapshot.graph_state.graph_definition_fingerprint,
            node_identifier=node.node_identifier,
            attempt_number=state.attempt_number,
            capability_identity=node.capability_identity or "missing",
            node_kind=node.node_kind,
            molecular_project_identifier=node.molecular_project_identifier,
            molecular_system_identifiers=node.molecular_system_identifiers,
            molecular_region_identifiers=node.molecular_region_identifiers,
            execution_target=node.execution_target,
            input_artifacts=tuple(artifacts),
            input_values=FrozenDict(values),
            parameters=node.parameters,
            timeout_seconds=policy.timeout_seconds if policy else None,
            approved=self._node_approved(snapshot, node.node_identifier),
            correlation_identifier=correlation_identifier,
        )

    def _apply_results(
        self,
        snapshot: WorkflowRunSnapshot,
        graph: WorkflowGraphDefinition,
        results: Mapping[str, CapabilityInvocationResult],
    ) -> WorkflowRunSnapshot:
        latest = self._latest_states(snapshot)
        nodes = {item.node_identifier: item for item in graph.nodes}
        states = list(snapshot.node_states)
        retries = list(snapshot.retry_states)
        decisions = list(snapshot.decision_states)
        budget = snapshot.budget_state
        reserved = dict(budget.reserved_budget)
        actual = dict(budget.actual_consumption)
        unknown = dict(budget.unknown_consumption)
        now = self.clock()
        replacements: dict[tuple[str, int], NodeState] = {}
        additions: list[NodeState] = []
        for node_id in sorted(results):
            result = results[node_id]
            state = latest[node_id]
            node = nodes[node_id]
            expected_invocation = self._invocation_identifier(snapshot, state)
            if result.invocation_identifier != expected_invocation:
                result = CapabilityInvocationResult(
                    invocation_identifier=expected_invocation,
                    status=CapabilityInvocationStatus.FAILED,
                    error_code="result_identity_mismatch",
                    error_message="Capability result identity did not match its invocation.",
                )
            if state.attempt_number > 1:
                retry_outcome = (
                    RetryOutcome.SUCCEEDED
                    if result.status is CapabilityInvocationStatus.SUCCEEDED
                    else RetryOutcome.CANCELLED
                    if result.status is CapabilityInvocationStatus.CANCELLED
                    else RetryOutcome.FAILED
                )
                for retry_index, retry in enumerate(retries):
                    if (
                        retry.node_id == state.node_id
                        and retry.attempt_number == state.attempt_number
                        and retry.outcome is None
                    ):
                        retries[retry_index] = RetryState.model_validate(
                            {
                                **retry.model_dump(mode="json"),
                                "executed_at_epoch": now,
                                "outcome": retry_outcome.value,
                            }
                        )
                        break
            estimates = self._node_estimates(node)
            for key, value in estimates.items():
                reserved[key] = max(reserved.get(key, 0.0) - value, 0.0)
            if node.estimated_cost is not None:
                cost_key = f"cost.{node.estimated_cost_currency}"
                if result.actual_cost is None:
                    unknown[cost_key] = unknown.get(cost_key, 0.0) + 1.0
                else:
                    actual[cost_key] = actual.get(cost_key, 0.0) + result.actual_cost
            for key in node.estimated_resources:
                if key not in result.resources_consumed:
                    unknown[key] = unknown.get(key, 0.0) + 1.0
            for key, value in result.resources_consumed.items():
                actual[key] = actual.get(key, 0.0) + float(value)
            for key in result.unknown_consumption:
                unknown[key] = unknown.get(key, 0.0) + 1.0
            policy = node.execution_policy or graph.global_execution_policy
            unknown_for_node = bool(
                set(result.unknown_consumption)
                or (node.estimated_cost is not None and result.actual_cost is None)
                or set(node.estimated_resources) - set(result.resources_consumed)
            )
            if (
                result.status is CapabilityInvocationStatus.SUCCEEDED
                and policy is not None
                and policy.require_known_consumption
                and unknown_for_node
            ):
                result = CapabilityInvocationResult(
                    invocation_identifier=result.invocation_identifier,
                    status=CapabilityInvocationStatus.BLOCKED,
                    error_code="consumption_unknown",
                    error_message="Known consumption evidence is required by policy.",
                )
            exceeded_actual = tuple(
                sorted(
                    key
                    for key, ceiling in budget.ceiling_values.items()
                    if float(actual.get(key, 0.0)) > float(ceiling)
                )
            )
            if (
                result.status is CapabilityInvocationStatus.SUCCEEDED
                and exceeded_actual
            ):
                decisions.append(
                    DecisionState(
                        decision_identifier=self._stable_identifier(
                            "decision",
                            {
                                "run": snapshot.graph_state.graph_run_identifier,
                                "node": node_id,
                                "actual_over_budget": exceeded_actual,
                                "attempt": state.attempt_number,
                            },
                        ),
                        graph_run_identifier=snapshot.graph_state.graph_run_identifier,
                        decision_kind=DecisionKind.BUDGET,
                        subject_identifier=node_id,
                        input_fingerprint=sha256_fingerprint(
                            {
                                "actual": actual,
                                "ceilings": dict(budget.ceiling_values),
                            }
                        ),
                        outcome="actual_over_budget",
                        decided_at_epoch=now,
                        related_node_ids=(node_id,),
                        metadata=FrozenDict(
                            {"exceeded_dimensions": ",".join(exceeded_actual)}
                        ),
                    )
                )
                result = CapabilityInvocationResult(
                    invocation_identifier=result.invocation_identifier,
                    status=CapabilityInvocationStatus.BLOCKED,
                    error_code="actual_budget_exceeded",
                    error_message=(
                        "Reported capability consumption exceeded the workflow budget ceiling."
                    ),
                )
            metadata = dict(result.execution_metadata)
            if result.verification_classification is not None:
                metadata["verification_classification"] = result.verification_classification
            if node.metadata.get("feasibility_status") is not None:
                metadata["feasibility_status"] = node.metadata["feasibility_status"]
            if (
                result.status is CapabilityInvocationStatus.SUCCEEDED
                and node.node_kind in {NodeKind.COMPARISON, NodeKind.AGGREGATION}
            ):
                selected_nodes = result.output_values.get("selected_node_identifiers", ())
                if isinstance(selected_nodes, tuple):
                    related_nodes = tuple(
                        item for item in selected_nodes if isinstance(item, str)
                    )
                else:
                    related_nodes = ()
                decisions.append(
                    DecisionState(
                        decision_identifier=self._stable_identifier(
                            "decision",
                            {
                                "run": snapshot.graph_state.graph_run_identifier,
                                "comparison": node_id,
                                "result": result.fingerprint,
                            },
                        ),
                        graph_run_identifier=snapshot.graph_state.graph_run_identifier,
                        decision_kind=DecisionKind.COMPARISON,
                        subject_identifier=node_id,
                        input_fingerprint=result.fingerprint,
                        outcome="selected" if related_nodes else "no_selection",
                        decided_at_epoch=now,
                        related_node_ids=related_nodes,
                    )
                )
            if result.status is CapabilityInvocationStatus.SUCCEEDED:
                replacements[(state.node_id, state.attempt_number)] = self._replace_node_state(
                    state,
                    status=NodeStatus.SUCCEEDED,
                    completed_at_epoch=now,
                    output_artifacts=result.output_artifacts,
                    output_values=result.output_values,
                    produced_artifact_types=tuple(
                        sorted(item.artifact_type for item in result.output_artifacts)
                    ),
                    cost_consumed=result.actual_cost,
                    resources_consumed=result.resources_consumed,
                    execution_metadata=FrozenDict(metadata),
                )
            elif result.status is CapabilityInvocationStatus.CANCELLED:
                replacements[(state.node_id, state.attempt_number)] = self._replace_node_state(
                    state,
                    status=NodeStatus.CANCELLED,
                    completed_at_epoch=now,
                    error_code=result.error_code,
                    error_message=result.error_message,
                )
            elif result.status is CapabilityInvocationStatus.BLOCKED:
                replacements[(state.node_id, state.attempt_number)] = self._replace_node_state(
                    state,
                    status=NodeStatus.BLOCKED,
                    completed_at_epoch=now,
                    error_code=result.error_code,
                    error_message=result.error_message,
                )
            else:
                retry_policy = node.retry_policy or graph.global_retry_policy
                retry_allowed = (
                    result.retryable
                    and retry_policy is not None
                    and state.attempt_number < state.max_attempts
                    and (
                        not retry_policy.retryable_error_codes
                        or result.error_code in retry_policy.retryable_error_codes
                    )
                )
                recovery_policy = (
                    node.failure_recovery_policy
                    or graph.global_failure_recovery_policy
                )
                failure_action = (
                    recovery_policy.on_failure
                    if recovery_policy is not None
                    else FailureAction.STOP
                )
                terminal_status = (
                    NodeStatus.SKIPPED_BY_POLICY
                    if not retry_allowed
                    and failure_action in {FailureAction.SKIP, FailureAction.FALLBACK}
                    else NodeStatus.FAILED
                )
                failure_metadata = dict(metadata)
                failure_metadata["failure_action"] = failure_action.value
                replacements[(state.node_id, state.attempt_number)] = self._replace_node_state(
                    state,
                    status=terminal_status,
                    completed_at_epoch=now,
                    error_code=result.error_code,
                    error_message=result.error_message,
                    execution_metadata=FrozenDict(failure_metadata),
                )
                if retry_allowed:
                    next_attempt = state.attempt_number + 1
                    retry_id = self._stable_identifier(
                        "retry",
                        {
                            "run": snapshot.graph_state.graph_run_identifier,
                            "node": node_id,
                            "attempt": next_attempt,
                        },
                    )
                    retry_delay = min(
                        retry_policy.retry_delay_seconds
                        * (retry_policy.backoff_multiplier ** max(state.attempt_number - 1, 0)),
                        retry_policy.max_delay_seconds
                        if retry_policy.max_delay_seconds is not None
                        else float("inf"),
                    )
                    retry_not_before = now + retry_delay
                    retries.append(
                        RetryState(
                            retry_id=retry_id,
                            graph_run_identifier=snapshot.graph_state.graph_run_identifier,
                            node_id=node_id,
                            attempt_number=next_attempt,
                            original_failure_code=result.error_code or "unknown_failure",
                            original_failure_message=result.error_message
                            or "Capability execution failed.",
                            scheduled_at_epoch=retry_not_before,
                            executed_at_epoch=None,
                            outcome=None,
                            retry_policy_fingerprint=retry_policy.fingerprint,
                        )
                    )
                    additions.append(
                        NodeState(
                            node_id=node_id,
                            graph_run_identifier=snapshot.graph_state.graph_run_identifier,
                            graph_definition_fingerprint=snapshot.graph_state.graph_definition_fingerprint,
                            status=NodeStatus.RETRY_SCHEDULED,
                            attempt_number=next_attempt,
                            max_attempts=state.max_attempts,
                            retry_reason=result.error_message,
                        )
                    )
                    decisions.append(
                        DecisionState(
                            decision_identifier=self._stable_identifier(
                                "decision",
                                {
                                    "run": snapshot.graph_state.graph_run_identifier,
                                    "retry": retry_id,
                                },
                            ),
                            graph_run_identifier=snapshot.graph_state.graph_run_identifier,
                            decision_kind=DecisionKind.FAILURE_PROPAGATION,
                            subject_identifier=node_id,
                            input_fingerprint=result.fingerprint,
                            outcome="retry_scheduled",
                            decided_at_epoch=now,
                            related_node_ids=(node_id,),
                        )
                    )
                elif failure_action in {FailureAction.SKIP, FailureAction.FALLBACK}:
                    fallback_node = (
                        recovery_policy.fallback_node_id
                        if recovery_policy is not None
                        and failure_action is FailureAction.FALLBACK
                        else None
                    )
                    related = (
                        (node_id, fallback_node)
                        if fallback_node is not None
                        else (node_id,)
                    )
                    decisions.append(
                        DecisionState(
                            decision_identifier=self._stable_identifier(
                                "decision",
                                {
                                    "run": snapshot.graph_state.graph_run_identifier,
                                    "node": node_id,
                                    "action": failure_action.value,
                                    "attempt": state.attempt_number,
                                },
                            ),
                            graph_run_identifier=snapshot.graph_state.graph_run_identifier,
                            decision_kind=DecisionKind.FAILURE_PROPAGATION,
                            subject_identifier=node_id,
                            input_fingerprint=result.fingerprint,
                            outcome=(
                                "fallback_activated"
                                if fallback_node is not None
                                else "skipped_by_policy"
                            ),
                            decided_at_epoch=now,
                            related_node_ids=related,
                        )
                    )
        for index, state in enumerate(states):
            replacement = replacements.get((state.node_id, state.attempt_number))
            if replacement is not None:
                states[index] = replacement
        states.extend(additions)
        budget_state = self._rebuild_budget(
            budget,
            reserved=reserved,
            actual=actual,
            unknown=unknown,
            now=now,
        )
        return self._update_graph_state(
            self._snapshot(
                snapshot,
                node_states=tuple(states),
                retry_states=tuple(retries),
                decision_states=tuple(decisions),
                budget_state=budget_state,
            )
        )

    def _transfer_bindings(
        self,
        snapshot: WorkflowRunSnapshot,
        graph: WorkflowGraphDefinition,
    ) -> WorkflowRunSnapshot:
        latest = self._latest_states(snapshot)
        binding_states = {item.binding_id: item for item in snapshot.binding_states}
        changed = False
        now = self.clock()
        for binding in graph.bindings:
            state = binding_states[binding.binding_identifier]
            if state.value_transferred:
                continue
            source_state = latest[binding.source_node_id]
            if source_state.status is not NodeStatus.SUCCEEDED:
                continue
            source_node = graph.get_node(binding.source_node_id)
            if source_node is None:
                continue
            source_port = next(
                (
                    item
                    for item in source_node.output_ports
                    if item.port_identifier == binding.source_port_id
                ),
                None,
            )
            if source_port is None:
                continue
            artifact_pointer = None
            if source_port.port_type is PortType.ARTIFACT:
                matches = [
                    item.pointer
                    for item in source_state.output_artifacts
                    if item.artifact_type == source_port.artifact_kind
                ]
                if not matches:
                    continue
                artifact_pointer = sorted(
                    matches,
                    key=lambda item: (item.artifact_identifier, item.content_sha256),
                )[0]
            elif binding.source_port_id not in source_state.output_values:
                continue
            changed = True
            binding_states[binding.binding_identifier] = BindingState(
                binding_id=binding.binding_identifier,
                graph_run_identifier=snapshot.graph_state.graph_run_identifier,
                source_node_id=binding.source_node_id,
                source_port_id=binding.source_port_id,
                destination_node_id=binding.destination_node_id,
                destination_port_id=binding.destination_port_id,
                value_transferred=True,
                artifact_reference=artifact_pointer,
                transfer_epoch=now,
            )
        return (
            self._snapshot(snapshot, binding_states=tuple(binding_states.values()))
            if changed
            else snapshot
        )

    def _finalize_or_pause(self, snapshot: WorkflowRunSnapshot) -> WorkflowRunSnapshot:
        latest = self._latest_states(snapshot)
        statuses = {item.status for item in latest.values()}
        now = self.clock()
        if all(
            status in {
                NodeStatus.SUCCEEDED,
                NodeStatus.SKIPPED_BY_CONDITION,
                NodeStatus.SKIPPED_BY_POLICY,
            }
            for status in statuses
        ):
            graph_status = GraphStatus.SUCCEEDED
            completed = now
        elif NodeStatus.AWAITING_APPROVAL in statuses:
            graph_status = GraphStatus.AWAITING_APPROVAL
            completed = None
        elif any(status is NodeStatus.BLOCKED for status in statuses):
            graph_status = GraphStatus.BLOCKED
            completed = now
        elif any(status is NodeStatus.FAILED for status in statuses):
            graph_status = GraphStatus.FAILED
            completed = now
        elif any(status is NodeStatus.RETRY_SCHEDULED for status in statuses):
            graph_status = GraphStatus.PAUSED
            completed = None
        else:
            graph_status = GraphStatus.PAUSED
            completed = None
        graph_state = self._graph_state(
            snapshot.graph_state,
            status=graph_status,
            completed_at_epoch=completed,
            current_node_id=None,
            counts=self._counts(snapshot.node_states),
            retry_count=len(snapshot.retry_states),
            error_message=(
                "Workflow execution could not continue."
                if graph_status in {GraphStatus.BLOCKED, GraphStatus.FAILED}
                else None
            ),
        )
        return self._snapshot(snapshot, graph_state=graph_state)

    def _update_graph_state(self, snapshot: WorkflowRunSnapshot) -> WorkflowRunSnapshot:
        latest = self._latest_states(snapshot)
        statuses = {item.status for item in latest.values()}
        current = snapshot.graph_state
        if all(
            item in {
                NodeStatus.SUCCEEDED,
                NodeStatus.SKIPPED_BY_CONDITION,
                NodeStatus.SKIPPED_BY_POLICY,
            }
            for item in statuses
        ):
            return self._finalize_or_pause(snapshot)
        if current.status in _TERMINAL_GRAPH_STATUSES:
            return snapshot
        status = (
            GraphStatus.RUNNING
            if NodeStatus.RUNNING in statuses
            else GraphStatus.AWAITING_APPROVAL
            if NodeStatus.AWAITING_APPROVAL in statuses
            else GraphStatus.READY
            if NodeStatus.READY in statuses
            else GraphStatus.PAUSED
        )
        graph_state = self._graph_state(
            current,
            status=status,
            started_at_epoch=(
                current.started_at_epoch
                or (self.clock() if status in {GraphStatus.RUNNING, GraphStatus.PAUSED} else None)
            ),
            completed_at_epoch=None,
            counts=self._counts(snapshot.node_states),
            retry_count=len(snapshot.retry_states),
        )
        return self._snapshot(snapshot, graph_state=graph_state)

    def _persist_if_changed(
        self, previous_fingerprint: str | None, snapshot: WorkflowRunSnapshot
    ) -> WorkflowRunSnapshot:
        if snapshot.snapshot_fingerprint == previous_fingerprint:
            return snapshot
        revised = WorkflowRunSnapshot.model_validate(
            {
                **snapshot.model_dump(mode="json", exclude={"snapshot_fingerprint"}),
                "revision": snapshot.revision + 1,
                "snapshot_fingerprint": None,
            }
        )
        return self.run_repository.update(
            revised, expected_revision=snapshot.revision
        )

    def _snapshot(self, snapshot: WorkflowRunSnapshot, **updates: object) -> WorkflowRunSnapshot:
        return WorkflowRunSnapshot.model_validate(
            {
                **snapshot.model_dump(mode="json", exclude={"snapshot_fingerprint"}),
                **updates,
                "snapshot_fingerprint": None,
            }
        )

    @staticmethod
    def _replace_node_state(state: NodeState, **updates: object) -> NodeState:
        return NodeState.model_validate({**state.model_dump(mode="json"), **updates})

    @staticmethod
    def _graph_state(
        state: GraphState,
        *,
        status: GraphStatus,
        counts: tuple[int, int, int],
        started_at_epoch: float | None | object = ...,
        completed_at_epoch: float | None | object = ...,
        current_node_id: str | None | object = ...,
        error_message: str | None | object = ...,
        retry_count: int | object = ...,
    ) -> GraphState:
        values = state.model_dump(mode="json")
        values.update(
            status=status.value,
            completed_nodes=counts[0],
            failed_nodes=counts[1],
            skipped_nodes=counts[2],
        )
        for key, value in {
            "started_at_epoch": started_at_epoch,
            "completed_at_epoch": completed_at_epoch,
            "current_node_id": current_node_id,
            "error_message": error_message,
            "retry_count": retry_count,
        }.items():
            if value is not ...:
                values[key] = value
        return GraphState.model_validate(values)

    @staticmethod
    def _latest_states(snapshot: WorkflowRunSnapshot) -> dict[str, NodeState]:
        latest: dict[str, NodeState] = {}
        for state in snapshot.node_states:
            previous = latest.get(state.node_id)
            if previous is None or state.attempt_number > previous.attempt_number:
                latest[state.node_id] = state
        return latest

    @classmethod
    def _counts(cls, states: tuple[NodeState, ...]) -> tuple[int, int, int]:
        latest: dict[str, NodeState] = {}
        for state in states:
            if state.node_id not in latest or state.attempt_number > latest[state.node_id].attempt_number:
                latest[state.node_id] = state
        completed = sum(item.status is NodeStatus.SUCCEEDED for item in latest.values())
        failed = sum(
            item.status in {NodeStatus.FAILED, NodeStatus.BLOCKED, NodeStatus.CANCELLED}
            for item in latest.values()
        )
        skipped = sum(
            item.status in {
                NodeStatus.SKIPPED_BY_CONDITION, NodeStatus.SKIPPED_BY_POLICY
            }
            for item in latest.values()
        )
        return completed, failed, skipped

    def _ready_nodes(self, snapshot: WorkflowRunSnapshot) -> tuple[str, ...]:
        now = self.clock()
        retry_not_before = {
            (item.node_id, item.attempt_number): item.scheduled_at_epoch
            for item in snapshot.retry_states
            if item.outcome is None
        }
        ready: list[str] = []
        for item in self._latest_states(snapshot).values():
            if item.status is NodeStatus.READY:
                ready.append(item.node_id)
            elif (
                item.status is NodeStatus.RETRY_SCHEDULED
                and retry_not_before.get((item.node_id, item.attempt_number), float("inf"))
                <= now
            ):
                ready.append(item.node_id)
        return tuple(sorted(ready))

    @staticmethod
    def _maximum_attempts(node: WorkflowNode, graph: WorkflowGraphDefinition) -> int:
        policy = node.retry_policy or graph.global_retry_policy
        return policy.max_attempts if policy is not None else 1

    def _parallelism(self, graph: WorkflowGraphDefinition) -> int:
        declared = (
            graph.global_execution_policy.concurrency_limit
            if graph.global_execution_policy is not None
            else self.maximum_parallelism
        )
        return min(self.maximum_parallelism, declared)

    @staticmethod
    def _node_estimates(node: WorkflowNode) -> dict[str, float]:
        estimates = {key: float(value) for key, value in node.estimated_resources.items()}
        if node.estimated_cost is not None:
            estimates[f"cost.{node.estimated_cost_currency}"] = node.estimated_cost
        return estimates

    def _rebuild_budget(
        self,
        budget: BudgetState,
        *,
        reserved: Mapping[str, float],
        actual: Mapping[str, float],
        unknown: Mapping[str, float],
        now: float,
    ) -> BudgetState:
        ceilings = dict(budget.ceiling_values)
        remaining = {
            key: max(
                float(value)
                - float(actual.get(key, 0.0))
                - float(reserved.get(key, 0.0)),
                0.0,
            )
            for key, value in ceilings.items()
        }
        over = any(
            float(actual.get(key, 0.0)) + float(reserved.get(key, 0.0)) > float(value)
            for key, value in ceilings.items()
        )
        at = any(
            math.isclose(
                float(actual.get(key, 0.0)) + float(reserved.get(key, 0.0)),
                float(value),
            )
            for key, value in ceilings.items()
        )
        status = (
            BudgetStatus.OVER_BUDGET
            if over
            else BudgetStatus.UNKNOWN
            if unknown
            else BudgetStatus.AT_CEILING
            if at
            else BudgetStatus.WITHIN_BUDGET
        )
        return BudgetState(
            graph_run_identifier=budget.graph_run_identifier,
            planning_estimates=budget.planning_estimates,
            reserved_budget=FrozenDict(sorted(reserved.items())),
            actual_consumption=FrozenDict(sorted(actual.items())),
            unknown_consumption=FrozenDict(sorted(unknown.items())),
            ceiling_values=budget.ceiling_values,
            remaining_budget=FrozenDict(sorted(remaining.items())),
            status=status,
            terminal_over_budget=over,
            last_updated_epoch=now,
        )

    @staticmethod
    def _node_approved(snapshot: WorkflowRunSnapshot, node_id: str) -> bool:
        approvals = [
            item
            for item in snapshot.approval_states
            if item.node_id in {None, node_id}
        ]
        return not approvals or all(item.status is ApprovalStatus.GRANTED for item in approvals)

    @staticmethod
    def _invocation_identifier(snapshot: WorkflowRunSnapshot, state: NodeState) -> str:
        return WorkflowOrchestrator._stable_identifier(
            "invocation",
            {
                "run": snapshot.graph_state.graph_run_identifier,
                "node": state.node_id,
                "attempt": max(state.attempt_number, 1),
                "graph": snapshot.graph_state.graph_definition_fingerprint,
            },
        )

    @staticmethod
    def _stable_identifier(prefix: str, identity: object) -> str:
        return validate_identifier(f"{prefix}.{sha256_fingerprint(identity)[:32]}")

    def _verify_runtime_identity(
        self, snapshot: WorkflowRunSnapshot, graph: WorkflowGraphDefinition
    ) -> None:
        expected = {item.node_identifier for item in graph.nodes}
        observed = set(self._latest_states(snapshot))
        if expected != observed:
            raise WorkflowStateIntegrityError(
                "Persisted node state does not match deterministic graph expansion."
            )
        if {item.edge_identifier for item in graph.edges} != {
            item.edge_id for item in snapshot.edge_states
        }:
            raise WorkflowStateIntegrityError(
                "Persisted edge state does not match deterministic graph expansion."
            )

    @staticmethod
    def _require_nonterminal(snapshot: WorkflowRunSnapshot) -> None:
        if snapshot.graph_state.status in _TERMINAL_GRAPH_STATUSES:
            raise WorkflowRunTerminalError("Workflow run is already terminal.")

    def _emit(self, event: str, **fields: str | int | float | bool) -> None:
        if self.observer is not None:
            self.observer(event, fields)
