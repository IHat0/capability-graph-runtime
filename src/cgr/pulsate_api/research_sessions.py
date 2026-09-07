"""Durable, resumable research sessions over the unified scientific runtime."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cgr.quantum_preflight.artifacts import write_json_atomic
from cgr.science import ArtifactReference, CandidateResearchPlan, ScientificObjective
from cgr.science.canonical import validate_identifier, validate_sha256
from cgr.workflow_graph import WorkflowGraphDefinition
from .scientific_capability_truth import ScientificCapabilityGroundingError
from .scientific_candidate_evidence import CandidateEvidenceReview

from .scientific_conversation import (
    ClarificationPrompt,
    PartialCovalentReactionTarget,
    ScientificEntityResolutionCandidate,
    ScientificEvidenceInterpreter,
    ScientificEvidenceProposal,
    ScientificIntentInterpreter,
    ScientificIntentProposal,
    ScientificQuestionWriter,
    ScientistEvidenceTurn,
)
from .scientific_executions import (
    ScientificExecutionRecord,
    ScientificExecutionRepository,
    ScientificNodeExecutionRecord,
    ScientificObjectiveCompileRequest,
    ScientistFacingResult,
)
from .scientific_objectives import (
    CovalentReactionTarget,
    ScientificExecutionBudget,
    ScientificInputReference,
    ScientificTask,
    ScientificWorkflowPlan,
    StructuredScientificObjective,
)
from .scientific_requirements import (
    ProviderNeutralScientificRequirementInterpreter,
    ScientificRequirementProposal,
    ValidatedResearchRequirements,
    validate_requirement_proposal,
)
from .scientific_runtime import ScientificObjectiveRuntime
from .scientific_session_resolution import ScientificSessionEvidenceResolver
from .scientific_unification import unresolved_requirement_identifiers

_MAXIMUM_SESSION_BYTES = 16 * 1024 * 1024
_SESSION_ID = re.compile(r"^research-session-[0-9a-f]{32}$")

ResearchSessionStatus = Literal[
    "understanding",
    "awaiting_clarification",
    "awaiting_approval",
    "planned",
    "running",
    "verifying",
    "replanning",
    "completed",
    "failed",
]
ResearchTurnRole = Literal["scientist", "pulsate"]


def _merge_identified(
    previous: tuple[object, ...],
    supplied: tuple[object, ...] | None,
    *,
    attribute: str,
) -> tuple[object, ...]:
    if supplied is None:
        return previous
    merged = {getattr(item, attribute): item for item in previous}
    for item in supplied:
        identifier = getattr(item, attribute)
        existing = merged.get(identifier)
        if existing is not None and existing != item:
            raise ValueError("Research evidence identity conflict.")
        merged[identifier] = item
    return tuple(sorted(merged.values(), key=lambda item: getattr(item, attribute)))


def _acquired_artifact_identifiers(
    artifacts: tuple[ArtifactReference, ...],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            item.artifact_identifier
            for item in artifacts
            if isinstance(
                item.metadata.get("acquisition_source_kind"), str
            )
        )
    )


class ResearchConversationTurn(BaseModel):
    """One immutable scientist or Pulsate utterance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    turn_identifier: str
    role: ResearchTurnRole
    content: str = Field(min_length=1, max_length=8192)
    created_at: datetime
    requirement_identifier: str | None = None

    @field_validator("turn_identifier", "requirement_identifier")
    @classmethod
    def identifiers(cls, value: str | None) -> str | None:
        return (
            validate_identifier(value, label="research conversation identifier")
            if value is not None
            else None
        )

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        value = " ".join(value.strip().split())
        if not value:
            raise ValueError("Research conversation turns cannot be blank.")
        return value


