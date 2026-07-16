"""Scientific experiments and generic capability execution contracts."""

from __future__ import annotations

from enum import Enum
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import (
    CapabilityVersion,
    ExecutionContext,
    ExecutionStatus,
)

from .artifacts import (
    ArtifactLineageEdge,
    ArtifactPointer,
    ArtifactReference,
    CreationProvenance,
)
from .canonical import (
    BoundedMetadata,
    CanonicalModel,
    validate_bounded_metadata,
    validate_identifier,
)
from .verification import ScientificVerificationResult


class AssumptionSource(str, Enum):
    """Origin of a scientific assumption."""

    USER_PROVIDED = "user_provided"
    DERIVED = "derived"
    SYSTEM_DEFAULT = "system_default"
    UNRESOLVED = "unresolved"


class ApprovalStatus(str, Enum):
    """Explicit approval state for an assumption."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NOT_REQUIRED = "not_required"


class ScientificAssumption(CanonicalModel):
    """An explicit, attributable scientific assumption."""

    assumption_identifier: str
    description: str = Field(min_length=1, max_length=4096)
    source: AssumptionSource
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    supporting_artifact: ArtifactPointer | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    uncertainty: str | None = Field(default=None, max_length=2048)
    blocks_execution_until_approved: bool = False

    @field_validator("assumption_identifier")
    @classmethod
    def validate_assumption_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="assumption identifier")

    @model_validator(mode="after")
    def validate_approval(self) -> Self:
        if (
            self.source == AssumptionSource.UNRESOLVED
            and self.approval_status == ApprovalStatus.NOT_REQUIRED
        ):
            raise ValueError("Unresolved assumptions cannot bypass explicit resolution.")
        return self

    @property
    def blocks_execution(self) -> bool:
        return self.blocks_execution_until_approved and self.approval_status != ApprovalStatus.APPROVED

    def approve(self) -> ScientificAssumption:
        """Return an explicitly approved copy of this assumption."""
        return self.model_copy(update={"approval_status": ApprovalStatus.APPROVED})


class ExperimentExecutionPolicy(CanonicalModel):
    """Bounded policy controlling whether an experiment may execute."""

    execution_allowed: bool = False
    require_all_blocking_assumptions_approved: bool = True
    permitted_runtimes: tuple[str, ...] = ()
    parameters: BoundedMetadata = Field(default_factory=dict)

    @field_validator("permitted_runtimes")
    @classmethod
    def order_runtimes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(validate_identifier(item) for item in value))

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)


class ScientificExperiment(CanonicalModel):
    """Versioned immutable scientific experiment definition."""

    experiment_identifier: str
    schema_version: CapabilityVersion
    original_objective: str = Field(min_length=1, max_length=16_384)
    normalized_objective: str = Field(min_length=1, max_length=16_384)
    scientific_domain: str
    input_artifacts: tuple[ArtifactReference, ...] = ()
    assumptions: tuple[ScientificAssumption, ...] = ()
    constraints: tuple[str, ...] = ()
    requested_outputs: tuple[str, ...] = ()
    execution_policy: ExperimentExecutionPolicy
    provenance: CreationProvenance

    @field_validator("experiment_identifier", "scientific_domain")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("input_artifacts")
    @classmethod
    def order_inputs(
        cls, value: tuple[ArtifactReference, ...]
    ) -> tuple[ArtifactReference, ...]:
        identifiers = [item.artifact_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Experiment input artifact identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.artifact_identifier))

    @field_validator("assumptions")
    @classmethod
    def order_assumptions(
        cls, value: tuple[ScientificAssumption, ...]
    ) -> tuple[ScientificAssumption, ...]:
        identifiers = [item.assumption_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Assumption identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.assumption_identifier))

    @field_validator("constraints")
    @classmethod
    def order_constraints(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("Experiment declarations cannot be empty.")
        return tuple(sorted(set(value)))

    @field_validator("requested_outputs")
    @classmethod
    def order_requested_outputs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(validate_identifier(item) for item in value)))

    @property
    def blocking_assumptions(self) -> tuple[ScientificAssumption, ...]:
        return tuple(item for item in self.assumptions if item.blocks_execution)

    @property
    def execution_ready(self) -> bool:
        if not self.execution_policy.execution_allowed:
            return False
        if self.execution_policy.require_all_blocking_assumptions_approved:
            return not self.blocking_assumptions
        return True


class DeterminismClassification(str, Enum):
    """Whether equivalent invocations are expected to reproduce outputs."""

    DETERMINISTIC = "deterministic"
    NONDETERMINISTIC = "nondeterministic"


class CapabilityDescriptor(CanonicalModel):
    """Scientific extension of CGR's stable capability/version convention."""

    capability_name: str
    version: CapabilityVersion
    accepted_artifact_types: tuple[str, ...] = ()
    produced_artifact_types: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    required_runtime: str | None = None
    determinism: DeterminismClassification

    @field_validator("capability_name")
    @classmethod
    def validate_capability_name(cls, value: str) -> str:
        return validate_identifier(value, label="capability name")

    @field_validator(
        "accepted_artifact_types", "produced_artifact_types", "required_tools"
    )
    @classmethod
    def order_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(validate_identifier(item) for item in value)))

    @field_validator("required_runtime")
    @classmethod
    def validate_runtime(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None


class CapabilityInvocation(CanonicalModel):
    """One capability request over exact input artifact identities."""

    capability: CapabilityDescriptor
    input_artifacts: tuple[ArtifactReference, ...]
    experiment: ArtifactPointer
    context: ExecutionContext
    parameters: BoundedMetadata = Field(default_factory=dict)

    @field_validator("input_artifacts")
    @classmethod
    def order_inputs(
        cls, value: tuple[ArtifactReference, ...]
    ) -> tuple[ArtifactReference, ...]:
        identifiers = [item.artifact_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Capability input artifact identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.artifact_identifier))

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)

    @model_validator(mode="after")
    def validate_context_metadata(self) -> Self:
        validate_bounded_metadata(dict(self.context.metadata))
        accepted = set(self.capability.accepted_artifact_types)
        if accepted and any(
            artifact.artifact_type not in accepted for artifact in self.input_artifacts
        ):
            raise ValueError("Capability invocation contains an unsupported artifact type.")
        return self

    def canonical_identity(self) -> Any:
        """Exclude the operational context timestamp from scientific identity."""
        data = self.model_dump(mode="json")
        context = data["context"]
        context.pop("created_at", None)
        return data


