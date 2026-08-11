"""Persistent execution of scientist objectives through the CGR workflow runtime."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from cgr.kernel.contracts import ExecutionContext, ExecutionStatus
from cgr.science import (
    ArtifactPointer,
    ArtifactReference,
    CapabilityInvocation as EngineCapabilityInvocation,
    CapabilityResult as EngineCapabilityResult,
)
from cgr.workflow_graph import (
    CapabilityAdapterRegistry,
    CapabilityInvocation,
    CapabilityInvocationResult,
    CapabilityInvocationStatus,
    EdgeKind,
    FailureAction,
    FailureRecoveryPolicy,
    GraphRunRepository,
    GraphStatus,
    NodeKind,
    RetryPolicy,
    WorkflowEdge,
    WorkflowGraphDefinition,
    WorkflowGraphMetadata,
    WorkflowGraphRepository,
    WorkflowNode,
    WorkflowOrchestrator,
)

from .scientific_executions import (
    ScientificExecutionRecord,
    ScientificExecutionRepository,
    ScientificNodeExecutionRecord,
    ScientistFacingResult,
)
from .scientific_objectives import StructuredScientificObjective


class ScientificCapabilityOutcome(BaseModel):
    """Bounded product outcome returned by one executable capability."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    output_artifacts: tuple[ArtifactReference, ...] = ()
    evidence_artifacts: tuple[ArtifactReference, ...] = ()
    checkpoint_identifiers: tuple[str, ...] = ()
    replanning_event_identifiers: tuple[str, ...] = ()
    scene_identifier: str | None = None
    scientific_summary: str | None = Field(default=None, max_length=8192)
    limitations: tuple[str, ...] = ()
    verified: bool | None = None
    scientist_result: ScientistFacingResult | None = None


