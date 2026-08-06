"""Phase 4 PySCF discovery through the existing workflow bridge."""

from __future__ import annotations

import hashlib

import pytest

from cgr.electronic_structure import (
    MOLECULE_CONSTRUCT,
    ElectronicArtifactPayloadStore,
    PySCFElectronicStructureAdapter,
)
from cgr.kernel.contracts import CapabilityVersion, ExecutionContext
from cgr.molecular import (
    MolecularConformer,
    MolecularConformerSet,
    MolecularCoordinate,
    MolecularGraph,
    MolecularGraphAtom,
    MolecularGraphBond,
)
from cgr.science import (
    ArtifactPointer,
    ArtifactReference,
    CapabilityExecutionEnvelope,
    CapabilityInvocation as ScientificCapabilityInvocation,
    CreationProvenance,
    ScientificCapabilityCatalog,
)
from cgr.workflow_graph import (
    CapabilityAdapterRegistry,
    CapabilityInvocation,
    CapabilityInvocationStatus,
    NodeKind,
    register_scientific_engine_adapter,
)

VERSION = CapabilityVersion(major=1, minor=0, patch=0)
EXPERIMENT = ArtifactPointer(
    artifact_identifier="experiment.phase4-workflow",
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


class Resolver:
    def __init__(self, reference: ArtifactReference) -> None:
        self.reference = reference

    def resolve(self, pointer: ArtifactPointer) -> ArtifactReference:
        assert pointer == self.reference.pointer
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
                metadata={"graph_run_identifier": invocation.graph_run_identifier},
            ),
            parameters=dict(invocation.parameters),
        )


def _source(store: MemoryPayloadStore) -> ArtifactReference:
    conformer_set = MolecularConformerSet(
        schema_version=VERSION,
        conformer_set_identifier="conformers.workflow-h2",
        source_graph=MolecularGraph(
            schema_version=VERSION,
            graph_identifier="graph.workflow-h2",
            representation_source="prepared_graph",
            canonical_smiles="[H][H]",
            mapped_smiles="[H:1][H:2]",
            atoms=(
                MolecularGraphAtom(
                    atom_index=0,
                    atomic_number=1,
                    element_symbol="H",
                    chiral_tag="unspecified",
                    atom_map_number=1,
                    source_atom_index=0,
                ),
                MolecularGraphAtom(
                    atom_index=1,
                    atomic_number=1,
                    element_symbol="H",
                    chiral_tag="unspecified",
                    atom_map_number=2,
                    source_atom_index=1,
                ),
            ),
            bonds=(
                MolecularGraphBond(
                    bond_index=0,
                    atom_index_a=0,
                    atom_index_b=1,
                    bond_order=1.0,
                    bond_type="single",
                    stereo="none",
                ),
            ),
            sanitized=True,
            explicit_hydrogens=True,
            formal_charge=0,
            component_count=1,
        ),
        generation_method="test_fixture",
        force_field="none",
        random_seed=0,
        requested_conformer_count=1,
        conformers=(
            MolecularConformer(
                conformer_identifier="conformer.workflow-h2",
                conformer_index=0,
                coordinates=(
                    MolecularCoordinate(atom_index=0, x=0.0, y=0.0, z=-0.37),
                    MolecularCoordinate(atom_index=1, x=0.0, y=0.0, z=0.37),
                ),
                optimization_status="not_requested",
            ),
        ),
    )
    payload = conformer_set.to_canonical_json().encode("utf-8")
    reference = ArtifactReference(
        artifact_identifier="fixture.workflow-h2",
        schema_version=VERSION,
        artifact_type="molecular_conformer_set",
        media_type="application/json",
        content_sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        provenance=CreationProvenance(
            producer="test.fixture",
            producer_version=VERSION,
        ),
    )
    store.write(reference, payload)
    return reference


def test_pyscf_adapter_is_discoverable_and_executes_through_workflow() -> None:
    pytest.importorskip("pyscf")

    payload_store: ElectronicArtifactPayloadStore = MemoryPayloadStore()
    source = _source(payload_store)
    adapter = PySCFElectronicStructureAdapter(payload_store)
    catalogue = ScientificCapabilityCatalog()
    registry = CapabilityAdapterRegistry()

    bridges = register_scientific_engine_adapter(
        adapter,
        capability_catalogue=catalogue,
        workflow_registry=registry,
        artifact_resolver=Resolver(source),
        invocation_builder=InvocationBuilder(),
    )

    assert len(bridges) == 7
    assert MOLECULE_CONSTRUCT in registry.identities()
    discovered = catalogue.find(
        objective_type="electronic_molecule_construction",
        execution_target="local_cpu",
    )
    assert len(discovered) == 1
    assert discovered[0].descriptor.capability_name == MOLECULE_CONSTRUCT

    result = registry.invoke(
        CapabilityInvocation(
            invocation_identifier="invocation.phase4-workflow",
            idempotency_identifier="idempotency.phase4-workflow",
            graph_run_identifier="run.phase4-workflow",
            graph_definition_fingerprint="f" * 64,
            node_identifier="node.phase4-workflow",
            attempt_number=1,
            capability_identity=MOLECULE_CONSTRUCT,
            node_kind=tuple(NodeKind)[0],
            input_artifacts=(source.pointer,),
            parameters={
                "molecular_charge": 0,
                "spin": 0,
                "conformer_index": 0,
            },
            correlation_identifier="correlation.phase4-workflow",
        )
    )

    assert result.status is CapabilityInvocationStatus.SUCCEEDED
    assert result.output_artifacts[0].artifact_type == "electronic_molecule"
    assert result.execution_metadata["engine_identifier"] == "engine.pyscf"
    assert result.execution_metadata["adapter_identifier"] == (
        "adapter.pyscf.electronic_structure"
    )
