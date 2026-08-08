"""Phase 5.2 RDKit registration and workflow execution regression."""

from __future__ import annotations

import hashlib

import pytest

from cgr.kernel.contracts import CapabilityVersion, ExecutionContext
from cgr.molecular.rdkit_adapter import (
    SMILES_PARSE,
    MolecularArtifactPayloadStore,
    RDKitCheminformaticsAdapter,
)
from cgr.science import (
    ArtifactPointer,
    ArtifactReference,
    CapabilityExecutionEnvelope,
    CapabilityInvocation as ScientificCapabilityInvocation,
    ScientificCapabilityCatalog,
)
from cgr.workflow_graph.capability_adapter import (
    CapabilityAdapterRegistry,
    CapabilityInvocation,
    CapabilityInvocationStatus,
)
from cgr.workflow_graph.contracts import NodeKind
from cgr.workflow_graph.scientific_engine_adapter import (
    register_scientific_engine_adapter,
)

VERSION = CapabilityVersion(major=1, minor=0, patch=0)
EXPERIMENT = ArtifactPointer(
    artifact_identifier="experiment.phase5-2-workflow",
    content_sha256="e" * 64,
)


class MemoryPayloadStore:
    def __init__(self) -> None:
        self.payloads: dict[tuple[str, str], bytes] = {}

    def read(self, reference: ArtifactReference) -> bytes:
        return self.payloads[(reference.artifact_identifier, reference.content_sha256)]

    def write(self, reference: ArtifactReference, payload: bytes) -> None:
        assert hashlib.sha256(payload).hexdigest() == reference.content_sha256
        self.payloads[(reference.artifact_identifier, reference.content_sha256)] = (
            payload
        )


class RejectingResolver:
    def resolve(self, pointer: ArtifactPointer) -> ArtifactReference:
        del pointer
        raise AssertionError("SMILES parsing must not resolve an input artifact.")


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
                metadata={"graph_run_identifier": invocation.graph_run_identifier},
            ),
            parameters=dict(invocation.parameters),
        )


def test_rdkit_adapter_is_discoverable_and_executes_through_phase4() -> None:
    pytest.importorskip("rdkit")
    payload_store: MolecularArtifactPayloadStore = MemoryPayloadStore()
    adapter = RDKitCheminformaticsAdapter(payload_store)
    catalogue = ScientificCapabilityCatalog()
    registry = CapabilityAdapterRegistry()

    bridges = register_scientific_engine_adapter(
        adapter,
        capability_catalogue=catalogue,
        workflow_registry=registry,
        artifact_resolver=RejectingResolver(),
        invocation_builder=InvocationBuilder(),
    )

    assert len(bridges) == len(adapter.declaration.capabilities) == 11
    assert SMILES_PARSE in registry.identities()
    discovered = catalogue.find(
        objective_type="smiles_parsing",
        execution_target="local_cpu",
    )
    assert len(discovered) == 1
    assert discovered[0].descriptor.capability_name == SMILES_PARSE

    result = registry.invoke(
        CapabilityInvocation(
            invocation_identifier="invocation.phase5-2-workflow",
            idempotency_identifier="idempotency.phase5-2-workflow",
            graph_run_identifier="run.phase5-2-workflow",
            graph_definition_fingerprint="f" * 64,
            node_identifier="node.phase5-2-workflow",
            attempt_number=1,
            capability_identity=SMILES_PARSE,
            node_kind=tuple(NodeKind)[0],
            parameters={"smiles": "CCO"},
            correlation_identifier="correlation.phase5-2-workflow",
        )
    )

    assert result.status is CapabilityInvocationStatus.SUCCEEDED
    assert result.output_artifacts[0].artifact_type == "molecular_graph"
    assert result.execution_metadata["engine_identifier"] == "engine.rdkit"
    assert result.execution_metadata["adapter_identifier"] == (
        "adapter.rdkit.cheminformatics"
    )