class ScientificCapabilityFailure(RuntimeError):
    """Controlled capability failure that can be diagnosed and replanned."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.retryable = retryable


@runtime_checkable
class ScientistCapabilityHandler(Protocol):
    def execute(
        self,
        *,
        invocation: CapabilityInvocation,
        objective: StructuredScientificObjective,
        record: ScientificExecutionRecord,
    ) -> ScientificCapabilityOutcome: ...


class ScientistCapabilityRegistry:
    """Exact capability handlers used by the scientist-facing product runtime."""

    def __init__(self, handlers: Mapping[str, ScientistCapabilityHandler] | None = None) -> None:
        self._handlers = dict(handlers or {})

    def register(self, capability_name: str, handler: ScientistCapabilityHandler) -> None:
        if capability_name in self._handlers:
            raise ValueError("Scientist capability handler is already registered.")
        if not isinstance(handler, ScientistCapabilityHandler):
            raise TypeError("Scientist capability handlers must implement execute().")
        self._handlers[capability_name] = handler

    def get(self, capability_name: str) -> ScientistCapabilityHandler | None:
        return self._handlers.get(capability_name)

    def identities(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))


def scientific_engine_registry(
    adapters: tuple[ScientificEngineAdapter, ...],
    *,
    parameter_providers: Mapping[
        str, Mapping[str, object] | ScientificParameterProvider
    ] | None = None,
    additional_handlers: Mapping[str, ScientistCapabilityHandler] | None = None,
) -> ScientistCapabilityRegistry:
    """Build one exact registry from declared native scientific adapters.

    Capability identity collisions fail closed: silently selecting one of two
    engines would make the executed method depend on import order.  Semantic,
    verification, campaign, and scene handlers remain explicit additions.
    """

    registry = ScientistCapabilityRegistry(additional_handlers)
    parameters = dict(parameter_providers or {})
    for adapter in adapters:
        if not isinstance(adapter, ScientificEngineAdapter):
            raise TypeError("Scientific engine registries require declared adapters.")
        for envelope in adapter.declaration.capabilities:
            capability_name = envelope.descriptor.capability_name
            registry.register(
                capability_name,
                ScientificEngineHandler(
                    adapter,
                    capability_name,
                    parameters=parameters.get(capability_name),
                ),
            )
    unknown_parameters = set(parameters) - set(registry.identities())
    if unknown_parameters:
        raise ValueError(
            "Scientific parameters reference undeclared capabilities: "
            + ", ".join(sorted(unknown_parameters))
        )
    return registry


@runtime_checkable
class ScientificEngineAdapter(Protocol):
    @property
    def declaration(self): ...

    def invoke(self, invocation: EngineCapabilityInvocation) -> EngineCapabilityResult: ...


ScientificParameterProvider = Callable[
    [StructuredScientificObjective, ScientificExecutionRecord], Mapping[str, object]
]


class ScientificEngineHandler:
    """Bridge a generic scientific engine adapter into objective orchestration."""

    def __init__(
        self,
        adapter: ScientificEngineAdapter,
        capability_name: str,
        *,
        parameters: Mapping[str, object] | ScientificParameterProvider | None = None,
    ) -> None:
        if not isinstance(adapter, ScientificEngineAdapter):
            raise TypeError("Scientific engine handlers require a declared adapter.")
        self.adapter = adapter
        self.capability_name = capability_name
        self.parameters = parameters or {}
        self.envelope = next(
            (
                item
                for item in adapter.declaration.capabilities
                if item.descriptor.capability_name == capability_name
            ),
            None,
        )
        if self.envelope is None:
            raise ValueError("Scientific engine adapter does not declare the capability.")

    def execute(
        self,
        *,
        invocation: CapabilityInvocation,
        objective: StructuredScientificObjective,
        record: ScientificExecutionRecord,
    ) -> ScientificCapabilityOutcome:
        step = next(
            item for item in record.plan.steps
            if item.step_identifier == invocation.node_identifier
        )
        dependency_artifact_ids = {
            artifact_identifier
            for dependency in step.depends_on
            for node in record.node_executions
            if node.step_identifier == dependency
            for artifact_identifier in node.output_artifact_identifiers
        }
        objective_artifact_ids = {
            item.artifact_identifier for item in objective.input_references
        }
        allowed_ids = dependency_artifact_ids | objective_artifact_ids
        accepted = set(self.envelope.descriptor.accepted_artifact_types)
        inputs = tuple(
            artifact
            for artifact in record.artifact_references
            if artifact.artifact_identifier in allowed_ids
            and (not accepted or artifact.artifact_type in accepted)
        )
        parameter_values = (
            self.parameters(objective, record)
            if callable(self.parameters)
            else self.parameters
        )
        objective_sha = hashlib.sha256(
            objective.model_dump_json().encode("utf-8")
        ).hexdigest()
        result = self.adapter.invoke(EngineCapabilityInvocation(
            capability=self.envelope.descriptor,
            input_artifacts=inputs,
            experiment=ArtifactPointer(
                artifact_identifier=objective.objective_identifier,
                content_sha256=objective_sha,
            ),
            context=ExecutionContext(execution_id=invocation.invocation_identifier),
            parameters=dict(parameter_values),
        ))
        if result.status is not ExecutionStatus.SUCCESS:
            failure = result.failure
            raise ScientificCapabilityFailure(
                failure.code if failure is not None else "scientific_engine_failed",
                (
                    failure.message
                    if failure is not None
                    else "Scientific engine execution failed without valid evidence."
                ),
                retryable=failure.retryable if failure is not None else False,
            )
        verification_passed = (
            bool(result.verification_results)
            and not any(item.has_blocking_failure for item in result.verification_results)
        )
        return ScientificCapabilityOutcome(
            output_artifacts=result.output_artifacts,
            evidence_artifacts=result.output_artifacts,
            verified=verification_passed or None,
            scientific_summary=(
                f"{self.capability_name} completed with "
                f"{len(result.output_artifacts)} persisted artifact(s)."
            ),
        )


def scientific_plan_graph(record: ScientificExecutionRecord) -> WorkflowGraphDefinition:
    """Compile one validated scientific plan into an immutable CGR graph."""

    retry_policy = (
        RetryPolicy(
            max_attempts=record.objective.budget.maximum_replans + 1,
            retry_delay_seconds=0.0,
        )
        if record.objective.budget.maximum_replans > 0
        else None
    )
    recovery_policy = (
        FailureRecoveryPolicy(
            on_failure=FailureAction.RETRY,
            retry_policy=retry_policy,
            recovery_checkpoint_required=False,
        )
        if retry_policy is not None
        else None
    )
    nodes = tuple(
        WorkflowNode(
            node_identifier=step.step_identifier,
            node_kind=(
                NodeKind.QUANTUM_LOCAL
                if step.capability_name.startswith(("quantum.", "quantum_workflow."))
                else NodeKind.CLASSICAL
            ),
            capability_identity=step.capability_name,
            execution_target=(
                "local_simulator"
                if step.capability_name.startswith(("quantum.", "quantum_workflow."))
                else "local_scientific_runtime"
            ),
            retry_policy=retry_policy,
            failure_recovery_policy=recovery_policy,
            verification_requirements=(
                ("scientific_verification",) if step.verification_required else ()
            ),
            metadata={
                "objective_identifier": record.objective.objective_identifier,
                "scientific_task": record.objective.task_type,
                "recovery_model": "bounded-whole-capability-retry",
            },
        )
        for step in record.plan.steps
        if not step.authorization_required
    )
    included = {node.node_identifier for node in nodes}
    edges = tuple(
        WorkflowEdge(
            edge_identifier=(
                "edge-"
                + hashlib.sha256(f"{source}:{step.step_identifier}".encode()).hexdigest()[:32]
            ),
            edge_kind=EdgeKind.DEPENDENCY,
            source_node_id=source,
            target_node_id=step.step_identifier,
        )
        for step in record.plan.steps
        if step.step_identifier in included
        for source in step.depends_on
        if source in included
    )
    dependent = {edge.target_node_id for edge in edges}
    sources = {edge.source_node_id for edge in edges}
    graph_identifier = f"scientific-graph-{record.objective.objective_identifier.rsplit('-', 1)[-1]}"
    return WorkflowGraphDefinition(
        graph_identifier=graph_identifier,
        version=1,
        nodes=nodes,
        edges=edges,
        root_node_ids=tuple(sorted(included - dependent)),
        terminal_node_ids=tuple(sorted(included - sources)),
        metadata=WorkflowGraphMetadata(
            objective_identifier=record.objective.objective_identifier,
            estimated_runtime_seconds=float(record.objective.budget.maximum_wall_time_seconds),
            verification_requirements=("seven_dimension_scientific_verification",),
            scientific_provenance={
                "planner": "artifact_driven_phase8_catalogue",
                "plan_identifier": record.plan.plan_identifier,
            },
        ),
    )


class ScientistResultAssembler:
    """Assemble a stable scientist-facing answer from persisted verified evidence."""

    def execute(
        self,
        *,
        invocation: CapabilityInvocation,
        objective: StructuredScientificObjective,
        record: ScientificExecutionRecord,
    ) -> ScientificCapabilityOutcome:
        del invocation
        methods = tuple(
            node.capability_name
            for node in record.node_executions
            if node.status == "succeeded" and node.capability_name != "scientist.result_assemble"
        )
        entities = tuple(
            f"{item.artifact_type}:{item.artifact_identifier}"
            for item in objective.input_references
        )
        verification_status = "passed" if record.verified else "inconclusive"
        result = ScientistFacingResult(
            original_request=objective.original_request,
            resolved_interpretation=(
                f"{objective.task_type} targeting {objective.semantic_target.target_label}"
            ),
            structures_and_entities=entities,
            methods=methods,
            assumptions=objective.assumptions,
            scientific_result=record.scientist_summary,
            verification_status=verification_status,
            uncertainty=(
                "Only quantities explicitly represented by the evidence artifacts are reported.",
            ),
            replanning_history=record.replanning_event_identifiers,
            important_limitations=record.limitations,
            evidence_artifact_identifiers=record.evidence_artifact_identifiers,
            scene_identifiers=((record.scene_identifier,) if record.scene_identifier else ()),
        )
        return ScientificCapabilityOutcome(
            scientist_result=result,
            scientific_summary=result.scientific_result,
            verified=record.verified,
        )


class ScientificObjectiveRuntime:
    """Execute and persist one scientist objective through the existing CGR runtime."""

    def __init__(
        self,
        *,
        root: Path,
        execution_repository: ScientificExecutionRepository,
        capability_registry: ScientistCapabilityRegistry,
    ) -> None:
        self.root = Path(root)
        self.execution_repository = execution_repository
        self.capability_registry = capability_registry
        if self.capability_registry.get("scientist.result_assemble") is None:
            self.capability_registry.register(
                "scientist.result_assemble", ScientistResultAssembler()
            )
        self.graph_repository = WorkflowGraphRepository(self.root)
        self.run_repository = GraphRunRepository(self.root)
        self._lock = threading.RLock()
        self._started = False

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self.graph_repository.start()
            self.run_repository.start()
            self._started = True

    def close(self) -> None:
        with self._lock:
            self.graph_repository.close()
            self.run_repository.close()
            self._started = False

    def execute(self, execution_identifier: str) -> ScientificExecutionRecord:
        with self._lock:
            if not self._started:
                raise RuntimeError("Scientific objective runtime is unavailable.")
            record = self.execution_repository.get(execution_identifier)
            if record.status == "succeeded" or record.status == "failed":
                return record
            if record.status != "planned" or not record.plan.executable:
                raise ScientificCapabilityFailure(
                    "scientific_execution_blocked",
                    "Scientific execution is blocked by clarification or authorization.",
                )
            declared_inputs = {
                item.artifact_identifier for item in record.objective.input_references
            }
            persisted_inputs = {
                item.artifact_identifier for item in record.artifact_references
            }
            missing_inputs = tuple(sorted(declared_inputs - persisted_inputs))
            if missing_inputs:
                raise ScientificCapabilityFailure(
                    "scientific_input_artifact_unavailable",
                    "Exact scientific input artifacts are unavailable: "
                    + ", ".join(missing_inputs),
                )
            missing = tuple(
                sorted(
                    {
                        step.capability_name
                        for step in record.plan.steps
                        if not step.authorization_required
                        and self.capability_registry.get(step.capability_name) is None
                    }
                )
            )
            if missing:
                raise ScientificCapabilityFailure(
                    "scientific_capability_unavailable",
                    "Required scientific capabilities are unavailable: " + ", ".join(missing),
                )

            graph = scientific_plan_graph(record)
            self.graph_repository.put(graph)
            record = self._replace_record(
                record,
                status="running",
                workflow_graph_identifier=graph.graph_identifier,
                workflow_run_identifier=record.execution_identifier,
                scientist_summary="Scientific capability execution is running.",
            )
            adapter = CapabilityAdapterRegistry(
                {
                    name: self._workflow_handler(name)
                    for name in self.capability_registry.identities()
                }
            )
            orchestrator = WorkflowOrchestrator(
                graph_repository=self.graph_repository,
                run_repository=self.run_repository,
                capability_adapter=adapter,
                maximum_parallelism=1,
            )
            try:
                orchestrator.create_run(
                    graph_identifier=graph.graph_identifier,
                    graph_version=graph.version,
                    tenant_identifier="pulsate-scientist",
                    created_by_principal="scientist-objective-runtime",
                    graph_run_identifier=record.execution_identifier,
                )
                snapshot = orchestrator.resume(
                    record.execution_identifier,
                    correlation_identifier=record.execution_identifier,
                    maximum_cycles=max(100, len(graph.nodes) * 12),
                )
            except Exception as error:
                current = self.execution_repository.get(record.execution_identifier)
                if current.status == "running":
                    self._replace_record(
                        current,
                        status="failed",
                        scientist_summary="Scientific workflow execution failed safely.",
                        limitations=(*current.limitations, str(error)[:1024]),
                    )
                raise

            current = self.execution_repository.get(record.execution_identifier)
            if snapshot.graph_state.status is GraphStatus.SUCCEEDED:
                if current.scientist_result is None:
                    raise RuntimeError("Successful workflow omitted its scientist-facing result.")
                return self._replace_record(
                    current,
                    status="succeeded",
                    workflow_snapshot_fingerprint=snapshot.snapshot_fingerprint,
                    result_artifact_identifiers=tuple(
                        artifact.artifact_identifier for artifact in current.artifact_references
                    ),
                )
            return self._replace_record(
                current,
                status="failed",
                workflow_snapshot_fingerprint=snapshot.snapshot_fingerprint,
                scientist_summary="Scientific workflow did not reach a successful terminal state.",
            )

    def _workflow_handler(self, capability_name: str):
        def invoke(invocation: CapabilityInvocation) -> CapabilityInvocationResult:
            record = self.execution_repository.get(invocation.graph_run_identifier)
            node = next(
                item for item in record.node_executions
                if item.step_identifier == invocation.node_identifier
            )
            record = self._update_node(
                record,
                node.model_copy(update={
                    "status": "running",
                    "attempt_count": invocation.attempt_number,
                    "error_code": None,
                    "error_message": None,
                }),
            )
            handler = self.capability_registry.get(capability_name)
            assert handler is not None
            try:
                outcome = handler.execute(
                    invocation=invocation,
                    objective=record.objective,
                    record=record,
                )
            except ScientificCapabilityFailure as error:
                current = self.execution_repository.get(record.execution_identifier)
                self._update_node(
                    current,
                    node.model_copy(update={
                        "status": "failed",
                        "attempt_count": invocation.attempt_number,
                        "error_code": error.code,
                        "error_message": error.public_message,
                    }),
                )
                return CapabilityInvocationResult(
                    invocation_identifier=invocation.invocation_identifier,
                    status=CapabilityInvocationStatus.FAILED,
                    retryable=error.retryable,
                    error_code=error.code,
                    error_message=error.public_message,
                )
            except Exception as error:
                detail = " ".join(str(error).split())[:1024]
                message = (
                    "Scientist capability handler failed unexpectedly "
                    f"({type(error).__name__}: {detail or 'no diagnostic supplied'})."
                )
                current = self.execution_repository.get(record.execution_identifier)
                self._update_node(
                    current,
                    node.model_copy(update={
                        "status": "failed",
                        "attempt_count": invocation.attempt_number,
                        "error_code": "scientific_handler_unexpected",
                        "error_message": message,
                    }),
                )
                return CapabilityInvocationResult(
                    invocation_identifier=invocation.invocation_identifier,
                    status=CapabilityInvocationStatus.FAILED,
                    retryable=False,
                    error_code="scientific_handler_unexpected",
                    error_message=message,
                )
            current = self.execution_repository.get(record.execution_identifier)
            merged = {
                artifact.artifact_identifier: artifact
                for artifact in current.artifact_references
            }
            for artifact in (*outcome.output_artifacts, *outcome.evidence_artifacts):
                existing = merged.get(artifact.artifact_identifier)
                if existing is not None and existing != artifact:
                    raise RuntimeError("Scientific artifact identity conflict.")
                merged[artifact.artifact_identifier] = artifact
            updated_node = node.model_copy(update={
                "status": "succeeded",
                "attempt_count": invocation.attempt_number,
                "error_code": None,
                "error_message": None,
                "output_artifact_identifiers": tuple(
                    artifact.artifact_identifier for artifact in outcome.output_artifacts
                ),
                "evidence_artifact_identifiers": tuple(
                    artifact.artifact_identifier for artifact in outcome.evidence_artifacts
                ),
            })
            current = self._replace_record(
                current,
                artifact_references=tuple(sorted(merged.values(), key=lambda item: item.artifact_identifier)),
                evidence_artifact_identifiers=tuple(dict.fromkeys((
                    *current.evidence_artifact_identifiers,
                    *(item.artifact_identifier for item in outcome.evidence_artifacts),
                ))),
                checkpoint_identifiers=tuple(dict.fromkeys((
                    *current.checkpoint_identifiers, *outcome.checkpoint_identifiers,
                ))),
                replanning_event_identifiers=tuple(dict.fromkeys((
                    *current.replanning_event_identifiers,
                    *outcome.replanning_event_identifiers,
                ))),
                scene_identifier=outcome.scene_identifier or current.scene_identifier,
                scientist_summary=outcome.scientific_summary or current.scientist_summary,
                limitations=tuple(dict.fromkeys((*current.limitations, *outcome.limitations))),
                verified=current.verified if outcome.verified is None else outcome.verified,
                scientist_result=outcome.scientist_result or current.scientist_result,
                node_executions=tuple(
                    updated_node if item.step_identifier == updated_node.step_identifier else item
                    for item in current.node_executions
                ),
            )
            return CapabilityInvocationResult(
                invocation_identifier=invocation.invocation_identifier,
                status=CapabilityInvocationStatus.SUCCEEDED,
                output_artifacts=outcome.output_artifacts,
                verification_classification=(
                    "passed" if outcome.verified else None
                ),
            )

        return invoke

    def _update_node(
        self,
        record: ScientificExecutionRecord,
        updated_node: ScientificNodeExecutionRecord,
    ) -> ScientificExecutionRecord:
        return self._replace_record(
            record,
            node_executions=tuple(
                updated_node if item.step_identifier == updated_node.step_identifier else item
                for item in record.node_executions
            ),
        )

    def _replace_record(self, record: ScientificExecutionRecord, **updates: object) -> ScientificExecutionRecord:
        now = datetime.now(UTC)
        if now <= record.updated_at:
            now = record.updated_at + timedelta(microseconds=1)
        replacement = ScientificExecutionRecord.model_validate({
            **record.model_dump(mode="json"),
            **updates,
            "updated_at": now,
        })
        return self.execution_repository.replace(
            replacement, expected_updated_at=record.updated_at
        )
