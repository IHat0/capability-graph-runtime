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
from cgr.science.canonical import validate_identifier

from .scientific_objectives import (
    ScientificExecutionBudget,
    ScientificInputReference,
    ScientificWorkflowPlan,
    StructuredScientificObjective,
    compile_scientific_objective,
    plan_scientific_objective,
)

_MAXIMUM_RECORD_BYTES = 4 * 1024 * 1024
_EXECUTION_ID = re.compile(r"^scientific-execution-[0-9a-f]{32}$")


class ScientificExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_identifier: str
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
    scene_identifier: str | None = None
    scientist_summary: str
    limitations: tuple[str, ...] = ()
    verified: bool = False

    @field_validator("execution_identifier")
    @classmethod
    def identifier(cls, value: str) -> str:
        if _EXECUTION_ID.fullmatch(value) is None:
            raise ValueError("Scientific execution identifier is invalid.")
        return value

    @model_validator(mode="after")
    def consistent(self) -> "ScientificExecutionRecord":
        if self.updated_at < self.created_at:
            raise ValueError("Scientific execution timestamps are inconsistent.")
        if self.status == "succeeded" and not self.result_artifact_identifiers:
            raise ValueError("Successful science requires result artifacts.")
        if self.verified and self.status != "succeeded":
            raise ValueError("Only a successful execution can be verified.")
        return self


class ScientificObjectiveCompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1, max_length=8192)
    input_references: tuple[ScientificInputReference, ...] = Field(default=(), max_length=64)
    budget: ScientificExecutionBudget = Field(default_factory=ScientificExecutionBudget)


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

    def create(self, request: ScientificObjectiveCompileRequest) -> ScientificExecutionRecord:
        objective = compile_scientific_objective(
            request.question,
            input_references=request.input_references,
            budget=request.budget,
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
            execution_identifier=identifier, created_at=now, updated_at=now,
            status=status, objective=objective, plan=plan,
            scientist_summary=summary,
            limitations=("This record contains a plan, not fabricated calculation results.",),
            verified=False,
        )
        with self._lock:
            self._require_started()
            directory = self.root / identifier
            if directory.exists():
                existing = self.get(identifier)
                if existing.objective != objective:
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

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("Scientific execution repository is unavailable.")
