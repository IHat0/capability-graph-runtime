"""Phase 5 scientific-engine workflow integration regressions."""

from __future__ import annotations

import pytest

from cgr.kernel.contracts import (
    CapabilityVersion,
    ExecutionContext,
    ExecutionStatus,
    HealthStatus,
)
from cgr.science.artifacts import (
    ArtifactPointer,
    ArtifactReference,
    CreationProvenance,
)
from cgr.science.capabilities import (
    CapabilityExecutionEnvelope,
    ScientificCapabilityCatalog,
)
from cgr.science.contracts import (
    CapabilityDescriptor,
    CapabilityInvocation as ScientificCapabilityInvocation,
    CapabilityResult,
    DeterminismClassification,
)
from cgr.science.engine_adapters import (
    ScientificEngineAdapterDeclaration,
    ScientificEngineHealthReport,
    ScientificEngineIdentity,
)
from cgr.workflow_graph.capability_adapter import (
    CapabilityAdapterRegistry,
    CapabilityInvocation,
    CapabilityInvocationResult,
    CapabilityInvocationStatus,
)
from cgr.workflow_graph.contracts import NodeKind
from cgr.workflow_graph.scientific_engine_adapter import (
    ScientificEngineRegistrationError,
    register_scientific_engine_adapter,
)


VERSION = CapabilityVersion(major=1, minor=0, patch=0)
EXPERIMENT = ArtifactPointer(
    artifact_identifier="experiment.fixture",
    content_sha256="e" * 64,
)


def _envelope(
    name: str = "science.fake",
    version: CapabilityVersion = VERSION,
) -> CapabilityExecutionEnvelope:
    return CapabilityExecutionEnvelope(
        descriptor=CapabilityDescriptor(
            capability_name=name,
            version=version,
            accepted_artifact_types=("molecular_structure",),
            produced_artifact_types=("scientific_result",),
            determinism=DeterminismClassification.DETERMINISTIC,
        ),
        execution_targets=("target.local",),
    )


def _reference(
    identifier: str,
    digest_character: str,
    artifact_type: str,
) -> ArtifactReference:
    return ArtifactReference(
        artifact_identifier=identifier,
        schema_version=VERSION,
        artifact_type=artifact_type,
        media_type="application/json",
        content_sha256=digest_character * 64,
        byte_size=2,
        provenance=CreationProvenance(
            producer="test.fixture",
            producer_version=VERSION,
        ),
    )


INPUT = _reference(
    "artifact.input",
    "a",
    "molecular_structure",
)
OUTPUT = _reference(
    "artifact.output",
    "b",
    "scientific_result",
)


class MappingResolver:
    def __init__(self, reference: ArtifactReference) -> None:
        self.reference = reference

    def resolve(self, pointer: ArtifactPointer) -> ArtifactReference:
        del pointer
        return self.reference


class InvocationBuilder:
    def build(
        self,
        invocation: CapabilityInvocation,
        envelope: CapabilityExecutionEnvelope,
        input_artifacts: tuple[ArtifactReference, ...],
    ) -> ScientificCapabilityInvocation:
        return ScientificCapabilityInvocation(
            capability=envelope.descriptor,
            input_artifacts=input_artifacts,
            experiment=EXPERIMENT,
            context=ExecutionContext(
                execution_id=invocation.invocation_identifier,
                correlation_id=invocation.correlation_identifier,
                metadata={
                    "graph_run_identifier": invocation.graph_run_identifier,
                },
            ),
            parameters=dict(invocation.parameters),
        )


class FakeAdapter:
    def __init__(
        self,
        declaration: ScientificEngineAdapterDeclaration,
        result: CapabilityResult,
        health: ScientificEngineHealthReport | None = None,
    ) -> None:
        self._declaration = declaration
        self._result = result
        self._health = health or ScientificEngineHealthReport(
            status=HealthStatus.HEALTHY
        )
        self.invocations: list[ScientificCapabilityInvocation] = []

    @property
    def declaration(self) -> ScientificEngineAdapterDeclaration:
        return self._declaration

    def health(self) -> ScientificEngineHealthReport:
        return self._health

    def invoke(
        self,
        invocation: ScientificCapabilityInvocation,
    ) -> CapabilityResult:
        self.invocations.append(invocation)
        return self._result


def _adapter(
    *,
    envelopes: tuple[CapabilityExecutionEnvelope, ...] | None = None,
    result: CapabilityResult | None = None,
    health: ScientificEngineHealthReport | None = None,
) -> FakeAdapter:
    declared = envelopes or (_envelope(),)
    declaration = ScientificEngineAdapterDeclaration(
        adapter_identifier="adapter.fake",
        adapter_version=VERSION,
        engine=ScientificEngineIdentity(
            engine_identifier="engine.fake",
            engine_version="1.0.0",
        ),
        capabilities=declared,
    )
    return FakeAdapter(
        declaration,
        result
        or CapabilityResult(
            status=ExecutionStatus.SUCCESS,
            output_artifacts=(OUTPUT,),
        ),
        health,
    )


def _workflow_invocation(
    capability_identity: str = "science.fake",
    pointer: ArtifactPointer = INPUT.pointer,
) -> CapabilityInvocation:
    return CapabilityInvocation(
        invocation_identifier="invocation.fixture",
        idempotency_identifier="idempotency.fixture",
        graph_run_identifier="run.fixture",
        graph_definition_fingerprint="f" * 64,
        node_identifier="node.fixture",
        attempt_number=1,
        capability_identity=capability_identity,
        node_kind=tuple(NodeKind)[0],
        input_artifacts=(pointer,),
        parameters={"seed": 7},
        correlation_identifier="correlation.fixture",
    )


