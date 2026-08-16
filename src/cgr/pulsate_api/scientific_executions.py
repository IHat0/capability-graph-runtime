"""Durable scientist-facing objective, plan, status, and result records."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cgr.quantum_preflight.artifacts import write_json_atomic
from cgr.science import (
    ArtifactReference,
    CandidateResearchPlan,
    ScientificObjective,
)
from cgr.science.canonical import validate_identifier, validate_sha256
from cgr.workflow_graph import WorkflowGraphDefinition

from .scientific_capability_truth import ScientificCapabilityTruthCatalogue
from .scientific_compute_routing import ScientificComputeRouter
from .scientific_objectives import (
    CovalentReactionTarget,
    ScientificExecutionBudget,
    ScientificInputReference,
    ScientificWorkflowPlan,
    StructuredScientificObjective,
    compile_scientific_objective,
    plan_scientific_objective,
)
from .scientific_requirements import ValidatedResearchRequirements
from .scientific_unification import canonical_execution_graph

_MAXIMUM_RECORD_BYTES = 4 * 1024 * 1024
_EXECUTION_ID = re.compile(r"^scientific-execution-[0-9a-f]{32}$")


class ScientificNodeExecutionRecord(BaseModel):
    """Persisted scientist-workflow node state and exact produced evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_identifier: str
    capability_name: str
    status: Literal["pending", "running", "succeeded", "failed", "blocked"]
    attempt_count: int = Field(default=0, ge=0, le=10)
    output_artifact_identifiers: tuple[str, ...] = ()
    evidence_artifact_identifiers: tuple[str, ...] = ()
    error_code: str | None = None
    error_message: str | None = Field(default=None, max_length=4096)

    @field_validator("step_identifier", "capability_name", "error_code")
    @classmethod
    def identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @model_validator(mode="after")
    def consistent(self) -> "ScientificNodeExecutionRecord":
        failed = self.status in {"failed", "blocked"}
        if failed != (self.error_code is not None and self.error_message is not None):
            raise ValueError("Failed scientific nodes require a controlled error.")
        if self.status in {"running", "succeeded", "failed"} and self.attempt_count < 1:
            raise ValueError("Attempted scientific nodes require an attempt count.")
        return self


