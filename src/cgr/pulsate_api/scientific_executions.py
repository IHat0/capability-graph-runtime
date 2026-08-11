"""Durable scientist-facing objective, plan, status, and result records."""

from __future__ import annotations

import json
import re
import stat
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cgr.quantum_preflight.artifacts import write_json_atomic
from cgr.science.canonical import validate_identifier, validate_sha256
from cgr.science import ArtifactReference

from .scientific_objectives import (
    CovalentReactionTarget,
    ScientificExecutionBudget,
    ScientificInputReference,
    ScientificWorkflowPlan,
    StructuredScientificObjective,
    compile_scientific_objective,
    plan_scientific_objective,
)

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
        artifact_ids = [item.artifact_identifier for item in self.artifact_references]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("Scientific artifact references must have unique identities.")
        step_ids = [item.step_identifier for item in self.node_executions]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Scientific node execution identities must be unique.")
        return self


class ScientificObjectiveCompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1, max_length=8192)
    input_references: tuple[ScientificInputReference, ...] = Field(default=(), max_length=64)
    artifact_references: tuple[ArtifactReference, ...] = Field(default=(), max_length=64)
    covalent_reaction_target: CovalentReactionTarget | None = None
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

    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.RLock()
        self._started = False

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
    ) -> ScientificExecutionRecord:
        objective = compile_scientific_objective(
            request.question,
            input_references=request.input_references,
            budget=request.budget,
            covalent_reaction_target=request.covalent_reaction_target,
        )
        plan = plan_scientific_objective(objective)
        identifier = f"scientific-execution-{objective.objective_identifier.rsplit('-', 1)[-1]}"
        now = datetime.now(UTC)
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
            if record.objective != current.objective or record.plan != current.plan:
                raise RuntimeError("Scientific objective and plan are immutable.")
            path = self.root / record.execution_identifier / "record.json"
            write_json_atomic(path, record, maximum_bytes=_MAXIMUM_RECORD_BYTES)
            return record

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("Scientific execution repository is unavailable.")