def test_registration_connects_catalogue_and_workflow_execution() -> None:
    adapter = _adapter()
    catalogue = ScientificCapabilityCatalog()
    registry = CapabilityAdapterRegistry()

    bridges = register_scientific_engine_adapter(
        adapter,
        capability_catalogue=catalogue,
        workflow_registry=registry,
        artifact_resolver=MappingResolver(INPUT),
        invocation_builder=InvocationBuilder(),
    )

    assert len(bridges) == 1
    assert registry.identities() == ("science.fake",)
    assert catalogue.get("science.fake", VERSION) == _envelope()

    result = registry.invoke(_workflow_invocation())

    assert result.status is CapabilityInvocationStatus.SUCCEEDED
    assert result.output_artifacts == (OUTPUT,)
    assert result.execution_metadata["adapter_identifier"] == "adapter.fake"
    assert result.execution_metadata["engine_identifier"] == "engine.fake"
    assert result.execution_metadata["lineage_edge_count"] == "0"
    assert len(adapter.invocations) == 1
    assert adapter.invocations[0].input_artifacts == (INPUT,)
    assert adapter.invocations[0].context.execution_id == "invocation.fixture"


def test_unavailable_adapter_blocks_without_execution() -> None:
    adapter = _adapter(
        health=ScientificEngineHealthReport(
            status=HealthStatus.UNAVAILABLE,
            reason_code="dependency_missing",
            message="The scientific engine dependency is unavailable.",
        )
    )
    registry = CapabilityAdapterRegistry()

    register_scientific_engine_adapter(
        adapter,
        capability_catalogue=ScientificCapabilityCatalog(),
        workflow_registry=registry,
        artifact_resolver=MappingResolver(INPUT),
        invocation_builder=InvocationBuilder(),
    )

    result = registry.invoke(_workflow_invocation())

    assert result.status is CapabilityInvocationStatus.BLOCKED
    assert result.error_code == "dependency_missing"
    assert adapter.invocations == []


def test_artifact_pointer_mismatch_fails_closed() -> None:
    wrong_reference = _reference(
        "artifact.input",
        "c",
        "molecular_structure",
    )
    adapter = _adapter()
    registry = CapabilityAdapterRegistry()

    register_scientific_engine_adapter(
        adapter,
        capability_catalogue=ScientificCapabilityCatalog(),
        workflow_registry=registry,
        artifact_resolver=MappingResolver(wrong_reference),
        invocation_builder=InvocationBuilder(),
    )

    result = registry.invoke(_workflow_invocation())

    assert result.status is CapabilityInvocationStatus.BLOCKED
    assert result.error_code == "artifact_reference_mismatch"
    assert adapter.invocations == []


def test_undeclared_output_type_is_rejected() -> None:
    unexpected = _reference(
        "artifact.unexpected",
        "d",
        "unexpected_result",
    )
    adapter = _adapter(
        result=CapabilityResult(
            status=ExecutionStatus.SUCCESS,
            output_artifacts=(unexpected,),
        )
    )
    registry = CapabilityAdapterRegistry()

    register_scientific_engine_adapter(
        adapter,
        capability_catalogue=ScientificCapabilityCatalog(),
        workflow_registry=registry,
        artifact_resolver=MappingResolver(INPUT),
        invocation_builder=InvocationBuilder(),
    )

    result = registry.invoke(_workflow_invocation())

    assert result.status is CapabilityInvocationStatus.FAILED
    assert result.error_code == "scientific_output_type_mismatch"


def test_multiple_versions_of_one_workflow_name_are_rejected() -> None:
    adapter = _adapter(
        envelopes=(
            _envelope(version=CapabilityVersion(major=1, minor=0, patch=0)),
            _envelope(version=CapabilityVersion(major=2, minor=0, patch=0)),
        )
    )

    with pytest.raises(
        ScientificEngineRegistrationError,
        match="cannot register multiple versions",
    ):
        register_scientific_engine_adapter(
            adapter,
            capability_catalogue=ScientificCapabilityCatalog(),
            workflow_registry=CapabilityAdapterRegistry(),
            artifact_resolver=MappingResolver(INPUT),
            invocation_builder=InvocationBuilder(),
        )


def test_catalogue_failure_rolls_back_workflow_registration() -> None:
    envelope = _envelope()
    catalogue = ScientificCapabilityCatalog((envelope,))
    registry = CapabilityAdapterRegistry()

    with pytest.raises(
        ScientificEngineRegistrationError,
        match="catalogue registration failed",
    ):
        register_scientific_engine_adapter(
            _adapter(envelopes=(envelope,)),
            capability_catalogue=catalogue,
            workflow_registry=registry,
            artifact_resolver=MappingResolver(INPUT),
            invocation_builder=InvocationBuilder(),
        )

    assert registry.identities() == ()


def test_bulk_workflow_registration_is_atomic() -> None:
    def handler(
        invocation: CapabilityInvocation,
    ) -> CapabilityInvocationResult:
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=CapabilityInvocationStatus.SUCCEEDED,
        )

    registry = CapabilityAdapterRegistry({"capability.existing": handler})

    with pytest.raises(ValueError, match="already registered"):
        registry.register_many(
            {
                "capability.new": handler,
                "capability.existing": handler,
            }
        )

    assert registry.identities() == ("capability.existing",)