class ExecutionEvidence(CanonicalModel):
    """Bounded machine evidence from capability execution."""

    runtime_identifier: str
    exit_code: int | None = None
    evidence_artifacts: tuple[ArtifactPointer, ...] = ()
    details: BoundedMetadata = Field(default_factory=dict)

    @field_validator("runtime_identifier")
    @classmethod
    def validate_runtime_identifier(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("details")
    @classmethod
    def validate_details(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)


class FailureInformation(CanonicalModel):
    """Structured bounded failure information."""

    code: str
    message: str = Field(min_length=1, max_length=4096)
    retryable: bool = False
    details: BoundedMetadata = Field(default_factory=dict)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("details")
    @classmethod
    def validate_details(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)


class CapabilityResult(CanonicalModel):
    """Generic scientific capability result and its authorization evidence."""

    status: ExecutionStatus
    output_artifacts: tuple[ArtifactReference, ...] = ()
    lineage: tuple[ArtifactLineageEdge, ...] = ()
    verification_results: tuple[ScientificVerificationResult, ...] = ()
    diagnostics: BoundedMetadata = Field(default_factory=dict)
    execution_evidence: ExecutionEvidence | None = None
    execution_receipt: ArtifactPointer | None = None
    failure: FailureInformation | None = None

    @field_validator("output_artifacts")
    @classmethod
    def order_outputs(
        cls, value: tuple[ArtifactReference, ...]
    ) -> tuple[ArtifactReference, ...]:
        identifiers = [item.artifact_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Capability output artifact identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.artifact_identifier))

    @field_validator("lineage")
    @classmethod
    def order_lineage(
        cls, value: tuple[ArtifactLineageEdge, ...]
    ) -> tuple[ArtifactLineageEdge, ...]:
        fingerprints = [edge.fingerprint for edge in value]
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("Capability result lineage edges must be unique.")
        return tuple(sorted(value, key=lambda edge: edge.fingerprint))

    @field_validator("verification_results")
    @classmethod
    def order_verification(
        cls, value: tuple[ScientificVerificationResult, ...]
    ) -> tuple[ScientificVerificationResult, ...]:
        return tuple(sorted(value, key=lambda result: result.fingerprint))

    @field_validator("diagnostics")
    @classmethod
    def validate_diagnostics(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status == ExecutionStatus.SUCCESS and self.failure is not None:
            raise ValueError("Successful capability results cannot contain failure information.")
        if self.status == ExecutionStatus.SUCCESS and any(
            result.has_blocking_failure for result in self.verification_results
        ):
            raise ValueError("Blocking verification failures cannot produce a successful result.")
        if self.status == ExecutionStatus.FAILED and self.failure is None:
            raise ValueError("Failed capability results require failure information.")
        return self
