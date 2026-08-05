"""Phase 5 scientific-engine integration with the Phase 4 workflow runtime."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cgr.kernel.contracts import ExecutionStatus, HealthStatus
from cgr.science.artifacts import ArtifactPointer, ArtifactReference
from cgr.science.capabilities import (
    CapabilityExecutionEnvelope,
    ScientificCapabilityCatalog,
)
from cgr.science.contracts import (
    CapabilityInvocation as ScientificCapabilityInvocation,
)
from cgr.science.contracts import CapabilityResult
from cgr.science.engine_adapters import ScientificEngineAdapter

from .capability_adapter import (
    CapabilityAdapterRegistry,
    CapabilityInvocation,
    CapabilityInvocationResult,
    CapabilityInvocationStatus,
)


@runtime_checkable
class ArtifactReferenceResolver(Protocol):
    """Resolve compact workflow pointers into complete scientific references."""

    def resolve(self, pointer: ArtifactPointer) -> ArtifactReference:
        """Return the exact complete reference matching the supplied pointer."""
        ...


@runtime_checkable
class ScientificInvocationBuilder(Protocol):
    """Build a scientific invocation from one workflow invocation."""

    def build(
        self,
        invocation: CapabilityInvocation,
        envelope: CapabilityExecutionEnvelope,
        input_artifacts: tuple[ArtifactReference, ...],
    ) -> ScientificCapabilityInvocation:
        """Return a complete invocation using generic Pulsate contracts."""
        ...


class ScientificEngineRegistrationError(ValueError):
    """Controlled failure while registering one scientific-engine adapter."""


def _artifact_identities(
    artifacts: tuple[ArtifactReference, ...],
) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (
                artifact.artifact_identifier,
                artifact.content_sha256,
            )
            for artifact in artifacts
        )
    )


class ScientificWorkflowAdapter:
    """Execute one declared scientific capability through the workflow boundary."""

    def __init__(
        self,
        *,
        adapter: ScientificEngineAdapter,
        envelope: CapabilityExecutionEnvelope,
        artifact_resolver: ArtifactReferenceResolver,
        invocation_builder: ScientificInvocationBuilder,
    ) -> None:
        declaration = adapter.declaration
        if envelope not in declaration.capabilities:
            raise ScientificEngineRegistrationError(
                "The workflow capability is not declared by the scientific adapter."
            )

        self._adapter = adapter
        self._envelope = envelope
        self._artifact_resolver = artifact_resolver
        self._invocation_builder = invocation_builder
        self._declaration_fingerprint = declaration.fingerprint

    @property
    def capability_identity(self) -> str:
        """Return the Phase 4 string identity for this exact declaration."""

        return self._envelope.descriptor.capability_name

    def invoke(self, invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        """Resolve, validate, execute and translate one workflow invocation."""

        if invocation.capability_identity != self.capability_identity:
            return self._controlled_result(
                invocation,
                status=CapabilityInvocationStatus.BLOCKED,
                error_code="capability_identity_mismatch",
                error_message=(
                    "The workflow invocation does not match the registered capability."
                ),
            )

        if invocation.cancellation_requested:
            return self._controlled_result(
                invocation,
                status=CapabilityInvocationStatus.CANCELLED,
                error_code="capability_cancelled",
                error_message="The capability invocation was cancelled before execution.",
            )

        try:
            declaration = self._adapter.declaration
        except Exception:
            return self._controlled_result(
                invocation,
                status=CapabilityInvocationStatus.BLOCKED,
                error_code="adapter_declaration_unavailable",
                error_message="The scientific adapter declaration is unavailable.",
            )

        if declaration.fingerprint != self._declaration_fingerprint:
            return self._controlled_result(
                invocation,
                status=CapabilityInvocationStatus.BLOCKED,
                error_code="adapter_declaration_changed",
                error_message=(
                    "The scientific adapter declaration changed after registration."
                ),
            )

        try:
            health = self._adapter.health()
        except Exception:
            return self._controlled_result(
                invocation,
                status=CapabilityInvocationStatus.BLOCKED,
                error_code="adapter_health_unavailable",
                error_message="The scientific adapter health could not be established.",
            )

        if health.status is HealthStatus.UNAVAILABLE:
            return self._controlled_result(
                invocation,
                status=CapabilityInvocationStatus.BLOCKED,
                error_code=health.reason_code or "adapter_unavailable",
                error_message=health.message
                or "The scientific adapter is unavailable.",
            )

        resolved: list[ArtifactReference] = []
        for pointer in invocation.input_artifacts:
            try:
                reference = self._artifact_resolver.resolve(pointer)
            except Exception:
                return self._controlled_result(
                    invocation,
                    status=CapabilityInvocationStatus.BLOCKED,
                    error_code="artifact_reference_unavailable",
                    error_message=(
                        "A complete scientific input artifact reference is unavailable."
                    ),
                )

            if not isinstance(reference, ArtifactReference):
                return self._controlled_result(
                    invocation,
                    status=CapabilityInvocationStatus.BLOCKED,
                    error_code="artifact_reference_invalid",
                    error_message=(
                        "The artifact resolver returned an invalid scientific reference."
                    ),
                )

            if reference.pointer != pointer:
                return self._controlled_result(
                    invocation,
                    status=CapabilityInvocationStatus.BLOCKED,
                    error_code="artifact_reference_mismatch",
                    error_message=(
                        "The resolved artifact reference does not match its workflow pointer."
                    ),
                )

            resolved.append(reference)

        input_artifacts = tuple(resolved)

        try:
            scientific_invocation = self._invocation_builder.build(
                invocation,
                self._envelope,
                input_artifacts,
            )
        except Exception:
            return self._controlled_result(
                invocation,
                status=CapabilityInvocationStatus.BLOCKED,
                error_code="scientific_invocation_invalid",
                error_message=(
                    "A valid scientific capability invocation could not be constructed."
                ),
            )

        if not isinstance(scientific_invocation, ScientificCapabilityInvocation):
            return self._controlled_result(
                invocation,
                status=CapabilityInvocationStatus.BLOCKED,
                error_code="scientific_invocation_invalid",
                error_message=(
                    "The invocation builder returned an invalid scientific invocation."
                ),
            )

        if scientific_invocation.capability != self._envelope.descriptor:
            return self._controlled_result(
                invocation,
                status=CapabilityInvocationStatus.BLOCKED,
                error_code="scientific_capability_mismatch",
                error_message=(
                    "The scientific invocation does not match the declared capability."
                ),
            )

        if _artifact_identities(
            scientific_invocation.input_artifacts
        ) != _artifact_identities(input_artifacts):
            return self._controlled_result(
                invocation,
                status=CapabilityInvocationStatus.BLOCKED,
                error_code="scientific_input_mismatch",
                error_message=(
                    "The scientific invocation changed the resolved input artifacts."
                ),
            )

        if (
            scientific_invocation.context.execution_id
            != invocation.invocation_identifier
        ):
            return self._controlled_result(
                invocation,
                status=CapabilityInvocationStatus.BLOCKED,
                error_code="execution_identity_mismatch",
                error_message=(
                    "The scientific execution context does not preserve invocation identity."
                ),
            )

        try:
            scientific_result = self._adapter.invoke(scientific_invocation)
        except Exception:
            return self._controlled_result(
                invocation,
                status=CapabilityInvocationStatus.FAILED,
                error_code="scientific_adapter_exception",
                error_message="The scientific adapter failed without a valid result.",
            )

        if not isinstance(scientific_result, CapabilityResult):
            return self._controlled_result(
                invocation,
                status=CapabilityInvocationStatus.FAILED,
                error_code="scientific_result_invalid",
                error_message="The scientific adapter returned an invalid result.",
            )

        produced_types = set(self._envelope.descriptor.produced_artifact_types)
        if produced_types and any(
            artifact.artifact_type not in produced_types
            for artifact in scientific_result.output_artifacts
        ):
            return self._controlled_result(
                invocation,
                status=CapabilityInvocationStatus.FAILED,
                error_code="scientific_output_type_mismatch",
                error_message=(
                    "The scientific adapter produced an undeclared artifact type."
                ),
            )

        metadata = {
            "adapter_identifier": declaration.adapter_identifier,
            "adapter_version": str(declaration.adapter_version),
            "engine_identifier": declaration.engine.engine_identifier,
            "engine_version": declaration.engine.engine_version,
            "adapter_health": health.status.value,
            "scientific_result_fingerprint": scientific_result.fingerprint,
            "lineage_edge_count": str(len(scientific_result.lineage)),
        }

        if scientific_result.execution_receipt is not None:
            metadata["execution_receipt_identifier"] = (
                scientific_result.execution_receipt.artifact_identifier
            )

        if scientific_result.status is ExecutionStatus.SUCCESS:
            return CapabilityInvocationResult(
                invocation_identifier=invocation.invocation_identifier,
                status=CapabilityInvocationStatus.SUCCEEDED,
                output_artifacts=scientific_result.output_artifacts,
                execution_metadata=metadata,
            )

        if scientific_result.status is ExecutionStatus.CANCELLED:
            return CapabilityInvocationResult(
                invocation_identifier=invocation.invocation_identifier,
                status=CapabilityInvocationStatus.CANCELLED,
                error_code="scientific_execution_cancelled",
                error_message="The scientific capability execution was cancelled.",
                execution_metadata=metadata,
            )

        if scientific_result.status in {
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMEOUT,
        }:
            failure = scientific_result.failure
            timeout = scientific_result.status is ExecutionStatus.TIMEOUT

            return CapabilityInvocationResult(
                invocation_identifier=invocation.invocation_identifier,
                status=CapabilityInvocationStatus.FAILED,
                retryable=failure.retryable if failure is not None else False,
                error_code=(
                    failure.code
                    if failure is not None
                    else (
                        "scientific_execution_timeout"
                        if timeout
                        else "scientific_execution_failed"
                    )
                ),
                error_message=(
                    failure.message
                    if failure is not None
                    else (
                        "The scientific capability execution timed out."
                        if timeout
                        else "The scientific capability execution failed."
                    )
                ),
                execution_metadata=metadata,
            )

        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=CapabilityInvocationStatus.FAILED,
            error_code="scientific_result_incomplete",
            error_message=(
                "The scientific adapter returned a non-terminal execution status."
            ),
            execution_metadata=metadata,
        )

    @staticmethod
    def _controlled_result(
        invocation: CapabilityInvocation,
        *,
        status: CapabilityInvocationStatus,
        error_code: str,
        error_message: str,
    ) -> CapabilityInvocationResult:
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=status,
            error_code=error_code,
            error_message=error_message,
        )


def register_scientific_engine_adapter(
    adapter: ScientificEngineAdapter,
    *,
    capability_catalogue: ScientificCapabilityCatalog,
    workflow_registry: CapabilityAdapterRegistry,
    artifact_resolver: ArtifactReferenceResolver,
    invocation_builder: ScientificInvocationBuilder,
) -> tuple[ScientificWorkflowAdapter, ...]:
    """Register one adapter with planner and workflow systems as one operation."""

    declaration = adapter.declaration
    capability_names = tuple(
        envelope.descriptor.capability_name for envelope in declaration.capabilities
    )

    if len(capability_names) != len(set(capability_names)):
        raise ScientificEngineRegistrationError(
            "Phase 4 cannot register multiple versions under one capability name."
        )

    bridges = tuple(
        ScientificWorkflowAdapter(
            adapter=adapter,
            envelope=envelope,
            artifact_resolver=artifact_resolver,
            invocation_builder=invocation_builder,
        )
        for envelope in declaration.capabilities
    )

    handlers = {bridge.capability_identity: bridge.invoke for bridge in bridges}

    try:
        workflow_registry.register_many(handlers)
    except Exception as error:
        raise ScientificEngineRegistrationError(
            "Scientific workflow capability registration failed."
        ) from error

    try:
        capability_catalogue.register_many(declaration.capabilities)
    except Exception as error:
        try:
            workflow_registry.unregister_many(tuple(handlers))
        except Exception as rollback_error:
            raise ScientificEngineRegistrationError(
                "Scientific adapter registration failed and rollback was incomplete."
            ) from rollback_error

        raise ScientificEngineRegistrationError(
            "Scientific capability catalogue registration failed."
        ) from error

    return bridges
