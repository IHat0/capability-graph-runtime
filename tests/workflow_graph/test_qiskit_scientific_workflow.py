"""Phase 5 Qiskit discovery through the existing scientific workflow bridge."""

from __future__ import annotations

import hashlib

import pytest

from cgr.electronic_structure import ElectronicActiveSpace, ElectronicTensor
from cgr.kernel.contracts import CapabilityVersion, ExecutionContext, HealthStatus
from cgr.quantum_workflow import (
    ANSATZ_CONSTRUCT,
    HAMILTONIAN_CONSTRUCT,
    STATEVECTOR_SIMULATE,
    VQE_EXECUTE,
    QiskitQuantumWorkflowAdapter,
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
    artifact_identifier="experiment.phase5a-workflow",
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
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("pyscf")
    from pyscf import ao2mo, gto, scf

    molecule = gto.M(
        atom="H 0 0 0; H 0 0 0.74",
        basis="sto-3g",
        charge=0,
        spin=0,
        unit="Angstrom",
        verbose=0,
    )
    mean_field = scf.RHF(molecule)
    mean_field.conv_tol = 1e-12
    mean_field.kernel()
    assert mean_field.converged

    coefficients = numpy.asarray(mean_field.mo_coeff)
    one_body = coefficients.T @ mean_field.get_hcore() @ coefficients
    two_body = numpy.asarray(
        ao2mo.kernel(molecule, coefficients, compact=False)
    ).reshape((2, 2, 2, 2))
    active = ElectronicActiveSpace(
        schema_version=VERSION,
        active_space_identifier="active.workflow-h2",
        molecule_identifier="molecule.workflow-h2",
        hartree_fock_result_identifier="hf.workflow-h2",
        active_electron_count=2,
        alpha_electron_count=1,
        beta_electron_count=1,
        active_spatial_orbital_count=2,
        active_spin_orbital_count=4,
        active_orbital_indices=(0, 1),
        constant_energy_hartree=float(molecule.energy_nuc()),
        one_body_integrals=ElectronicTensor(
            shape=(2, 2),
            values=tuple(float(value) for value in one_body.reshape(-1)),
            unit="hartree",
            index_convention="active_pq",
        ),
        two_body_integrals=ElectronicTensor(
            shape=(2, 2, 2, 2),
            values=tuple(float(value) for value in two_body.reshape(-1)),
            unit="hartree",
            index_convention="chemist_active_pqrs",
        ),
    )
    payload = active.to_canonical_json().encode("utf-8")
    reference = ArtifactReference(
        artifact_identifier="fixture.workflow-active",
        schema_version=VERSION,
        artifact_type="electronic_active_space",
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


def test_qiskit_adapter_is_discoverable_and_executes_through_workflow() -> None:
    pytest.importorskip("qiskit")
    pytest.importorskip("qiskit_nature")
    pytest.importorskip("qiskit_algorithms")

    payload_store = MemoryPayloadStore()
    source = _source(payload_store)
    adapter = QiskitQuantumWorkflowAdapter(payload_store)
    assert adapter.health().status is HealthStatus.HEALTHY
    catalogue = ScientificCapabilityCatalog()
    registry = CapabilityAdapterRegistry()

    bridges = register_scientific_engine_adapter(
        adapter,
        capability_catalogue=catalogue,
        workflow_registry=registry,
        artifact_resolver=Resolver(source),
        invocation_builder=InvocationBuilder(),
    )

    assert len(bridges) == 6
    assert HAMILTONIAN_CONSTRUCT in registry.identities()
    assert ANSATZ_CONSTRUCT in registry.identities()
    assert VQE_EXECUTE in registry.identities()
    assert STATEVECTOR_SIMULATE in registry.identities()
    discovered = catalogue.find(
        objective_type="second_quantized_hamiltonian_construction",
        execution_target="local_cpu",
    )
    assert len(discovered) == 1
    assert discovered[0].descriptor.capability_name == HAMILTONIAN_CONSTRUCT

    variational = catalogue.find(
        objective_type="variational_ground_state",
        execution_target="local_cpu",
    )
    assert len(variational) == 1
    assert variational[0].descriptor.capability_name == VQE_EXECUTE

    result = registry.invoke(
        CapabilityInvocation(
            invocation_identifier="invocation.phase5a-workflow",
            idempotency_identifier="idempotency.phase5a-workflow",
            graph_run_identifier="run.phase5a-workflow",
            graph_definition_fingerprint="f" * 64,
            node_identifier="node.phase5a-workflow",
            attempt_number=1,
            capability_identity=HAMILTONIAN_CONSTRUCT,
            node_kind=tuple(NodeKind)[0],
            input_artifacts=(source.pointer,),
            parameters={"integral_symmetry_tolerance": 1e-10},
            correlation_identifier="correlation.phase5a-workflow",
        )
    )

    assert result.status is CapabilityInvocationStatus.SUCCEEDED
    assert result.output_artifacts[0].artifact_type == ("second_quantized_hamiltonian")
    assert result.execution_metadata["engine_identifier"] == ("engine.qiskit_nature")
    assert result.execution_metadata["adapter_identifier"] == (
        "adapter.qiskit_nature.quantum_workflow"
    )