class ScientistFacingResult(BaseModel):
    """Stable product result, distinct from raw engine output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    original_request: str
    resolved_interpretation: str
    structures_and_entities: tuple[str, ...]
    methods: tuple[str, ...]
    assumptions: tuple[str, ...]
    scientific_result: str
    verification_status: Literal["passed", "failed", "inconclusive"]
    uncertainty: tuple[str, ...]
    replanning_history: tuple[str, ...]
    important_limitations: tuple[str, ...]
    evidence_artifact_identifiers: tuple[str, ...]
    scene_identifiers: tuple[str, ...]
    principal_result: str | None = Field(default=None, max_length=8192)
    candidate_ranking: tuple[str, ...] = ()
    confidence_and_uncertainty: tuple[str, ...] = ()
    recommended_next_step: str | None = Field(default=None, max_length=4096)
    synthesis_provider_kind: str | None = None
    synthesis_model_name: str | None = Field(default=None, max_length=512)

    @field_validator("synthesis_provider_kind")
    @classmethod
    def synthesis_provider(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None


class ScientificExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_identifier: str
    tenant_identifier_sha256: str | None = None
    created_at: datetime
    updated_at: datetime
    status: Literal[
        "clarification_required", "authorization_required", "planned",
        "running", "succeeded", "failed",
    ]
    objective: StructuredScientificObjective
    plan: ScientificWorkflowPlan
    canonical_objective: ScientificObjective | None = None
    canonical_plan: CandidateResearchPlan | None = None
    canonical_graph: WorkflowGraphDefinition | None = None
    result_artifact_identifiers: tuple[str, ...] = ()
    evidence_artifact_identifiers: tuple[str, ...] = ()
    checkpoint_identifiers: tuple[str, ...] = ()
    replanning_event_identifiers: tuple[str, ...] = ()
    artifact_references: tuple[ArtifactReference, ...] = ()
    node_executions: tuple[ScientificNodeExecutionRecord, ...] = ()
    workflow_graph_identifier: str | None = None
    workflow_run_identifier: str | None = None
    workflow_snapshot_fingerprint: str | None = None
    scene_identifier: str | None = None
    scientist_summary: str
    verified_scientific_summaries: tuple[str, ...] = ()
    scientist_result: ScientistFacingResult | None = None
    limitations: tuple[str, ...] = ()
    verified: bool = False

    @field_validator("execution_identifier")
    @classmethod
    def identifier(cls, value: str) -> str:
        if _EXECUTION_ID.fullmatch(value) is None:
            raise ValueError("Scientific execution identifier is invalid.")
        return value

    @field_validator("tenant_identifier_sha256")
    @classmethod
    def tenant_identity(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @model_validator(mode="after")
    def consistent(self) -> "ScientificExecutionRecord":
        if self.updated_at < self.created_at:
            raise ValueError("Scientific execution timestamps are inconsistent.")
        if self.status == "succeeded" and not self.result_artifact_identifiers:
            raise ValueError("Successful science requires result artifacts.")
        if self.verified and self.status not in {"running", "succeeded"}:
            raise ValueError("Only a running or successful execution can be verified.")
        if self.status == "succeeded" and self.scientist_result is None:
            raise ValueError("Successful science requires a scientist-facing result.")
        if self.scientist_result is not None and self.status not in {"running", "succeeded"}:
            raise ValueError("Only an assembling or successful execution may carry a scientist result.")
        if self.verified_scientific_summaries and not self.verified:
            raise ValueError(
                "Verified scientific summaries require a verified execution state."
            )
        artifact_ids = [item.artifact_identifier for item in self.artifact_references]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("Scientific artifact references must have unique identities.")
        step_ids = [item.step_identifier for item in self.node_executions]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Scientific node execution identities must be unique.")
        canonical = (
            self.canonical_objective,
            self.canonical_plan,
            self.canonical_graph,
        )
        if any(item is not None for item in canonical) and not all(
            item is not None for item in canonical
        ):
            raise ValueError("Canonical objective, plan, and graph must remain together.")
        if (
            self.canonical_objective is not None
            and self.canonical_plan is not None
            and self.canonical_graph is not None
            and (
                self.canonical_objective.objective_identifier
                != self.objective.objective_identifier
                or self.canonical_plan.objective_identifier
                != self.objective.objective_identifier
                or self.canonical_graph.metadata is None
                or self.canonical_graph.metadata.objective_identifier
                != self.objective.objective_identifier
            )
        ):
            raise ValueError("Canonical scientific execution identities must agree.")
        return self


class ScientificObjectiveCompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1, max_length=8192)
    input_references: tuple[ScientificInputReference, ...] = Field(default=(), max_length=64)
    artifact_references: tuple[ArtifactReference, ...] = Field(default=(), max_length=64)
    covalent_reaction_target: CovalentReactionTarget | None = None
    research_requirements: ValidatedResearchRequirements | None = None
    budget: ScientificExecutionBudget = Field(default_factory=ScientificExecutionBudget)

    @model_validator(mode="after")
    def bind_artifact_identities(self) -> "ScientificObjectiveCompileRequest":
        declared = {item.artifact_identifier for item in self.input_references}
        supplied = {item.artifact_identifier for item in self.artifact_references}
        if len(supplied) != len(self.artifact_references):
            raise ValueError("Scientific input artifact identities must be unique.")
        if supplied and supplied != declared:
            raise ValueError(
                "Every semantic scientific input must bind one exact artifact reference."
            )
        return self


class ScientificExecutionRepository:
    """Append-safe one-record-per-directory persistence with strict resolution."""

    def __init__(
        self,
        root: Path,
        *,
        capability_truth: ScientificCapabilityTruthCatalogue | None = None,
        compute_router: ScientificComputeRouter | None = None,
    ) -> None:
        self.root = root
        self._lock = threading.RLock()
        self._started = False
        self._capability_truth = capability_truth
        self._compute_router = compute_router

    def bind_capability_truth(
        self,
        capability_truth: ScientificCapabilityTruthCatalogue,
    ) -> None:
        """Bind one immutable production snapshot before accepting requests."""

        if not isinstance(capability_truth, ScientificCapabilityTruthCatalogue):
            raise TypeError("Scientific capability truth is invalid.")
        with self._lock:
            if self._started:
                raise RuntimeError(
                    "Scientific capability truth cannot change after repository startup."
                )
            if (
                self._capability_truth is not None
                and self._capability_truth.fingerprint != capability_truth.fingerprint
            ):
                raise RuntimeError("Scientific capability truth is already bound.")
            self._capability_truth = capability_truth

    def capability_truth_summary(self) -> dict[str, object]:
        with self._lock:
            if self._capability_truth is None:
                return {
                    "available": False,
                    "policy": "unbound_nonproduction_repository",
                }
            return self._capability_truth.summary()

    def bind_compute_router(self, compute_router: ScientificComputeRouter) -> None:
        """Bind deterministic resource routing before accepting requests."""

        if not isinstance(compute_router, ScientificComputeRouter):
            raise TypeError("Scientific compute routing is invalid.")
        with self._lock:
            if self._started:
                raise RuntimeError(
                    "Scientific compute routing cannot change after repository startup."
                )
            if self._compute_router is not None and self._compute_router is not compute_router:
                raise RuntimeError("Scientific compute routing is already bound.")
            self._compute_router = compute_router

    def compute_routing_summary(self) -> dict[str, object]:
        with self._lock:
            if self._compute_router is None:
                return {
                    "available": False,
                    "policy": "unbound_nonproduction_repository",
                }
            return self._compute_router.summary()

    def start(self) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            metadata = self.root.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise RuntimeError("Scientific execution repository root is unsafe.")
            self._started = True

    def close(self) -> None:
        with self._lock:
            self._started = False

    def create(
        self,
        request: ScientificObjectiveCompileRequest,
        *,
        tenant_identifier_sha256: str | None = None,
        project_identifier: str | None = None,
    ) -> ScientificExecutionRecord:
        objective = compile_scientific_objective(
            request.question,
            input_references=request.input_references,
            budget=request.budget,
            covalent_reaction_target=request.covalent_reaction_target,
            research_requirements=request.research_requirements,
        )
        plan = plan_scientific_objective(objective)
        if self._capability_truth is not None:
            self._capability_truth.require_selectable(
                step.capability_name
                for step in plan.steps
                if not step.authorization_required
            )
        if tenant_identifier_sha256 is None:
            identifier = (
                f"scientific-execution-"
                f"{objective.objective_identifier.rsplit('-', 1)[-1]}"
            )
        else:
            execution_identity = (
                objective.objective_identifier
                + f":{validate_sha256(tenant_identifier_sha256)}"
                + (f":{validate_identifier(project_identifier)}" if project_identifier else "")
            )
            identifier = (
                "scientific-execution-"
                + hashlib.sha256(execution_identity.encode("utf-8")).hexdigest()[:32]
            )
        now = datetime.now(UTC)
        declared_artifacts = {
            item.artifact_identifier for item in request.input_references
        }
        persisted_artifacts = {
            item.artifact_identifier for item in request.artifact_references
        }
        additional_requirements = (
            ("requirement-exact-input-artifacts",)
            if declared_artifacts - persisted_artifacts
            else ()
        )
        resource_snapshot = (
            self._compute_router.resource_snapshot()
            if self._compute_router is not None
            else None
        )
        canonical_objective, canonical_plan, canonical_graph = (
            canonical_execution_graph(
                objective=objective,
                phase8_plan=plan,
                execution_identifier=identifier,
                project_identifier=project_identifier or identifier,
                input_artifacts=tuple(
                    item.pointer for item in request.artifact_references
                ),
                tenant_identifier=tenant_identifier_sha256,
                additional_unresolved_requirements=additional_requirements,
                compute_router=self._compute_router,
                resource_snapshot=resource_snapshot,
            )
        )
        if objective.clarification_required:
            status = "clarification_required"
            summary = "The request was structured but needs explicit scientific clarification before execution."
        elif any(step.authorization_required for step in plan.steps):
            status = "authorization_required"
            summary = "The workflow is prepared through its authorization boundary; no IBM job was submitted."
        else:
            status = "planned"
            summary = "The request was compiled into a validated scientific capability plan; execution has not started."
        record = ScientificExecutionRecord(
            execution_identifier=identifier,
            tenant_identifier_sha256=tenant_identifier_sha256,
            created_at=now, updated_at=now,
            status=status, objective=objective, plan=plan,
            canonical_objective=canonical_objective,
            canonical_plan=canonical_plan,
            canonical_graph=canonical_graph,
            scientist_summary=summary,
            limitations=("This record contains a plan, not fabricated calculation results.",),
            artifact_references=request.artifact_references,
            node_executions=tuple(
                ScientificNodeExecutionRecord(
                    step_identifier=step.step_identifier,
                    capability_name=step.capability_name,
                    status="pending",
                )
                for step in plan.steps
            ),
            verified=False,
        )
        with self._lock:
            self._require_started()
            directory = self.root / identifier
            if directory.exists():
                existing = self.get(identifier)
                if (
                    existing.objective != objective
                    or existing.tenant_identifier_sha256 != tenant_identifier_sha256
                ):
                    raise RuntimeError("Scientific execution identity conflict.")
                return existing
            directory.mkdir()
            write_json_atomic(directory / "record.json", record, maximum_bytes=_MAXIMUM_RECORD_BYTES)
        return record

    def get(self, execution_identifier: str) -> ScientificExecutionRecord:
        if _EXECUTION_ID.fullmatch(execution_identifier) is None:
            raise KeyError("Scientific execution not found.")
        with self._lock:
            self._require_started()
            directory = self.root / execution_identifier
            record_path = directory / "record.json"
            try:
                if (
                    directory.resolve(strict=True).parent != self.root.resolve(strict=True)
                    or record_path.resolve(strict=True).parent != directory.resolve(strict=True)
                    or stat.S_ISLNK(directory.lstat().st_mode)
                    or stat.S_ISLNK(record_path.lstat().st_mode)
                    or record_path.stat().st_size > _MAXIMUM_RECORD_BYTES
                ):
                    raise KeyError("Scientific execution not found.")
                payload = json.loads(record_path.read_text(encoding="utf-8"))
                record = ScientificExecutionRecord.model_validate(payload)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                raise KeyError("Scientific execution not found.") from None
            if record.execution_identifier != execution_identifier:
                raise KeyError("Scientific execution not found.")
            return record

    def replace(
        self,
        record: ScientificExecutionRecord,
        *,
        expected_updated_at: datetime,
    ) -> ScientificExecutionRecord:
        """Atomically replace one execution record with optimistic concurrency."""

        with self._lock:
            self._require_started()
            current = self.get(record.execution_identifier)
            if current.updated_at != expected_updated_at:
                raise RuntimeError("Scientific execution record changed concurrently.")
            if record.created_at != current.created_at or record.updated_at <= current.updated_at:
                raise RuntimeError("Scientific execution timestamps cannot be rewritten.")
            if (
                record.objective != current.objective
                or record.plan != current.plan
                or record.canonical_objective != current.canonical_objective
                or record.canonical_plan != current.canonical_plan
                or record.canonical_graph != current.canonical_graph
            ):
                raise RuntimeError("Scientific objectives, plans, and graph are immutable.")
            path = self.root / record.execution_identifier / "record.json"
            write_json_atomic(path, record, maximum_bytes=_MAXIMUM_RECORD_BYTES)
            return record

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("Scientific execution repository is unavailable.")