class ResearchCompilation(BaseModel):
    """One Phase 8 compatibility revision attached to canonical Phases 3 and 4."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    compilation_identifier: str
    compiled_at: datetime
    execution_identifier: str
    effective_question: str
    input_references: tuple[ScientificInputReference, ...] = ()
    artifact_references: tuple[ArtifactReference, ...] = ()
    covalent_reaction_target: CovalentReactionTarget | None = None
    budget: ScientificExecutionBudget
    compatibility_objective: StructuredScientificObjective
    compatibility_plan: ScientificWorkflowPlan
    canonical_objective: ScientificObjective
    canonical_plan: CandidateResearchPlan
    canonical_graph: WorkflowGraphDefinition

    @field_validator("compilation_identifier", "execution_identifier")
    @classmethod
    def identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="research compilation identifier")


class ResearchSession(BaseModel):
    """The single durable owner of a scientist question and its whole lifecycle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_identifier: str
    tenant_identifier_sha256: str
    created_at: datetime
    updated_at: datetime
    revision: int = Field(ge=1)
    status: ResearchSessionStatus
    conversation: tuple[ResearchConversationTurn, ...] = Field(min_length=1)
    input_references: tuple[ScientificInputReference, ...] = ()
    artifact_references: tuple[ArtifactReference, ...] = ()
    unapproved_input_artifact_identifiers: tuple[str, ...] = ()
    covalent_reaction_target: CovalentReactionTarget | None = None
    budget: ScientificExecutionBudget = Field(default_factory=ScientificExecutionBudget)
    next_questions: tuple[ClarificationPrompt, ...] = ()
    clarification_attempts: dict[str, int] = Field(default_factory=dict)
    intent_proposal: ScientificIntentProposal | None = None
    accepted_task_type: ScientificTask | None = None
    requirement_proposal: ScientificRequirementProposal | None = None
    accepted_research_requirements: ValidatedResearchRequirements | None = None
    evidence_proposal: ScientificEvidenceProposal | None = None
    accepted_partial_covalent_reaction_target: (
        PartialCovalentReactionTarget | None
    ) = None
    accepted_evidence: tuple[ScientificEvidenceProposal, ...] = Field(
        default=(), max_length=64
    )
    compilation: ResearchCompilation | None = None
    execution_status: str | None = None
    execution_steps: tuple[ScientificNodeExecutionRecord, ...] = ()
    scene_identifier: str | None = None
    scientist_result: ScientistFacingResult | None = None
    scientist_summary: str
    evidence_reviews: tuple[CandidateEvidenceReview, ...] = Field(default=(), max_length=64)

    @field_validator("session_identifier")
    @classmethod
    def session_identity(cls, value: str) -> str:
        if _SESSION_ID.fullmatch(value) is None:
            raise ValueError("Research session identifier is invalid.")
        return value

    @field_validator("tenant_identifier_sha256")
    @classmethod
    def tenant_identity(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("Research session timestamps are inconsistent.")
        turn_ids = [item.turn_identifier for item in self.conversation]
        if len(turn_ids) != len(set(turn_ids)):
            raise ValueError("Research conversation turn identities must be unique.")
        if self.status == "awaiting_clarification" and not self.next_questions:
            raise ValueError("Clarification state requires a next question.")
        if self.status != "awaiting_clarification" and self.next_questions:
            raise ValueError("Only clarification state may expose next questions.")
        if self.status in {
            "awaiting_approval",
            "planned",
            "running",
            "verifying",
            "replanning",
            "completed",
        } and self.compilation is None:
            raise ValueError("Active research states require a canonical compilation.")
        if self.status == "completed" and self.scientist_result is None:
            raise ValueError("Completed research requires a scientist-facing result.")
        if self.scientist_result is not None and self.status != "completed":
            raise ValueError("Only completed research may expose a final result.")
        if self.evidence_proposal is not None and self.status != "awaiting_clarification":
            raise ValueError("A pending evidence proposal requires scientist review.")
        if any(value < 1 for value in self.clarification_attempts.values()):
            raise ValueError("Clarification attempt counts must be positive.")
        declared = {item.artifact_identifier for item in self.input_references}
        supplied = {item.artifact_identifier for item in self.artifact_references}
        if len(supplied) != len(self.artifact_references) or (
            supplied and supplied != declared
        ):
            raise ValueError(
                "Every persisted session artifact must bind one semantic input."
            )
        if not set(self.unapproved_input_artifact_identifiers).issubset(supplied):
            raise ValueError("Unapproved inputs must reference session artifacts.")
        return self


class ResearchSessionCreateRequest(BaseModel):
    """Start a session with plain text and optional exact scientific evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1, max_length=8192)
    input_references: tuple[ScientificInputReference, ...] = Field(
        default=(), max_length=64
    )
    artifact_references: tuple[ArtifactReference, ...] = Field(
        default=(), max_length=64
    )
    covalent_reaction_target: CovalentReactionTarget | None = None
    budget: ScientificExecutionBudget = Field(default_factory=ScientificExecutionBudget)
    accept_acquired_input_evidence: bool = False

    @model_validator(mode="after")
    def exact_artifact_bindings(self) -> Self:
        ScientificObjectiveCompileRequest(
            question=self.question,
            input_references=self.input_references,
            artifact_references=self.artifact_references,
            covalent_reaction_target=self.covalent_reaction_target,
            budget=self.budget,
        )
        return self


class ResearchSessionReplyRequest(BaseModel):
    """Continue one session; omitted evidence is retained from its current revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    message: str = Field(min_length=1, max_length=8192)
    input_references: tuple[ScientificInputReference, ...] | None = Field(
        default=None, max_length=64
    )
    artifact_references: tuple[ArtifactReference, ...] | None = Field(
        default=None, max_length=64
    )
    covalent_reaction_target: CovalentReactionTarget | None = None
    budget: ScientificExecutionBudget | None = None
    accept_intent_proposal: bool = False
    accept_requirement_proposal: bool = False
    accept_evidence_proposal: bool = False
    accept_acquired_input_evidence: bool = False


@dataclass(frozen=True, slots=True)
class _AutomaticCompilation:
    compilation: ResearchCompilation | None
    execution: ScientificExecutionRecord | None
    questions: tuple[ClarificationPrompt, ...]
    intent_proposal: ScientificIntentProposal | None
    evidence_proposal: ScientificEvidenceProposal | None
    input_references: tuple[ScientificInputReference, ...]
    artifact_references: tuple[ArtifactReference, ...]
    covalent_reaction_target: CovalentReactionTarget | None
    accepted_partial_target: PartialCovalentReactionTarget | None
    accepted_evidence: tuple[ScientificEvidenceProposal, ...]


class ResearchSessionRepository:
    """Strict one-record-per-directory persistence for research sessions."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()
        self._started = False

    def start(self) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            metadata = self.root.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise RuntimeError("Research session repository root is unsafe.")
            self._started = True

    def close(self) -> None:
        with self._lock:
            self._started = False

    def create(self, session: ResearchSession) -> ResearchSession:
        with self._lock:
            self._require_started()
            directory = self.root / session.session_identifier
            if directory.exists():
                raise RuntimeError("Research session identity conflict.")
            directory.mkdir()
            write_json_atomic(
                directory / "record.json",
                session,
                maximum_bytes=_MAXIMUM_SESSION_BYTES,
            )
            return session

    def get(self, session_identifier: str) -> ResearchSession:
        if _SESSION_ID.fullmatch(session_identifier) is None:
            raise KeyError("Research session not found.")
        with self._lock:
            self._require_started()
            directory = self.root / session_identifier
            path = directory / "record.json"
            try:
                if (
                    directory.resolve(strict=True).parent
                    != self.root.resolve(strict=True)
                    or path.resolve(strict=True).parent
                    != directory.resolve(strict=True)
                    or stat.S_ISLNK(directory.lstat().st_mode)
                    or stat.S_ISLNK(path.lstat().st_mode)
                    or path.stat().st_size > _MAXIMUM_SESSION_BYTES
                ):
                    raise KeyError("Research session not found.")
                session = ResearchSession.model_validate(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                raise KeyError("Research session not found.") from None
            if session.session_identifier != session_identifier:
                raise KeyError("Research session not found.")
            return session

    def replace(
        self,
        session: ResearchSession,
        *,
        expected_revision: int,
    ) -> ResearchSession:
        with self._lock:
            current = self.get(session.session_identifier)
            if current.revision != expected_revision:
                raise RuntimeError("Research session changed concurrently.")
            if (
                session.created_at != current.created_at
                or session.revision != current.revision + 1
                or session.updated_at <= current.updated_at
                or session.tenant_identifier_sha256
                != current.tenant_identifier_sha256
            ):
                raise RuntimeError("Research session history cannot be rewritten.")
            write_json_atomic(
                self.root / session.session_identifier / "record.json",
                session,
                maximum_bytes=_MAXIMUM_SESSION_BYTES,
            )
            return session

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("Research session repository is unavailable.")


class ResearchSessionController:
    """One top-level facade for conversation, compilation, and execution."""

    def __init__(
        self,
        *,
        repository: ResearchSessionRepository,
        execution_repository: ScientificExecutionRepository,
        question_writer: ScientificQuestionWriter,
        requirement_interpreter: ProviderNeutralScientificRequirementInterpreter | None = None,
        intent_interpreter: ScientificIntentInterpreter | None = None,
        evidence_interpreter: ScientificEvidenceInterpreter | None = None,
        evidence_resolver: ScientificSessionEvidenceResolver | None = None,
        runtime: ScientificObjectiveRuntime | None = None,
    ) -> None:
        self.repository = repository
        self.execution_repository = execution_repository
        self.question_writer = question_writer
        self.requirement_interpreter = requirement_interpreter
        self.intent_interpreter = intent_interpreter
        self.evidence_interpreter = evidence_interpreter
        self.evidence_resolver = evidence_resolver
        self.runtime = runtime
        self._lock = threading.RLock()

    def create(
        self,
        request: ResearchSessionCreateRequest,
        *,
        tenant_identifier_sha256: str,
    ) -> ResearchSession:
        validate_sha256(tenant_identifier_sha256)
        with self._lock:
            now = datetime.now(UTC)
            identifier = "research-session-" + hashlib.sha256(
                (
                    tenant_identifier_sha256
                    + "\x1f"
                    + request.question
                    + "\x1f"
                    + now.isoformat()
                ).encode("utf-8")
            ).hexdigest()[:32]
            first_turn = self._turn(
                session_identifier=identifier,
                position=1,
                role="scientist",
                content=request.question,
                created_at=now,
            )
            requirement_proposal = (
                self.requirement_interpreter.propose(request.question)
                if self.requirement_interpreter is not None
                else None
            )
            accepted_requirements: ValidatedResearchRequirements | None = None
            requirement_validation_error: str | None = None
            if requirement_proposal is not None:
                try:
                    accepted_requirements = validate_requirement_proposal(
                        requirement_proposal,
                        available_input_types=tuple(
                            item.artifact_type for item in request.input_references
                        ),
                    )
                except ValueError as error:
                    requirement_validation_error = str(error)
                else:
                    requirement_proposal = None
            effective_inputs = request.input_references
            effective_artifacts = request.artifact_references
            effective_target = request.covalent_reaction_target
            accepted_evidence: tuple[ScientificEvidenceProposal, ...] = ()
            accepted_partial_target: PartialCovalentReactionTarget | None = None
            if requirement_validation_error is not None:
                compilation = execution = proposal = evidence_proposal = None
                questions = (
                    ClarificationPrompt(
                        requirement_identifier="requirement-capability-composition",
                        question=(
                            requirement_validation_error
                            + " Please clarify the desired research outcome; "
                            "Pulsate will resume this session."
                        ),
                    ),
                )
            else:
                automatic = self._compile_with_automatic_evidence(
                    session_identifier=identifier,
                    scientist_turns=(first_turn,),
                    input_references=effective_inputs,
                    artifact_references=effective_artifacts,
                    covalent_reaction_target=effective_target,
                    budget=request.budget,
                    tenant_identifier_sha256=tenant_identifier_sha256,
                    accepted_research_requirements=accepted_requirements,
                )
                compilation = automatic.compilation
                execution = automatic.execution
                questions = automatic.questions
                proposal = automatic.intent_proposal
                evidence_proposal = automatic.evidence_proposal
                effective_inputs = automatic.input_references
                effective_artifacts = automatic.artifact_references
                effective_target = automatic.covalent_reaction_target
                accepted_partial_target = automatic.accepted_partial_target
                accepted_evidence = automatic.accepted_evidence
            conversation = (
                first_turn,
                *self._question_turns(identifier, questions, 2, now),
            )
            unapproved_inputs = (
                ()
                if request.accept_acquired_input_evidence
                else _acquired_artifact_identifiers(request.artifact_references)
            )
            status, summary = self._state(
                execution, questions, unapproved_inputs=unapproved_inputs
            )
            return self.repository.create(
                ResearchSession(
                    session_identifier=identifier,
                    tenant_identifier_sha256=tenant_identifier_sha256,
                    created_at=now,
                    updated_at=now,
                    revision=1,
                    status=status,
                    conversation=conversation,
                    input_references=effective_inputs,
                    artifact_references=effective_artifacts,
                    unapproved_input_artifact_identifiers=unapproved_inputs,
                    covalent_reaction_target=effective_target,
                    budget=request.budget,
                    next_questions=questions,
                    clarification_attempts=self._next_attempts({}, questions),
                    intent_proposal=proposal,
                    requirement_proposal=requirement_proposal,
                    accepted_research_requirements=accepted_requirements,
                    evidence_proposal=evidence_proposal,
                    accepted_partial_covalent_reaction_target=(
                        accepted_partial_target
                    ),
                    accepted_evidence=accepted_evidence,
                    compilation=compilation,
                    execution_status=(
                        execution.status if execution is not None else None
                    ),
                    scientist_summary=summary,
                )
            )

    def reply(
        self,
        session_identifier: str,
        request: ResearchSessionReplyRequest,
        *,
        tenant_identifier_sha256: str,
    ) -> ResearchSession:
        with self._lock:
            current = self._owned(session_identifier, tenant_identifier_sha256)
            if current.status in {"running", "verifying", "replanning"}:
                raise ValueError("Research is running. Wait for the result before changing the question.")
            if current.status == "completed" and current.compilation and self.runtime is not None and not any((
                request.input_references, request.artifact_references, request.budget,
                request.covalent_reaction_target, request.accept_intent_proposal,
                request.accept_requirement_proposal, request.accept_evidence_proposal,
                request.accept_acquired_input_evidence,
            )):
                assembler = self.runtime.capability_registry.get("scientist.result_assemble")
                reviewer = getattr(assembler, "review_candidate_evidence", None)
                if reviewer is not None:
                    record = self.execution_repository.get(current.compilation.execution_identifier)
                    previous = next((item for item in reversed(current.evidence_reviews) if item.candidate_identifiers), None)
                    review = reviewer(record, request.message, previous)
                    if review is not None:
                        now = self._next_time(current.updated_at)
                        question_turn = self._turn(session_identifier=session_identifier,
                            position=len(current.conversation) + 1, role="scientist",
                            content=request.message, created_at=now)
                        answer_turn = self._turn(session_identifier=session_identifier,
                            position=len(current.conversation) + 2, role="pulsate",
                            content=review.response, created_at=now)
                        return self.repository.replace(current.model_copy(update={
                            "updated_at": now, "revision": current.revision + 1,
                            "conversation": (*current.conversation, question_turn, answer_turn),
                            "evidence_reviews": (*current.evidence_reviews, review),
                        }), expected_revision=current.revision)
            now = self._next_time(current.updated_at)
            scientist_turn = self._turn(
                session_identifier=session_identifier,
                position=len(current.conversation) + 1,
                role="scientist",
                content=request.message,
                created_at=now,
            )
            scientist_turns = tuple(
                item for item in (*current.conversation, scientist_turn)
                if item.role == "scientist"
            )
            inputs = _merge_identified(
                current.input_references,
                request.input_references,
                attribute="reference_identifier",
            )
            artifacts = _merge_identified(
                current.artifact_references,
                request.artifact_references,
                attribute="artifact_identifier",
            )
            reaction_target = (
                request.covalent_reaction_target
                if request.covalent_reaction_target is not None
                else current.covalent_reaction_target
            )
            budget = (
                request.budget
                if request.budget is not None
                else current.budget
            )
            intent_proposal = current.intent_proposal
            requirement_proposal = current.requirement_proposal
            accepted_requirements = current.accepted_research_requirements
            evidence_proposal = current.evidence_proposal
            selected_entity_proposal = self._selected_entity_proposal(
                pending=evidence_proposal,
                reply=scientist_turn,
                input_references=inputs,
                tenant_identifier_sha256=tenant_identifier_sha256,
            )
            accepted_partial_target = (
                current.accepted_partial_covalent_reaction_target
            )
            accepted_task_type = (
                intent_proposal.task_type
                if request.accept_intent_proposal and intent_proposal is not None
                else current.accepted_task_type
            )
            requirement_validation_error: str | None = None
            if requirement_proposal is not None and accepted_requirements is None:
                try:
                    accepted_requirements = validate_requirement_proposal(
                        requirement_proposal,
                        available_input_types=tuple(
                            item.artifact_type for item in inputs
                        ),
                    )
                except ValueError as error:
                    requirement_validation_error = str(error)
                else:
                    requirement_proposal = None
            accepted_evidence = current.accepted_evidence
            unapproved_inputs = set(
                current.unapproved_input_artifact_identifiers
            )
            if request.artifact_references is not None:
                unapproved_inputs.update(
                    _acquired_artifact_identifiers(request.artifact_references)
                )
            if request.accept_acquired_input_evidence:
                unapproved_inputs.clear()
            if (
                request.accept_evidence_proposal
                and evidence_proposal is not None
                and evidence_proposal.source_kind != "entity_resolution_candidates"
            ):
                inputs = self._accept_proposed_inputs(
                    inputs,
                    evidence_proposal.input_references,
                )
                artifacts = self._accept_proposed_artifacts(
                    artifacts,
                    evidence_proposal.artifact_references,
                )
                reaction_target = (
                    evidence_proposal.covalent_reaction_target
                    or reaction_target
                )
                if evidence_proposal.partial_covalent_reaction_target is not None:
                    accepted_partial_target = self._merge_partial_target(
                        accepted_partial_target,
                        evidence_proposal.partial_covalent_reaction_target,
                    )
                    reaction_target = (
                        accepted_partial_target.complete_target()
                        or reaction_target
                    )
                accepted_evidence = (*accepted_evidence, evidence_proposal)
                unapproved_inputs.difference_update(
                    item.artifact_identifier
                    for item in evidence_proposal.artifact_references
                )
            if (
                selected_entity_proposal is not None
                and self._evidence_can_be_accepted_automatically(
                    selected_entity_proposal
                )
            ):
                inputs = self._accept_proposed_inputs(
                    inputs,
                    selected_entity_proposal.input_references,
                )
                artifacts = self._accept_proposed_artifacts(
                    artifacts,
                    selected_entity_proposal.artifact_references,
                )
                accepted_evidence = (*accepted_evidence, selected_entity_proposal)
                unapproved_inputs.difference_update(
                    item.artifact_identifier
                    for item in selected_entity_proposal.artifact_references
                )
                evidence_proposal = None
                selected_entity_proposal = None
            if requirement_validation_error is not None:
                compilation = execution = proposal = next_evidence_proposal = None
                questions = (
                    ClarificationPrompt(
                        requirement_identifier="requirement-capability-composition",
                        question=(
                            requirement_validation_error
                            + " Please clarify the target, available structures, or "
                            "desired output; Pulsate will resume this session."
                        ),
                    ),
                )
            elif requirement_proposal is not None and accepted_requirements is None:
                compilation = execution = proposal = next_evidence_proposal = None
                questions = self._questions_for_requirement_proposal(
                    requirement_proposal
                )
            else:
                automatic = self._compile_with_automatic_evidence(
                    session_identifier=session_identifier,
                    scientist_turns=scientist_turns,
                    input_references=inputs,
                    artifact_references=artifacts,
                    covalent_reaction_target=reaction_target,
                    budget=budget,
                    tenant_identifier_sha256=tenant_identifier_sha256,
                    accepted_task_type=accepted_task_type,
                    accepted_partial_target=accepted_partial_target,
                    accepted_evidence=accepted_evidence,
                    accepted_research_requirements=accepted_requirements,
                    previous_questions=current.next_questions,
                    clarification_attempts=current.clarification_attempts,
                )
                compilation = automatic.compilation
                execution = automatic.execution
                questions = automatic.questions
                proposal = automatic.intent_proposal
                next_evidence_proposal = automatic.evidence_proposal
                inputs = automatic.input_references
                artifacts = automatic.artifact_references
                reaction_target = automatic.covalent_reaction_target
                accepted_partial_target = automatic.accepted_partial_target
                accepted_evidence = automatic.accepted_evidence
                if selected_entity_proposal is not None:
                    next_evidence_proposal = selected_entity_proposal
                    questions = self._questions_for_evidence_proposal(
                        questions,
                        selected_entity_proposal,
                    )
            next_attempts = self._next_attempts(
                current.clarification_attempts, questions
            )
            start = len(current.conversation) + 2
            question_turns = self._question_turns(
                session_identifier, questions, start, now
            )
            unresolved_input_approvals = tuple(sorted(unapproved_inputs))
            status, summary = self._state(
                execution,
                questions,
                unapproved_inputs=unresolved_input_approvals,
            )
            replacement = current.model_copy(
                update={
                    "updated_at": now,
                    "revision": current.revision + 1,
                    "status": status,
                    "conversation": (
                        *current.conversation,
                        scientist_turn,
                        *question_turns,
                    ),
                    "input_references": inputs,
                    "artifact_references": artifacts,
                    "unapproved_input_artifact_identifiers": (
                        unresolved_input_approvals
                    ),
                    "covalent_reaction_target": reaction_target,
                    "budget": budget,
                    "next_questions": questions,
                    "clarification_attempts": next_attempts,
                    "intent_proposal": proposal,
                    "accepted_task_type": accepted_task_type,
                    "requirement_proposal": requirement_proposal,
                    "accepted_research_requirements": accepted_requirements,
                    "evidence_proposal": next_evidence_proposal,
                    "accepted_partial_covalent_reaction_target": (
                        accepted_partial_target
                    ),
                    "accepted_evidence": accepted_evidence,
                    "compilation": compilation,
                    "execution_status": (
                        execution.status if execution is not None else None
                    ),
                    "scene_identifier": None,
                    "execution_steps": (),
                    "scientist_result": None,
                    "scientist_summary": summary,
                }
            )
            return self.repository.replace(
                replacement, expected_revision=current.revision
            )

    def get(
        self,
        session_identifier: str,
        *,
        tenant_identifier_sha256: str,
    ) -> ResearchSession:
        current = self._owned(session_identifier, tenant_identifier_sha256)
        if current.compilation is None:
            return current
        execution = self.execution_repository.get(current.compilation.execution_identifier)
        if current.status not in {"running", "planned", "failed", "completed"}:
            return current
        status = {
            "running": "running", "succeeded": "completed", "failed": "failed",
        }.get(execution.status, current.status)
        # Read the durable execution snapshot without acquiring the runtime lock.
        # A disconnected caller can recover progress and the final verified result.
        return current.model_copy(update={
            "status": status,
            "execution_status": execution.status,
            "execution_steps": execution.node_executions,
            "scene_identifier": execution.scene_identifier,
            "scientist_result": execution.scientist_result if status == "completed" else None,
            "scientist_summary": (
                execution.scientist_summary
                if execution.status in {"running", "succeeded", "failed"}
                else current.scientist_summary
            ),
        })

    def execute(
        self,
        session_identifier: str,
        *,
        tenant_identifier_sha256: str,
    ) -> ResearchSession:
        if self.runtime is None:
            raise RuntimeError("Unified scientific runtime is unavailable.")
        with self._lock:
            current = self._owned(session_identifier, tenant_identifier_sha256)
            if current.compilation is None or current.status != "planned":
                raise ValueError(
                    "This research session needs clarification or approval "
                    "before execution."
                )
            current = self.repository.replace(
                current.model_copy(update={
                    "status": "running",
                    "execution_status": "running",
                    "revision": current.revision + 1,
                    "updated_at": self._next_time(current.updated_at),
                    "scientist_summary": "Research is running. You can reopen this session to check progress.",
                }),
                expected_revision=current.revision,
            )
        # Never hold the conversation lock across a potentially long calculation.
        # The running transition above rejects duplicate submissions and replies.
        try:
            self.runtime.execute(current.compilation.execution_identifier)
        finally:
            with self._lock:
                persisted = self._owned(session_identifier, tenant_identifier_sha256)
                snapshot = self.get(
                    session_identifier,
                    tenant_identifier_sha256=tenant_identifier_sha256,
                )
                if snapshot.execution_status == "planned":
                    snapshot = snapshot.model_copy(update={"status": "planned"})
                self.repository.replace(
                    snapshot.model_copy(update={
                        "updated_at": self._next_time(persisted.updated_at),
                        "revision": persisted.revision + 1,
                    }),
                    expected_revision=persisted.revision,
                )
        return self.get(
            session_identifier,
            tenant_identifier_sha256=tenant_identifier_sha256,
        )

    def capability(self) -> dict[str, object]:
        return {
            "research_session": "unified",
            "conversation": "resumable",
            "canonical_objective": "cgr.science.ScientificObjective",
            "canonical_plan": "cgr.science.CandidateResearchPlan",
            "canonical_graph": "cgr.workflow_graph.WorkflowGraphDefinition",
            "language_model": self.question_writer.capability(),
            "intent_interpreter": (
                self.intent_interpreter.capability()
                if self.intent_interpreter is not None
                else {"available": False, "requires_scientist_confirmation": True}
            ),
            "requirement_interpreter": (
                self.requirement_interpreter.capability()
                if self.requirement_interpreter is not None
                else {"available": False, "requires_scientist_confirmation": True}
            ),
            "planning_mode": "llm_requirements_deterministic_capability_composition",
            "interaction_policy": {
                "complete_questions_proceed_without_confirmation": True,
                "follow_up_policy": "missing_or_ambiguous_evidence_only",
                "unique_high_confidence_acquisition_auto_applied": True,
                "multiple_meaningful_entity_matches_require_selection": True,
                "model_extracted_scientific_values_require_review": True,
                "session_resume_mode": "same_canonical_research_session",
            },
            "scientific_capability_truth": (
                self.execution_repository.capability_truth_summary()
            ),
            "compute_routing": self.execution_repository.compute_routing_summary(),
            "visualization": {
                "research_session_workspace": True,
                "evidence_grounded_interactions": True,
                "computational_overlays": True,
                "candidate_comparison_and_export": True,
                "grounding_policy": (
                    "persisted_artifact_or_deterministic_computation_only"
                ),
            },
            "evidence_interpreter": (
                self.evidence_interpreter.capability()
                if self.evidence_interpreter is not None
                else {"available": False, "requires_scientist_confirmation": True}
            ),
            "structure_evidence_resolver": (
                self.evidence_resolver.capability()
                if self.evidence_resolver is not None
                else {"available": False, "requires_scientist_confirmation": True}
            ),
            "fact_policy": "scientist_evidence_or_deterministic_computation_only",
        }

    @staticmethod
    def _evidence_can_be_accepted_automatically(
        proposal: ScientificEvidenceProposal,
    ) -> bool:
        """Accept only unique, high-confidence trusted-source acquisitions."""

        if proposal.source_kind not in {
            "exact_identifier_acquisition",
            "named_identifier_acquisition",
        }:
            return False
        if proposal.entity_candidates or not proposal.artifact_references:
            return False
        return all(
            item.metadata.get("evidence_resolution_confidence") == "high"
            and isinstance(item.metadata.get("acquisition_source_kind"), str)
            and bool(str(item.metadata["acquisition_source_kind"]).strip())
            for item in proposal.artifact_references
        )

    def _compile_with_automatic_evidence(
        self,
        *,
        session_identifier: str,
        scientist_turns: tuple[ResearchConversationTurn, ...],
        input_references: tuple[ScientificInputReference, ...],
        artifact_references: tuple[ArtifactReference, ...],
        covalent_reaction_target: CovalentReactionTarget | None,
        budget: ScientificExecutionBudget,
        tenant_identifier_sha256: str,
        accepted_task_type: ScientificTask | None = None,
        accepted_partial_target: PartialCovalentReactionTarget | None = None,
        accepted_evidence: tuple[ScientificEvidenceProposal, ...] = (),
        accepted_research_requirements: ValidatedResearchRequirements | None = None,
        previous_questions: tuple[ClarificationPrompt, ...] = (),
        clarification_attempts: dict[str, int] | None = None,
    ) -> _AutomaticCompilation:
        """Compile, applying unambiguous trusted acquisitions in the same turn."""

        inputs = input_references
        artifacts = artifact_references
        target = covalent_reaction_target
        partial = accepted_partial_target
        evidence = accepted_evidence
        for _attempt in range(4):
            (
                compilation,
                execution,
                questions,
                intent_proposal,
                evidence_proposal,
            ) = self._compile(
                session_identifier=session_identifier,
                scientist_turns=scientist_turns,
                input_references=inputs,
                artifact_references=artifacts,
                covalent_reaction_target=target,
                budget=budget,
                tenant_identifier_sha256=tenant_identifier_sha256,
                accepted_task_type=accepted_task_type,
                accepted_partial_target=partial,
                accepted_evidence=evidence,
                accepted_research_requirements=accepted_research_requirements,
                previous_questions=previous_questions,
                clarification_attempts=clarification_attempts,
            )
            if (
                evidence_proposal is None
                or not self._evidence_can_be_accepted_automatically(
                    evidence_proposal
                )
            ):
                return _AutomaticCompilation(
                    compilation=compilation,
                    execution=execution,
                    questions=questions,
                    intent_proposal=intent_proposal,
                    evidence_proposal=evidence_proposal,
                    input_references=inputs,
                    artifact_references=artifacts,
                    covalent_reaction_target=target,
                    accepted_partial_target=partial,
                    accepted_evidence=evidence,
                )
            inputs = self._accept_proposed_inputs(
                inputs,
                evidence_proposal.input_references,
            )
            artifacts = self._accept_proposed_artifacts(
                artifacts,
                evidence_proposal.artifact_references,
            )
            if evidence_proposal.covalent_reaction_target is not None:
                target = evidence_proposal.covalent_reaction_target
            if evidence_proposal.partial_covalent_reaction_target is not None:
                partial = self._merge_partial_target(
                    partial,
                    evidence_proposal.partial_covalent_reaction_target,
                )
                target = partial.complete_target() or target
            if not any(
                item.proposal_identifier == evidence_proposal.proposal_identifier
                for item in evidence
            ):
                evidence = (*evidence, evidence_proposal)
        raise RuntimeError(
            "Automatic evidence acquisition exceeded its bounded resolution passes."
        )

    def _compile(
        self,
        *,
        session_identifier: str,
        scientist_turns: tuple[ResearchConversationTurn, ...],
        input_references: tuple[ScientificInputReference, ...],
        artifact_references: tuple[ArtifactReference, ...],
        covalent_reaction_target: CovalentReactionTarget | None,
        budget: ScientificExecutionBudget,
        tenant_identifier_sha256: str,
        accepted_task_type: ScientificTask | None = None,
        accepted_partial_target: PartialCovalentReactionTarget | None = None,
        accepted_evidence: tuple[ScientificEvidenceProposal, ...] = (),
        accepted_research_requirements: ValidatedResearchRequirements | None = None,
        previous_questions: tuple[ClarificationPrompt, ...] = (),
        clarification_attempts: dict[str, int] | None = None,
    ) -> tuple[
        ResearchCompilation | None,
        ScientificExecutionRecord | None,
        tuple[ClarificationPrompt, ...],
        ScientificIntentProposal | None,
        ScientificEvidenceProposal | None,
    ]:
        effective_question = " Scientist follow-up: ".join(
            item.content for item in scientist_turns
        )
        if len(effective_question) > 8192:
            questions = self.question_writer.write(
                (
                    (
                        "requirement-question-scope",
                        "The accumulated question exceeds the supported "
                        "interpretation scope.",
                    ),
                )
            )
            questions = self._prevent_repeated_checklist(
                questions,
                previous_questions=previous_questions,
                attempts=clarification_attempts or {},
            )
            return None, None, questions, None, None
        request = ScientificObjectiveCompileRequest(
            question=effective_question,
            input_references=input_references,
            artifact_references=artifact_references,
            covalent_reaction_target=covalent_reaction_target,
            budget=budget,
            research_requirements=accepted_research_requirements,
        )
        try:
            execution = self.execution_repository.create(
                request,
                tenant_identifier_sha256=tenant_identifier_sha256,
                project_identifier=session_identifier,
            )
        except ScientificCapabilityGroundingError as error:
            return None, None, (ClarificationPrompt(
                requirement_identifier="requirement-execution-capability",
                question=(
                    "This research needs an execution capability that is not configured: "
                    + ", ".join(error.capability_names)
                    + ". This is a platform configuration limitation, not missing scientific information. "
                    "An administrator must configure the executor before this request can run."
                ),
            ),), None, None
        except ValueError:
            execution = None
            if accepted_task_type is not None:
                effective_question = (
                    effective_question
                    + " Scientist-confirmed registered research goal: "
                    + accepted_task_type.replace("_", " ")
                    + "."
                )
                request = request.model_copy(update={"question": effective_question})
                try:
                    execution = self.execution_repository.create(
                        request,
                        tenant_identifier_sha256=tenant_identifier_sha256,
                        project_identifier=session_identifier,
                    )
                except ValueError:
                    execution = None
            if execution is None:
                proposal = (
                    self.intent_interpreter.propose(effective_question)
                    if self.intent_interpreter is not None
                    else None
                )
                if proposal is not None:
                    interpreted_question = (
                        effective_question
                        + " Exact-quote model interpretation of the registered "
                        "research goal: "
                        + proposal.task_type.replace("_", " ")
                        + "."
                    )
                    interpreted_request = request.model_copy(
                        update={"question": interpreted_question}
                    )
                    try:
                        execution = self.execution_repository.create(
                            interpreted_request,
                            tenant_identifier_sha256=tenant_identifier_sha256,
                            project_identifier=session_identifier,
                        )
                    except ValueError:
                        execution = None
                    else:
                        effective_question = interpreted_question
                if execution is not None:
                    proposal = None
                else:
                    reason = (
                        "The proposed registered research goal still cannot be "
                        "composed safely. Please describe the desired output more "
                        "precisely."
                        if proposal is not None
                        else (
                            "The research goal is not yet specific enough to route "
                            "through a registered capability."
                        )
                    )
                    questions = self.question_writer.write(
                        ((
                            "requirement-research-goal",
                            reason,
                        ),)
                    )
                    questions = self._prevent_repeated_checklist(
                        questions,
                        previous_questions=previous_questions,
                        attempts=clarification_attempts or {},
                    )
                    return None, None, questions, proposal, None

        assert execution is not None
        compilation, execution, questions, no_intent = self._compiled_result(
            session_identifier=session_identifier,
            effective_question=effective_question,
            execution=execution,
            input_references=input_references,
            artifact_references=artifact_references,
            covalent_reaction_target=covalent_reaction_target,
            budget=budget,
            tenant_identifier_sha256=tenant_identifier_sha256,
        )
        try:
            evidence_proposal = self._evidence_proposal(
                objective=execution.objective,
                scientist_turns=scientist_turns,
                input_references=input_references,
                artifact_references=artifact_references,
                covalent_reaction_target=covalent_reaction_target,
                tenant_identifier_sha256=tenant_identifier_sha256,
                accepted_partial_target=accepted_partial_target,
                accepted_evidence=accepted_evidence,
            )
        except (TypeError, ValueError):
            # Model-derived or acquired evidence is non-authoritative. A malformed
            # proposal must fail closed into the existing clarification path.
            evidence_proposal = None
        if evidence_proposal is not None:
            questions = self._questions_for_evidence_proposal(
                questions, evidence_proposal
            )
        elif self.evidence_resolver is not None:
            unresolved_reason = self.evidence_resolver.unresolved_reason(
                input_references=input_references,
                artifact_references=artifact_references,
                covalent_reaction_target=covalent_reaction_target,
            )
            if unresolved_reason is not None:
                questions = self._questions_for_resolution_failure(
                    questions, unresolved_reason
                )
        if accepted_partial_target is not None and covalent_reaction_target is None:
            questions = self._questions_for_partial_target(
                questions, accepted_partial_target
            )
        questions = self._prevent_repeated_checklist(
            questions,
            previous_questions=previous_questions,
            attempts=clarification_attempts or {},
        )
        return compilation, execution, questions, no_intent, evidence_proposal

    def _compiled_result(
        self,
        *,
        session_identifier: str,
        effective_question: str,
        execution: ScientificExecutionRecord,
        input_references: tuple[ScientificInputReference, ...],
        artifact_references: tuple[ArtifactReference, ...],
        covalent_reaction_target: CovalentReactionTarget | None,
        budget: ScientificExecutionBudget,
        tenant_identifier_sha256: str,
    ) -> tuple[
        ResearchCompilation,
        ScientificExecutionRecord,
        tuple[ClarificationPrompt, ...],
        None,
    ]:

        objective = execution.objective
        requirement_pairs = list(
            zip(
                unresolved_requirement_identifiers(objective),
                objective.ambiguity_hypotheses,
                strict=True,
            )
        )
        declared_artifacts = {
            item.artifact_identifier for item in input_references
        }
        persisted_artifacts = {
            item.artifact_identifier for item in artifact_references
        }
        if missing_artifacts := tuple(
            sorted(declared_artifacts - persisted_artifacts)
        ):
            requirement_pairs.append(
                (
                    "requirement-exact-input-artifacts",
                    "Exact persisted input artifacts are required for: "
                    + ", ".join(missing_artifacts),
                )
            )
        requirements = tuple(requirement_pairs)
        questions = self.question_writer.write(requirements)
        if (
            execution.canonical_objective is None
            or execution.canonical_plan is None
            or execution.canonical_graph is None
        ):
            raise RuntimeError("Scientific execution omitted canonical compilation.")
        canonical_objective = execution.canonical_objective
        candidate_plan = execution.canonical_plan
        graph = execution.canonical_graph
        compilation = ResearchCompilation(
            compilation_identifier=(
                "research-compilation-"
                + hashlib.sha256(
                    (
                        session_identifier
                        + "\x1f"
                        + execution.execution_identifier
                    ).encode("utf-8")
                ).hexdigest()[:32]
            ),
            compiled_at=datetime.now(UTC),
            execution_identifier=execution.execution_identifier,
            effective_question=effective_question,
            input_references=input_references,
            artifact_references=artifact_references,
            covalent_reaction_target=covalent_reaction_target,
            budget=budget,
            compatibility_objective=objective,
            compatibility_plan=execution.plan,
            canonical_objective=canonical_objective,
            canonical_plan=candidate_plan,
            canonical_graph=graph,
        )
        return compilation, execution, questions, None

    def _evidence_proposal(
        self,
        *,
        objective: StructuredScientificObjective,
        scientist_turns: tuple[ResearchConversationTurn, ...],
        input_references: tuple[ScientificInputReference, ...],
        artifact_references: tuple[ArtifactReference, ...],
        covalent_reaction_target: CovalentReactionTarget | None,
        tenant_identifier_sha256: str,
        accepted_partial_target: PartialCovalentReactionTarget | None,
        accepted_evidence: tuple[ScientificEvidenceProposal, ...],
    ) -> ScientificEvidenceProposal | None:
        capability_profile = (
            objective.research_requirements.capability_profile
            if objective.research_requirements is not None
            else objective.task_type
        )
        if capability_profile not in {
            "covalent_transition_state",
            "metal_active_site_quantum",
            "protein_ligand_discovery",
            "structure_analysis",
        }:
            return None
        exact_identifiers = (
            self.evidence_resolver.acquire_exact_identifiers(
                scientist_text=scientist_turns[-1].content,
                input_references=input_references,
                artifact_references=artifact_references,
                tenant_identifier_sha256=tenant_identifier_sha256,
            )
            if self.evidence_resolver is not None
            else None
        )
        if exact_identifiers is not None:
            return exact_identifiers
        if (
            capability_profile == "covalent_transition_state"
            and covalent_reaction_target is not None
        ):
            return None
        deterministic = (
            self.evidence_resolver.propose_covalent_evidence(
                input_references=input_references,
                artifact_references=artifact_references,
                covalent_reaction_target=covalent_reaction_target,
                tenant_identifier_sha256=tenant_identifier_sha256,
            )
            if (
                self.evidence_resolver is not None
                and capability_profile == "covalent_transition_state"
            )
            else None
        )
        if deterministic is not None:
            return deterministic
        if self.evidence_interpreter is None:
            return None
        accepted_turn_identifiers = {
            quote.turn_identifier
            for accepted in accepted_evidence
            for quote in accepted.supporting_quotes
        }
        evidence_turns = tuple(
            ScientistEvidenceTurn(
                turn_identifier=item.turn_identifier,
                content=item.content,
            )
            for item in scientist_turns
            if item.turn_identifier not in accepted_turn_identifiers
        )
        if not evidence_turns:
            return None
        named_evidence: ScientificEvidenceProposal | None = None
        named_protein_evidence: ScientificEvidenceProposal | None = None
        if (
            self.evidence_resolver is not None
            and not any(
                item.artifact_type == "protein_structure"
                for item in input_references
            )
        ):
            for evidence_turn in reversed(evidence_turns):
                named_candidate = self.evidence_interpreter.propose_named_protein(
                    (evidence_turn,)
                )
                if named_candidate is None:
                    continue
                named_protein_evidence = self.evidence_resolver.acquire_named_entity(
                    candidate=named_candidate,
                    input_references=input_references,
                    tenant_identifier_sha256=tenant_identifier_sha256,
                )
                if named_protein_evidence is not None:
                    break
        if (
            self.evidence_resolver is not None
            and not any(
                item.artifact_type == "ligand_structure"
                for item in input_references
            )
        ):
            for evidence_turn in reversed(evidence_turns):
                named_candidate = self.evidence_interpreter.propose_named_ligand(
                    (evidence_turn,)
                )
                if named_candidate is None:
                    continue
                named_evidence = self.evidence_resolver.acquire_named_entity(
                    candidate=named_candidate,
                    input_references=input_references,
                    tenant_identifier_sha256=tenant_identifier_sha256,
                )
                if named_evidence is not None:
                    break
        if (
            named_protein_evidence is not None
            and named_protein_evidence.source_kind == "entity_resolution_candidates"
        ):
            return named_protein_evidence
        if (
            named_evidence is not None
            and named_evidence.source_kind == "entity_resolution_candidates"
        ):
            return named_evidence
        if named_protein_evidence is not None:
            if named_evidence is None:
                named_evidence = named_protein_evidence
            else:
                combined_identity = (
                    named_protein_evidence.proposal_identifier
                    + "\x1f"
                    + named_evidence.proposal_identifier
                )
                named_evidence = named_evidence.model_copy(
                    update={
                        "proposal_identifier": (
                            "evidence-proposal-"
                            + hashlib.sha256(
                                combined_identity.encode("utf-8")
                            ).hexdigest()[:32]
                        ),
                        "summary": (
                            "Pulsate resolved the literal protein and ligand names "
                            "to trusted-source structures. Review both identities, "
                            "provenance records, and confidence before use."
                        ),
                        "input_references": (
                            *named_protein_evidence.input_references,
                            *named_evidence.input_references,
                        ),
                        "artifact_references": (
                            *named_protein_evidence.artifact_references,
                            *named_evidence.artifact_references,
                        ),
                        "supporting_quotes": (
                            *named_protein_evidence.supporting_quotes,
                            *named_evidence.supporting_quotes,
                        ),
                    }
                )
        proposal = (
            self.evidence_interpreter.propose_covalent_target(evidence_turns)
            if capability_profile == "covalent_transition_state"
            else None
        )
        if named_evidence is None:
            return proposal
        if proposal is None:
            return named_evidence
        canonical = (
            named_evidence.proposal_identifier
            + "\x1f"
            + proposal.proposal_identifier
        )
        return named_evidence.model_copy(
            update={
                "proposal_identifier": (
                    "evidence-proposal-"
                    + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
                ),
                "summary": (
                    "Pulsate resolved one literal named input and extracted "
                    "additional reaction evidence from exact scientist quotes. "
                    "Review and confirm the combined proposal before use."
                ),
                "covalent_reaction_target": proposal.covalent_reaction_target,
                "partial_covalent_reaction_target": (
                    proposal.partial_covalent_reaction_target
                ),
                "supporting_quotes": (
                    *named_evidence.supporting_quotes,
                    *proposal.supporting_quotes,
                ),
            }
        )

    @staticmethod
    def _questions_for_evidence_proposal(
        questions: tuple[ClarificationPrompt, ...],
        proposal: ScientificEvidenceProposal,
    ) -> tuple[ClarificationPrompt, ...]:
        proposed_types = {
            item.artifact_type for item in proposal.input_references
        }
        retained: list[ClarificationPrompt] = []
        for item in questions:
            lowered = item.question.casefold()
            if (
                proposal.covalent_reaction_target is not None
                and any(
                    token in lowered
                    for token in (
                        "reaction-site",
                        "reaction site",
                        "reaction atom",
                        "reaction pattern",
                    )
                )
            ):
                continue
            if (
                "ligand" in lowered
                and "ligand_structure" in proposed_types
            ):
                continue
            if (
                "protein" in lowered
                and "protein_structure" in proposed_types
                and "ligand" not in lowered
            ):
                continue
            retained.append(item)
        if proposal.source_kind == "entity_resolution_candidates":
            choices = "; ".join(
                (
                    f"{item.display_label} [source {item.source_kind} "
                    f"{item.source_identifier}"
                    + (
                        f", structure {item.structure_identifier}"
                        if item.structure_identifier is not None
                        else ""
                    )
                    + "]"
                )
                for item in proposal.entity_candidates
            )
            question = (
                proposal.summary
                + " Reply with one exact source identifier from these candidates: "
                + choices
                + ". Pulsate will acquire that exact structure and ask for final "
                "provenance confirmation."
            )
        else:
            question = (
                proposal.summary
                + " Confirm the evidence proposal to apply it, or correct any "
                "value in your reply."
            )
        retained.append(
            ClarificationPrompt(
                requirement_identifier="requirement-evidence-proposal",
                question=question,
            )
        )
        return tuple(retained)

    @staticmethod
    def _questions_for_requirement_proposal(
        proposal: ScientificRequirementProposal,
    ) -> tuple[ClarificationPrompt, ...]:
        operations = ", ".join(
            item.operation.replace("_", " ") for item in proposal.requirements
        )
        return (
            ClarificationPrompt(
                requirement_identifier="requirement-general-research-operations",
                question=(
                    "Pulsate understood these requested research operations: "
                    + operations
                    + ". Confirm this requirement proposal, or describe what should "
                    "be added, removed, or corrected."
                ),
            ),
        )

    def _selected_entity_proposal(
        self,
        *,
        pending: ScientificEvidenceProposal | None,
        reply: ResearchConversationTurn,
        input_references: tuple[ScientificInputReference, ...],
        tenant_identifier_sha256: str,
    ) -> ScientificEvidenceProposal | None:
        if (
            pending is None
            or pending.source_kind != "entity_resolution_candidates"
            or self.evidence_resolver is None
        ):
            return None
        selected = self._selected_entity_candidate(
            reply.content,
            pending.entity_candidates,
        )
        if selected is None:
            return None
        return self.evidence_resolver.acquire_selected_entity_candidate(
            candidate=selected,
            input_references=input_references,
            tenant_identifier_sha256=tenant_identifier_sha256,
        )

    @staticmethod
    def _selected_entity_candidate(
        reply: str,
        candidates: tuple[ScientificEntityResolutionCandidate, ...],
    ) -> ScientificEntityResolutionCandidate | None:
        def contains(identifier: str | None) -> bool:
            if identifier is None:
                return False
            return re.search(
                r"(?<![A-Za-z0-9])" + re.escape(identifier) + r"(?![A-Za-z0-9])",
                reply,
                re.IGNORECASE,
            ) is not None

        matched = []
        for candidate in candidates:
            source_identifier = candidate.source_identifier
            structure_identifier = candidate.structure_identifier
            same_source_count = sum(
                item.source_identifier == source_identifier
                for item in candidates
            )
            source_selected = contains(source_identifier) and (
                same_source_count == 1 or contains(structure_identifier)
            )
            structure_selected = contains(structure_identifier) and sum(
                item.structure_identifier == structure_identifier
                for item in candidates
            ) == 1
            if source_selected or structure_selected:
                matched.append(candidate)
        return matched[0] if len(matched) == 1 else None

    @staticmethod
    def _merge_partial_target(
        current: PartialCovalentReactionTarget | None,
        proposed: PartialCovalentReactionTarget,
    ) -> PartialCovalentReactionTarget:
        """Merge explicitly confirmed fields; newer confirmed corrections win."""

        values = (
            current.model_dump(exclude_none=True)
            if current is not None
            else {"reaction_family": "covalent_substitution"}
        )
        values.update(proposed.model_dump(exclude_none=True))
        return PartialCovalentReactionTarget.model_validate(values)

    @staticmethod
    def _questions_for_partial_target(
        questions: tuple[ClarificationPrompt, ...],
        partial: PartialCovalentReactionTarget,
    ) -> tuple[ClarificationPrompt, ...]:
        missing = partial.missing_required_fields
        if not missing:
            return questions
        labels = {
            "protein_chain_label": "protein chain label",
            "protein_residue_sequence": "protein residue sequence",
            "protein_atom_name": "protein atom name",
            "protein_nucleophile_formal_charge": "nucleophile formal charge",
            "ligand_reaction_smarts": "mapped ligand reaction SMARTS",
            "selected_total_qm_charge": "total QM charge",
            "selected_spin": "spin",
        }
        exact_missing = ", ".join(labels[item] for item in missing)
        repaired: list[ClarificationPrompt] = []
        changed = False
        for item in questions:
            lowered = item.question.casefold()
            if any(
                token in lowered
                for token in (
                    "reaction atom",
                    "reaction-site",
                    "reaction site",
                    "reaction smarts",
                    "charge",
                    "spin",
                )
            ):
                repaired.append(
                    item.model_copy(
                        update={
                            "question": (
                                "Pulsate retained your confirmed partial evidence. "
                                "The remaining exact fields are: "
                                + exact_missing
                                + "."
                            )
                        }
                    )
                )
                changed = True
            else:
                repaired.append(item)
        if not changed:
            repaired.append(
                ClarificationPrompt(
                    requirement_identifier="requirement-partial-reaction-evidence",
                    question=(
                        "Pulsate retained your confirmed partial evidence. The "
                        "remaining exact fields are: " + exact_missing + "."
                    ),
                )
            )
        return tuple(repaired)

    @staticmethod
    def _questions_for_resolution_failure(
        questions: tuple[ClarificationPrompt, ...],
        reason: str,
    ) -> tuple[ClarificationPrompt, ...]:
        explanation = {
            "no_explicit_protein_secondary_connection": (
                "The protein file contains no unique declared connection to a "
                "secondary component, so Pulsate cannot choose a ligand safely."
            ),
            "multiple_explicit_protein_secondary_connections": (
                "The protein file declares multiple possible secondary-component "
                "connections, so Pulsate needs you to identify the intended one."
            ),
            "protein_nucleophile_formal_charge_not_explicit": (
                "The source does not explicitly encode the selected nucleophile's "
                "formal charge, so Pulsate needs that value from you."
            ),
            "multiple_qm_spin_candidates": (
                "Deterministic preflight found more than one valid spin candidate, "
                "so Pulsate needs you to choose one."
            ),
        }.get(
            reason,
            "The persisted structure did not contain enough unique evidence for "
            "automatic resolution.",
        )
        repaired: list[ClarificationPrompt] = []
        for item in questions:
            if any(
                token in item.question.casefold()
                for token in ("ligand", "reaction-site", "reaction site")
            ):
                repaired.append(
                    item.model_copy(
                        update={"question": explanation + " " + item.question}
                    )
                )
            else:
                repaired.append(item)
        return tuple(repaired)

    @staticmethod
    def _prevent_repeated_checklist(
        questions: tuple[ClarificationPrompt, ...],
        *,
        previous_questions: tuple[ClarificationPrompt, ...],
        attempts: dict[str, int],
    ) -> tuple[ClarificationPrompt, ...]:
        previous_by_identifier = {
            item.requirement_identifier: item for item in previous_questions
        }
        repaired: list[ClarificationPrompt] = []
        for item in questions:
            previous = previous_by_identifier.get(item.requirement_identifier)
            count = attempts.get(item.requirement_identifier, 0)
            if (
                previous is None
                or count < 1
                or previous.question != item.question
            ):
                repaired.append(item)
                continue
            if item.requirement_identifier == "requirement-evidence-proposal":
                diagnostic = (
                    "The evidence proposal is still waiting for an explicit "
                    "confirmation or correction."
                )
            elif "ligand" in item.question.casefold():
                diagnostic = (
                    "I could not bind the previous reply to one exact ligand "
                    "structure. Provide a ligand file or label one exact source as "
                    "PubChem CID, SMILES, or InChI."
                )
            elif "protein" in item.question.casefold():
                diagnostic = (
                    "I could not bind the previous reply to one exact protein "
                    "structure. Provide a protein file or label one exact PDB ID."
                )
            else:
                diagnostic = (
                    "I could not validate the previous reply for this requirement. "
                    "Please state the requested value explicitly or attach its exact "
                    "evidence."
                )
            repaired.append(
                item.model_copy(
                    update={
                        "question": diagnostic + " " + item.question
                    }
                )
            )
        return tuple(repaired)

    @staticmethod
    def _next_attempts(
        previous: dict[str, int],
        questions: tuple[ClarificationPrompt, ...],
    ) -> dict[str, int]:
        updated = dict(previous)
        for item in questions:
            updated[item.requirement_identifier] = (
                updated.get(item.requirement_identifier, 0) + 1
            )
        return updated

    @staticmethod
    def _accept_proposed_inputs(
        current: tuple[ScientificInputReference, ...],
        proposed: tuple[ScientificInputReference, ...],
    ) -> tuple[ScientificInputReference, ...]:
        if not proposed:
            return current
        replaced_types = {item.artifact_type for item in proposed}
        values = [
            item for item in current if item.artifact_type not in replaced_types
        ]
        values.extend(proposed)
        return tuple(sorted(values, key=lambda item: item.reference_identifier))

    @staticmethod
    def _accept_proposed_artifacts(
        current: tuple[ArtifactReference, ...],
        proposed: tuple[ArtifactReference, ...],
    ) -> tuple[ArtifactReference, ...]:
        if not proposed:
            return current
        replaced_types = {item.artifact_type for item in proposed}
        values = [
            item for item in current if item.artifact_type not in replaced_types
        ]
        values.extend(proposed)
        return tuple(sorted(values, key=lambda item: item.artifact_identifier))

    @staticmethod
    def _state(
        execution: ScientificExecutionRecord | None,
        questions: tuple[ClarificationPrompt, ...],
        *,
        unapproved_inputs: tuple[str, ...] = (),
    ) -> tuple[ResearchSessionStatus, str]:
        if questions:
            return (
                "awaiting_clarification",
                "Pulsate needs a scientist reply and will resume this same "
                "session afterward.",
            )
        if execution is None:
            return "failed", "The session could not be compiled safely."
        if unapproved_inputs:
            return (
                "awaiting_approval",
                "The research plan is ready, but acquired or generated input "
                "evidence still requires explicit scientist approval.",
            )
        if execution.status == "authorization_required":
            return (
                "awaiting_approval",
                "The research plan is ready at an explicit authorization boundary.",
            )
        return "planned", execution.scientist_summary

    @staticmethod
    def _turn(
        *,
        session_identifier: str,
        position: int,
        role: ResearchTurnRole,
        content: str,
        created_at: datetime,
        requirement_identifier: str | None = None,
    ) -> ResearchConversationTurn:
        return ResearchConversationTurn(
            turn_identifier=(
                f"turn-{session_identifier.rsplit('-', 1)[-1]}-{position:04d}"
            ),
            role=role,
            content=content,
            created_at=created_at,
            requirement_identifier=requirement_identifier,
        )

    def _question_turns(
        self,
        session_identifier: str,
        questions: tuple[ClarificationPrompt, ...],
        start: int,
        created_at: datetime,
    ) -> tuple[ResearchConversationTurn, ...]:
        return tuple(
            self._turn(
                session_identifier=session_identifier,
                position=start + offset,
                role="pulsate",
                content=question.question,
                created_at=created_at,
                requirement_identifier=question.requirement_identifier,
            )
            for offset, question in enumerate(questions)
        )

    def _owned(
        self,
        session_identifier: str,
        tenant_identifier_sha256: str,
    ) -> ResearchSession:
        session = self.repository.get(session_identifier)
        if session.tenant_identifier_sha256 != tenant_identifier_sha256:
            raise KeyError("Research session not found.")
        return session

    @staticmethod
    def _next_time(value: datetime) -> datetime:
        now = datetime.now(UTC)
        return now if now > value else value + timedelta(microseconds=1)


__all__ = [
    "ResearchCompilation",
    "ResearchConversationTurn",
    "ResearchSession",
    "ResearchSessionController",
    "ResearchSessionCreateRequest",
    "ResearchSessionReplyRequest",
    "ResearchSessionRepository",
    "ResearchSessionStatus",
]
