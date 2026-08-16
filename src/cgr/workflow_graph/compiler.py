"""Deterministic compilation of Phase 3 candidate plans into Phase 4 graphs."""

from __future__ import annotations

from enum import Enum
from typing import Self

from pydantic import Field, field_validator, model_validator

from cgr.science.capabilities import CapabilityEstimateType
from cgr.science.canonical import CanonicalModel, sha256_fingerprint, validate_identifier
from cgr.science.planning import (
    CandidatePlanArtifactFlow,
    CandidatePlanAssignment,
    CandidateResearchPlan,
)

from .contracts import (
    ApprovalRequirement,
    CostCeiling,
    EdgeKind,
    ExecutionPolicy,
    FailureAction,
    FailureRecoveryPolicy,
    FrozenDict,
    InputPort,
    NodeKind,
    OutputPort,
    PortBinding,
    PortType,
    QuantumEscalationPolicy,
    ResourceCeiling,
    RetryPolicy,
    WorkflowEdge,
    WorkflowGraphDefinition,
    WorkflowGraphMetadata,
    WorkflowNode,
)
from .validation import WorkflowGraphValidator


class WorkflowCompilationError(ValueError):
    """Controlled failure to compile an immutable candidate plan."""


class AssignmentOrdering(str, Enum):
    """Closed assignment ordering policy for deterministic compilation."""

    PLAN_ORDER = "plan_order"
    ARTIFACT_FLOW = "artifact_flow"


class WorkflowCompilationPolicy(CanonicalModel):
    """Explicit non-executing policy used while compiling one plan."""

    ordering: AssignmentOrdering = AssignmentOrdering.ARTIFACT_FLOW
    concurrency_limit: int = Field(default=1, ge=1, le=64)
    timeout_seconds: float | None = Field(default=None, gt=0)
    retry_policy: RetryPolicy = RetryPolicy()
    failure_action: FailureAction = FailureAction.STOP
    cost_ceilings: tuple[CostCeiling, ...] = ()
    resource_ceilings: tuple[ResourceCeiling, ...] = ()
    allow_ibm_submission: bool = False
    ibm_required_approval_identifiers: tuple[str, ...] = ()
    ibm_allowed_backends: tuple[str, ...] = ()
    ibm_max_qubits: int | None = Field(default=None, ge=1)

    @field_validator(
        "ibm_required_approval_identifiers", "ibm_allowed_backends"
    )
    @classmethod
    def order_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(validate_identifier(item) for item in value)
        if len(normalized) != len(set(normalized)):
            raise ValueError("Compilation policy identifiers must be unique.")
        return tuple(sorted(normalized))

    @model_validator(mode="after")
    def validate_ibm_policy(self) -> Self:
        if self.allow_ibm_submission and not self.ibm_required_approval_identifiers:
            raise ValueError("IBM-enabled compilation requires approval identifiers.")
        return self


