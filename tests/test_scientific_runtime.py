"""Scientist objectives execute through persisted CGR graph runs."""

from __future__ import annotations

import hashlib

from cgr.kernel.contracts import CapabilityVersion
from cgr.pulsate_api.scientific_executions import (
    ScientificExecutionRepository,
    ScientificObjectiveCompileRequest,
)
from cgr.pulsate_api.scientific_objectives import ScientificInputReference
from cgr.pulsate_api.scientific_runtime import (
    ScientificCapabilityOutcome,
    ScientificObjectiveRuntime,
    ScientistCapabilityRegistry,
    scientific_engine_registry,
)
from cgr.science import ArtifactReference, CreationProvenance
from cgr.electronic_structure import PySCFElectronicStructureAdapter


class PayloadStore:
    def __init__(self) -> None:
        self.payloads = {}

    def read(self, reference):
        return self.payloads[reference.artifact_identifier]

    def write(self, reference, payload):
        self.payloads[reference.artifact_identifier] = bytes(payload)


class EvidenceHandler:
    def execute(self, *, invocation, objective, record):
        del objective, record
        payload = invocation.capability_identity.encode()
        reference = ArtifactReference(
            artifact_identifier=(
                "scientific-artifact-"
                + hashlib.sha256(
                    f"{invocation.graph_run_identifier}:{invocation.node_identifier}".encode()
                ).hexdigest()[:32]
            ),
            schema_version=CapabilityVersion(major=1, minor=0, patch=0),
            artifact_type="scientific_execution_evidence",
            media_type="application/json",
            content_sha256=hashlib.sha256(payload).hexdigest(),
            byte_size=len(payload),
            provenance=CreationProvenance(
                producer=invocation.capability_identity,
                producer_version=CapabilityVersion(major=1, minor=0, patch=0),
            ),
        )
        verification = invocation.capability_identity.startswith("scientific_verification.")
        return ScientificCapabilityOutcome(
            output_artifacts=(reference,),
            evidence_artifacts=((reference,) if verification else ()),
            scientific_summary=(
                "The bounded scientific calculation completed with verified evidence."
                if verification else None
            ),
            verified=(True if verification else None),
            scene_identifier=(
                "scientific-scene-runtime" if invocation.capability_identity == "molecular.scene_project" else None
            ),
        )


def test_native_adapter_declarations_build_exact_scientist_registry() -> None:
    adapter = PySCFElectronicStructureAdapter(PayloadStore())
    registry = scientific_engine_registry((adapter,))

    assert set(registry.identities()) == {
        envelope.descriptor.capability_name
        for envelope in adapter.declaration.capabilities
    }
    assert registry.get("electronic.implicit_solvent_hartree_fock") is not None


def test_runtime_executes_composed_plan_through_persisted_cgr_graph(tmp_path) -> None:
    repository = ScientificExecutionRepository(tmp_path / "executions")
    repository.start()
    record = repository.create(ScientificObjectiveCompileRequest(
        question="Which conformer is preferred in water: the axial or equatorial form?",
        input_references=(ScientificInputReference(
            reference_identifier="molecule-project",
            artifact_type="molecular_structure",
            artifact_identifier="molecule-artifact",
        ),),
        artifact_references=(ArtifactReference(
            artifact_identifier="molecule-artifact",
            schema_version=CapabilityVersion(major=1, minor=0, patch=0),
            artifact_type="molecular_structure",
            media_type="chemical/x-mdl-molfile",
            content_sha256="a" * 64,
            provenance=CreationProvenance(
                producer="test.fixture",
                producer_version=CapabilityVersion(major=1, minor=0, patch=0),
            ),
        ),),
    ))
    handler = EvidenceHandler()
    registry = ScientistCapabilityRegistry({
        step.capability_name: handler
        for step in record.plan.steps
        if step.capability_name != "scientist.result_assemble"
    })
    runtime = ScientificObjectiveRuntime(
        root=tmp_path / "workflow",
        execution_repository=repository,
        capability_registry=registry,
    )
    runtime.start()

    completed = runtime.execute(record.execution_identifier)
    reloaded = repository.get(record.execution_identifier)

    assert completed == reloaded
    assert completed.status == "succeeded"
    assert completed.verified
    assert completed.scientist_result is not None
    assert completed.scientist_result.verification_status == "passed"
    assert completed.workflow_graph_identifier is not None
    assert completed.workflow_snapshot_fingerprint is not None
    assert all(node.status == "succeeded" for node in completed.node_executions)
    assert completed.scene_identifier == "scientific-scene-runtime"
    assert completed.evidence_artifact_identifiers
