"""Pure domain-neutral scientific workflow contracts and transitions."""

from __future__ import annotations

from enum import Enum
from typing import Self

from pydantic import field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion

from .artifacts import ArtifactReference
from .canonical import CanonicalModel, validate_identifier
from .verification import ScientificVerificationOutcome, ScientificVerificationResult


class WorkflowTerminalStatus(str, Enum):
    """Lifecycle state of a scientific workflow."""

    ACTIVE = "active"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class WorkflowVerificationRequirement(CanonicalModel):
    """Verifier identity and acceptable outcomes required by a phase."""

    verifier_identifier: str
    acceptable_outcomes: tuple[ScientificVerificationOutcome, ...] = (
        ScientificVerificationOutcome.PASSED,
    )

    @field_validator("verifier_identifier")
    @classmethod
    def validate_verifier_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="verifier identifier")

    @field_validator("acceptable_outcomes")
    @classmethod
    def order_outcomes(
        cls, value: tuple[ScientificVerificationOutcome, ...]
    ) -> tuple[ScientificVerificationOutcome, ...]:
        if not value:
            raise ValueError("Workflow verification outcomes cannot be empty.")
        return tuple(sorted(set(value), key=lambda outcome: outcome.value))


class WorkflowPhase(CanonicalModel):
    """One phase and the evidence required to enter it."""

    phase_identifier: str
    required_artifact_types: tuple[str, ...] = ()
    required_verifications: tuple[WorkflowVerificationRequirement, ...] = ()

    @field_validator("phase_identifier")
    @classmethod
    def validate_phase_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="phase identifier")

    @field_validator("required_artifact_types")
    @classmethod
    def order_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(validate_identifier(item) for item in value)))

    @field_validator("required_verifications")
    @classmethod
    def order_verification_requirements(
        cls, value: tuple[WorkflowVerificationRequirement, ...]
    ) -> tuple[WorkflowVerificationRequirement, ...]:
        identifiers = [item.verifier_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Workflow verifier requirements must be unique per phase.")
        return tuple(sorted(value, key=lambda item: item.verifier_identifier))


class WorkflowTransition(CanonicalModel):
    """One allowed directed phase transition."""

    source_phase: str
    destination_phase: str

    @field_validator("source_phase", "destination_phase")
    @classmethod
    def validate_phase_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="phase identifier")

    @model_validator(mode="after")
    def reject_self_transition(self) -> Self:
        if self.source_phase == self.destination_phase:
            raise ValueError("Workflow transitions must advance to another phase.")
        return self


class WorkflowDefinition(CanonicalModel):
    """Versioned workflow graph with explicit entry and terminal phases."""

    workflow_identifier: str
    schema_version: CapabilityVersion
    phases: tuple[WorkflowPhase, ...]
    transitions: tuple[WorkflowTransition, ...]
    entry_phase: str
    terminal_phases: tuple[str, ...]

    @field_validator("workflow_identifier", "entry_phase")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("terminal_phases")
    @classmethod
    def order_terminal_phases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(validate_identifier(item) for item in value)))

    @field_validator("phases")
    @classmethod
    def order_phases(cls, value: tuple[WorkflowPhase, ...]) -> tuple[WorkflowPhase, ...]:
        return tuple(sorted(value, key=lambda phase: phase.phase_identifier))

    @field_validator("transitions")
    @classmethod
    def order_transitions(
        cls, value: tuple[WorkflowTransition, ...]
    ) -> tuple[WorkflowTransition, ...]:
        return tuple(
            sorted(value, key=lambda item: (item.source_phase, item.destination_phase))
        )

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        phase_ids = [phase.phase_identifier for phase in self.phases]
        if not phase_ids:
            raise ValueError("A workflow requires at least one phase.")
        if len(phase_ids) != len(set(phase_ids)):
            raise ValueError("Workflow phase identifiers must be unique.")
        known = set(phase_ids)
        if self.entry_phase not in known:
            raise ValueError("The workflow entry phase is unknown.")
        if not self.terminal_phases or not set(self.terminal_phases).issubset(known):
            raise ValueError("Workflow terminal phases must be known and nonempty.")
        pairs = [
            (transition.source_phase, transition.destination_phase)
            for transition in self.transitions
        ]
        if len(pairs) != len(set(pairs)):
            raise ValueError("Workflow transitions must be unique.")
        if any(source not in known or destination not in known for source, destination in pairs):
            raise ValueError("Workflow transitions must reference known phases.")
        return self

    def phase(self, phase_identifier: str) -> WorkflowPhase:
        """Return a declared phase or reject an unknown identifier."""
        for phase in self.phases:
            if phase.phase_identifier == phase_identifier:
                return phase
        raise ValueError(f"Unknown workflow phase '{phase_identifier}'.")


class WorkflowTransitionRecord(CanonicalModel):
    """Stable evidence that one declared transition occurred."""

    source_phase: str
    destination_phase: str
    artifact_fingerprints: tuple[str, ...] = ()
    verification_fingerprints: tuple[str, ...] = ()

    @field_validator("source_phase", "destination_phase")
    @classmethod
    def validate_phase_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="phase identifier")

    @field_validator("artifact_fingerprints", "verification_fingerprints")
    @classmethod
    def order_fingerprints(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))