class WorkflowGraphCompiler:
    """Pure compiler from CandidateResearchPlan to WorkflowGraphDefinition."""

    def compile(
        self,
        plan: CandidateResearchPlan,
        *,
        created_by_principal: str,
        tenant_identifier: str | None = None,
        policy: WorkflowCompilationPolicy | None = None,
        graph_identifier: str | None = None,
        graph_version: int = 1,
    ) -> WorkflowGraphDefinition:
        """Compile without executing, authorizing, or probing capabilities."""

        if not isinstance(plan, CandidateResearchPlan):
            raise WorkflowCompilationError("A canonical candidate research plan is required.")
        principal = validate_identifier(
            created_by_principal, label="workflow compiler principal"
        )
        tenant = (
            validate_identifier(tenant_identifier, label="workflow tenant identifier")
            if tenant_identifier is not None
            else None
        )
        if graph_version < 1:
            raise WorkflowCompilationError("Workflow graph versions start at one.")
        policy = policy or WorkflowCompilationPolicy()
        graph_identifier = graph_identifier or self._graph_identifier(plan)
        graph_identifier = validate_identifier(
            graph_identifier, label="workflow graph identifier"
        )

        assignments = self._ordered_assignments(
            plan, policy.ordering, artifact_flows=plan.artifact_flows
        )
        if not assignments:
            raise WorkflowCompilationError(
                "A candidate plan without selected assignments cannot be compiled."
            )
        producers = self._artifact_producers(assignments)
        consumed_types = {
            artifact_type
            for assignment in assignments
            for artifact_type in assignment.input_artifact_types
        }
        explicit_sources = {
            (flow.destination_assignment_identifier, flow.artifact_type): (
                flow.source_assignment_identifier
            )
            for flow in plan.artifact_flows
        }
        ambiguous = tuple(sorted({
            artifact_type
            for assignment in assignments
            for artifact_type in assignment.input_artifact_types
            if (
                artifact_type in consumed_types
                and len(producers.get(artifact_type, ())) > 1
                and (assignment.assignment_identifier, artifact_type)
                not in explicit_sources
            )
        }))
        if ambiguous:
            raise WorkflowCompilationError(
                "Candidate plan artifact flow is ambiguous for: " + ",".join(ambiguous)
            )

        node_ids = {
            assignment.assignment_identifier: self._node_identifier(assignment)
            for assignment in assignments
        }
        nodes: list[WorkflowNode] = []
        edges: list[WorkflowEdge] = []
        bindings: list[PortBinding] = []
        node_approval_identifiers: set[str] = set()

        for assignment in assignments:
            node_id = node_ids[assignment.assignment_identifier]
            input_ports = tuple(
                InputPort(
                    port_identifier=self._port_identifier("input", artifact_type),
                    port_type=PortType.ARTIFACT,
                    artifact_kind=artifact_type,
                    required=True,
                    multiple=(
                        artifact_type not in producers
                        and len(assignment.input_artifacts) > 1
                    ),
                    external=artifact_type not in producers,
                )
                for artifact_type in assignment.input_artifact_types
            )
            output_ports = tuple(
                OutputPort(
                    port_identifier=self._port_identifier("output", artifact_type),
                    port_type=PortType.ARTIFACT,
                    artifact_kind=artifact_type,
                )
                for artifact_type in assignment.produced_artifact_types
            )
            approvals = self._approval_requirements(
                assignment=assignment,
                graph_identifier=graph_identifier,
                graph_version=graph_version,
                node_identifier=node_id,
                additional_identifiers=(
                    policy.ibm_required_approval_identifiers
                    if assignment.execution_target == "ibm_quantum"
                    and policy.allow_ibm_submission
                    else ()
                ),
            )
            node_approval_identifiers.update(
                item.approval_identifier for item in approvals
            )
            node_kind = self._node_kind(assignment.execution_target)
            quantum_policy = None
            if node_kind in {NodeKind.QUANTUM_LOCAL, NodeKind.QUANTUM_IBM}:
                quantum_policy = QuantumEscalationPolicy(
                    allow_ibm_submission=(
                        node_kind is NodeKind.QUANTUM_IBM
                        and policy.allow_ibm_submission
                    ),
                    required_approvals=(
                        policy.ibm_required_approval_identifiers
                        if node_kind is NodeKind.QUANTUM_IBM
                        else ()
                    ),
                    allowed_backends=(
                        policy.ibm_allowed_backends
                        if node_kind is NodeKind.QUANTUM_IBM
                        else ()
                    ),
                    max_qubits=(
                        policy.ibm_max_qubits
                        if node_kind is NodeKind.QUANTUM_IBM
                        else None
                    ),
                )
            (
                estimated_cost,
                estimated_cost_currency,
                estimated_resources,
            ) = self._assignment_estimates(assignment)
            verification_requirements = tuple(
                item.verifier_identifier
                for item in assignment.feasibility.required_verifications
            )
            nodes.append(
                WorkflowNode(
                    node_identifier=node_id,
                    node_kind=node_kind,
                    capability_identity=assignment.capability_name,
                    molecular_project_identifier=plan.project_identifier,
                    molecular_system_identifiers=plan.molecular_system_identifiers,
                    execution_target=assignment.execution_target,
                    input_ports=input_ports,
                    output_ports=output_ports,
                    execution_policy=ExecutionPolicy(
                        concurrency_limit=policy.concurrency_limit,
                        timeout_seconds=policy.timeout_seconds,
                        requires_approval=bool(approvals),
                        approval_requirements=approvals,
                    ),
                    retry_policy=policy.retry_policy,
                    failure_recovery_policy=FailureRecoveryPolicy(
                        on_failure=policy.failure_action,
                        retry_policy=(
                            policy.retry_policy
                            if policy.failure_action is FailureAction.RETRY
                            else None
                        ),
                    ),
                    quantum_escalation_policy=quantum_policy,
                    estimated_cost=estimated_cost,
                    estimated_cost_currency=estimated_cost_currency,
                    estimated_resources=estimated_resources,
                    verification_requirements=verification_requirements,
                    metadata=FrozenDict(
                        {
                            "assignment_identifier": assignment.assignment_identifier,
                            "capability_version": self._version_text(assignment),
                            "feasibility_status": assignment.feasibility.status.value,
                            "plan_identifier": plan.plan_identifier,
                            "principal": principal,
                        }
                    ),
                )
            )

            for artifact_type in assignment.input_artifact_types:
                producer_assignments = producers.get(artifact_type, ())
                if not producer_assignments:
                    continue
                explicit_source = explicit_sources.get(
                    (assignment.assignment_identifier, artifact_type)
                )
                producer_assignment = (
                    next(
                        item
                        for item in producer_assignments
                        if item.assignment_identifier == explicit_source
                    )
                    if explicit_source is not None
                    else producer_assignments[0]
                )
                if producer_assignment.assignment_identifier == assignment.assignment_identifier:
                    raise WorkflowCompilationError(
                        "Candidate plan artifact flow contains a self-dependency."
                    )
                source_node_id = node_ids[producer_assignment.assignment_identifier]
                source_port_id = self._port_identifier("output", artifact_type)
                target_port_id = self._port_identifier("input", artifact_type)
                binding_id = self._stable_identifier(
                    "binding",
                    {
                        "source_node": source_node_id,
                        "source_port": source_port_id,
                        "target_node": node_id,
                        "target_port": target_port_id,
                    },
                )
                edge_id = self._stable_identifier(
                    "edge",
                    {"binding": binding_id, "kind": EdgeKind.DATA_FLOW.value},
                )
                bindings.append(
                    PortBinding(
                        binding_identifier=binding_id,
                        source_node_id=source_node_id,
                        source_port_id=source_port_id,
                        destination_node_id=node_id,
                        destination_port_id=target_port_id,
                    )
                )
                edges.append(
                    WorkflowEdge(
                        edge_identifier=edge_id,
                        edge_kind=EdgeKind.DATA_FLOW,
                        source_node_id=source_node_id,
                        target_node_id=node_id,
                        source_port_id=source_port_id,
                        target_port_id=target_port_id,
                    )
                )

        incoming = {node.node_identifier: 0 for node in nodes}
        outgoing = {node.node_identifier: 0 for node in nodes}
        for edge in edges:
            incoming[edge.target_node_id] += 1
            outgoing[edge.source_node_id] += 1
        roots = tuple(sorted(node_id for node_id, count in incoming.items() if count == 0))
        terminals = tuple(
            sorted(node_id for node_id, count in outgoing.items() if count == 0)
        )

        global_approvals = tuple(
            ApprovalRequirement(
                approval_identifier=approval_identifier,
                graph_identifier=graph_identifier,
                graph_version=graph_version,
                operation_identity="workflow.graph.execute",
                description="Approval required by the candidate research plan.",
            )
            for approval_identifier in plan.approval_requirements
            if approval_identifier not in node_approval_identifiers
        )

        graph = WorkflowGraphDefinition(
            graph_identifier=graph_identifier,
            version=graph_version,
            nodes=tuple(nodes),
            edges=tuple(edges),
            bindings=tuple(bindings),
            root_node_ids=roots,
            terminal_node_ids=terminals,
            global_execution_policy=ExecutionPolicy(
                concurrency_limit=policy.concurrency_limit,
                timeout_seconds=policy.timeout_seconds,
                requires_approval=bool(global_approvals),
                approval_requirements=global_approvals,
            ),
            global_retry_policy=policy.retry_policy,
            cost_ceilings=policy.cost_ceilings,
            resource_ceilings=policy.resource_ceilings,
            metadata=WorkflowGraphMetadata(
                tenant_identifier=tenant,
                objective_identifier=plan.objective_identifier,
                molecular_project_identifier=plan.project_identifier,
                molecular_system_identifiers=plan.molecular_system_identifiers,
                candidate_plan_fingerprint=plan.fingerprint,
                rejected_alternative_identifiers=tuple(
                    item.alternative_identifier for item in plan.rejected_alternatives
                ),
                unresolved_requirement_identifiers=(
                    plan.unresolved_requirement_identifiers
                ),
                unresolved_goal_identifiers=plan.unresolved_goal_identifiers,
                estimated_cost=self._aggregate_cost(plan),
                estimated_runtime_seconds=self._aggregate_runtime(plan),
                verification_requirements=tuple(
                    item.verifier_identifier
                    for item in plan.verification_requirements
                ),
                scientific_provenance=FrozenDict(
                    {
                        "plan_identifier": plan.plan_identifier,
                        "plan_fingerprint": plan.fingerprint,
                        "objective_fingerprint": plan.objective_fingerprint,
                        "planning_constraints_fingerprint": (
                            plan.planning_constraints_fingerprint
                        ),
                        "resource_snapshot_fingerprint": (
                            plan.resource_snapshot_fingerprint
                        ),
                        "compiler_principal": principal,
                    }
                ),
            ),
        )
        validation = WorkflowGraphValidator(graph).validate()
        if not validation.valid:
            codes = ",".join(item.code for item in validation.errors[:8])
            raise WorkflowCompilationError(
                f"Compiled workflow graph failed static validation: {codes}"
            )
        return graph

    @classmethod
    def _ordered_assignments(
        cls,
        plan: CandidateResearchPlan,
        ordering: AssignmentOrdering,
        *,
        artifact_flows: tuple[CandidatePlanArtifactFlow, ...] = (),
    ) -> tuple[CandidatePlanAssignment, ...]:
        assignments = plan.selected_assignments
        if ordering is AssignmentOrdering.PLAN_ORDER or len(assignments) < 2:
            return assignments
        producers = cls._artifact_producers(assignments)
        by_identifier = {item.assignment_identifier: item for item in assignments}
        prerequisites: dict[str, set[str]] = {identifier: set() for identifier in by_identifier}
        successors: dict[str, set[str]] = {identifier: set() for identifier in by_identifier}
        explicit_destinations = {
            (flow.destination_assignment_identifier, flow.artifact_type)
            for flow in artifact_flows
        }
        for flow in artifact_flows:
            producer = flow.source_assignment_identifier
            destination = flow.destination_assignment_identifier
            prerequisites[destination].add(producer)
            successors[producer].add(destination)
        for assignment in assignments:
            for artifact_type in assignment.input_artifact_types:
                if (
                    assignment.assignment_identifier,
                    artifact_type,
                ) in explicit_destinations:
                    continue
                candidates = producers.get(artifact_type, ())
                if len(candidates) != 1:
                    continue
                producer = candidates[0].assignment_identifier
                if producer == assignment.assignment_identifier:
                    raise WorkflowCompilationError(
                        "Candidate plan artifact flow contains a self-dependency."
                    )
                prerequisites[assignment.assignment_identifier].add(producer)
                successors[producer].add(assignment.assignment_identifier)
        ready = sorted(
            identifier for identifier, required in prerequisites.items() if not required
        )
        ordered: list[CandidatePlanAssignment] = []
        while ready:
            identifier = ready.pop(0)
            ordered.append(by_identifier[identifier])
            for successor in sorted(successors[identifier]):
                prerequisites[successor].discard(identifier)
                if not prerequisites[successor] and all(
                    item.assignment_identifier != successor for item in ordered
                ) and successor not in ready:
                    ready.append(successor)
                    ready.sort()
        if len(ordered) != len(assignments):
            raise WorkflowCompilationError(
                "Candidate plan artifact flow contains a directed cycle."
            )
        return tuple(ordered)

    @staticmethod
    def _artifact_producers(
        assignments: tuple[CandidatePlanAssignment, ...],
    ) -> dict[str, tuple[CandidatePlanAssignment, ...]]:
        producers: dict[str, list[CandidatePlanAssignment]] = {}
        for assignment in assignments:
            for artifact_type in assignment.produced_artifact_types:
                producers.setdefault(artifact_type, []).append(assignment)
        return {
            artifact_type: tuple(
                sorted(items, key=lambda item: item.assignment_identifier)
            )
            for artifact_type, items in producers.items()
        }

    @staticmethod
    def _node_kind(execution_target: str | None) -> NodeKind:
        if execution_target == "ibm_quantum":
            return NodeKind.QUANTUM_IBM
        if execution_target in {"local_simulator", "local_quantum"}:
            return NodeKind.QUANTUM_LOCAL
        return NodeKind.CLASSICAL

    @staticmethod
    def _version_text(assignment: CandidatePlanAssignment) -> str:
        version = assignment.capability_version
        return f"{version.major}.{version.minor}.{version.patch}"

    @classmethod
    def _graph_identifier(cls, plan: CandidateResearchPlan) -> str:
        return cls._stable_identifier(
            "workflow",
            {
                "plan_identifier": plan.plan_identifier,
                "plan_fingerprint": plan.fingerprint,
            },
        )

    @classmethod
    def _node_identifier(cls, assignment: CandidatePlanAssignment) -> str:
        return cls._stable_identifier(
            "node",
            {
                "assignment": assignment.assignment_identifier,
                "capability": assignment.capability_name,
                "version": cls._version_text(assignment),
            },
        )

    @staticmethod
    def _port_identifier(direction: str, artifact_type: str) -> str:
        digest = sha256_fingerprint(
            {"direction": direction, "artifact_type": artifact_type}
        )[:16]
        return validate_identifier(f"{direction}.{digest}")

    @staticmethod
    def _stable_identifier(prefix: str, identity: object) -> str:
        return validate_identifier(f"{prefix}.{sha256_fingerprint(identity)[:32]}")

    @classmethod
    def _approval_requirements(
        cls,
        *,
        assignment: CandidatePlanAssignment,
        graph_identifier: str,
        graph_version: int,
        node_identifier: str,
        additional_identifiers: tuple[str, ...] = (),
    ) -> tuple[ApprovalRequirement, ...]:
        identifiers = tuple(
            sorted(
                set(assignment.feasibility.required_approvals).union(
                    additional_identifiers
                )
            )
        )
        return tuple(
            ApprovalRequirement(
                approval_identifier=approval_identifier,
                graph_identifier=graph_identifier,
                graph_version=graph_version,
                node_identifier=node_identifier,
                capability_identity=assignment.capability_name,
                operation_identity="workflow.node.execute",
                description="Approval required by the Phase 3 feasibility result.",
            )
            for approval_identifier in identifiers
        )

    @staticmethod
    def _assignment_estimates(
        assignment: CandidatePlanAssignment,
    ) -> tuple[float | None, str | None, FrozenDict]:
        cost: float | None = None
        cost_currency: str | None = None
        resources: dict[str, float] = {}
        for estimate in assignment.feasibility.estimates:
            value = (
                estimate.upper_bound
                if estimate.upper_bound is not None
                else estimate.expected_value
                if estimate.expected_value is not None
                else estimate.lower_bound
            )
            if value is None:
                continue
            if estimate.estimate_type is CapabilityEstimateType.MONETARY_COST:
                if cost_currency is not None and cost_currency != estimate.unit:
                    raise WorkflowCompilationError(
                        "A node cannot aggregate monetary estimates in multiple currencies."
                    )
                cost_currency = estimate.unit
                cost = value if cost is None else cost + value
            else:
                resources[
                    f"{estimate.estimate_type.value}.{estimate.unit}"
                ] = value
        return cost, cost_currency, FrozenDict(sorted(resources.items()))

    @staticmethod
    def _aggregate_cost(plan: CandidateResearchPlan) -> float | None:
        values = [
            item.expected_value
            if item.expected_value is not None
            else item.upper_bound
            if item.upper_bound is not None
            else item.lower_bound
            for item in plan.aggregate_estimates
            if item.estimate_type is CapabilityEstimateType.MONETARY_COST
        ]
        return sum(value for value in values if value is not None) if values else None

    @staticmethod
    def _aggregate_runtime(plan: CandidateResearchPlan) -> float | None:
        values = [
            item.expected_value
            if item.expected_value is not None
            else item.upper_bound
            if item.upper_bound is not None
            else item.lower_bound
            for item in plan.aggregate_estimates
            if item.estimate_type is CapabilityEstimateType.RUNTIME
        ]
        return sum(value for value in values if value is not None) if values else None