class WorkflowState(CanonicalModel):
    """Immutable current evidence and authorization state of a workflow."""

    current_phase: str
    completed_phases: tuple[str, ...] = ()
    produced_artifacts: tuple[ArtifactReference, ...] = ()
    verification_results: tuple[ScientificVerificationResult, ...] = ()
    transition_history: tuple[WorkflowTransitionRecord, ...] = ()
    authorization_status: bool = False
    terminal_status: WorkflowTerminalStatus = WorkflowTerminalStatus.ACTIVE

    @field_validator("current_phase")
    @classmethod
    def validate_current_phase(cls, value: str) -> str:
        return validate_identifier(value, label="phase identifier")

    @field_validator("completed_phases")
    @classmethod
    def unique_completed_phases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Completed workflow phases must be unique.")
        return value

    @field_validator("produced_artifacts")
    @classmethod
    def order_artifacts(
        cls, value: tuple[ArtifactReference, ...]
    ) -> tuple[ArtifactReference, ...]:
        return tuple(sorted(value, key=lambda artifact: artifact.artifact_identifier))

    @field_validator("verification_results")
    @classmethod
    def order_verifications(
        cls, value: tuple[ScientificVerificationResult, ...]
    ) -> tuple[ScientificVerificationResult, ...]:
        return tuple(sorted(value, key=lambda result: result.fingerprint))

    @model_validator(mode="after")
    def validate_authorization(self) -> Self:
        if self.authorization_status and self.terminal_status != WorkflowTerminalStatus.COMPLETED:
            raise ValueError("Only completed workflows may be authorized.")
        if self.authorization_status and any(
            result.has_blocking_failure for result in self.verification_results
        ):
            raise ValueError("Blocking verification failures prohibit workflow authorization.")
        return self


class TransitionValidation(CanonicalModel):
    """Deterministic result of validating a requested workflow transition."""

    allowed: bool
    failures: tuple[str, ...] = ()


def validate_workflow_transition(
    definition: WorkflowDefinition,
    state: WorkflowState,
    destination_phase: str,
) -> TransitionValidation:
    """Validate one transition without mutating workflow state."""
    failures: list[str] = []
    known = {phase.phase_identifier for phase in definition.phases}
    if state.current_phase not in known:
        failures.append("current_phase_unknown")
    if destination_phase not in known:
        failures.append("destination_phase_unknown")
        return TransitionValidation(allowed=False, failures=tuple(failures))
    if state.current_phase in definition.terminal_phases:
        failures.append("terminal_phase_cannot_advance")
    if (state.current_phase, destination_phase) not in {
        (transition.source_phase, transition.destination_phase)
        for transition in definition.transitions
    }:
        failures.append("illegal_transition")

    destination = definition.phase(destination_phase)
    artifact_types = {artifact.artifact_type for artifact in state.produced_artifacts}
    for artifact_type in destination.required_artifact_types:
        if artifact_type not in artifact_types:
            failures.append(f"required_artifact_missing:{artifact_type}")

    for requirement in destination.required_verifications:
        if not any(
            result.verifier_identifier == requirement.verifier_identifier
            and result.outcome in requirement.acceptable_outcomes
            for result in state.verification_results
        ):
            failures.append(
                f"required_verification_missing:{requirement.verifier_identifier}"
            )

    if destination_phase in definition.terminal_phases and any(
        result.has_blocking_failure for result in state.verification_results
    ):
        failures.append("blocking_verification_failed")
    return TransitionValidation(
        allowed=not failures,
        failures=tuple(dict.fromkeys(failures)),
    )


def transition_workflow(
    definition: WorkflowDefinition,
    state: WorkflowState,
    destination_phase: str,
) -> WorkflowState:
    """Return the next immutable workflow state after validated advancement."""
    validation = validate_workflow_transition(definition, state, destination_phase)
    if not validation.allowed:
        raise ValueError("Workflow transition rejected: " + ", ".join(validation.failures))
    terminal = destination_phase in definition.terminal_phases
    record = WorkflowTransitionRecord(
        source_phase=state.current_phase,
        destination_phase=destination_phase,
        artifact_fingerprints=tuple(
            artifact.fingerprint for artifact in state.produced_artifacts
        ),
        verification_fingerprints=tuple(
            result.fingerprint for result in state.verification_results
        ),
    )
    completed = (
        state.completed_phases
        if state.current_phase in state.completed_phases
        else (*state.completed_phases, state.current_phase)
    )
    return WorkflowState(
        current_phase=destination_phase,
        completed_phases=completed,
        produced_artifacts=state.produced_artifacts,
        verification_results=state.verification_results,
        transition_history=(*state.transition_history, record),
        authorization_status=terminal,
        terminal_status=(
            WorkflowTerminalStatus.COMPLETED
            if terminal
            else WorkflowTerminalStatus.ACTIVE
        ),
    )
