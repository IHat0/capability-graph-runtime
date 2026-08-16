"""Major D general structure-analysis execution acceptance."""

from __future__ import annotations

import hashlib
import json

from cgr.kernel.contracts import CapabilityVersion
from cgr.pulsate_api.phase8_scientific_handlers import structure_analysis_registry
from cgr.pulsate_api.scientific_executions import (
    ScientificExecutionRepository,
    ScientificObjectiveCompileRequest,
)
from cgr.pulsate_api.scientific_objectives import ScientificInputReference
from cgr.pulsate_api.scientific_requirements import (
    ScientificRequirementProposal,
    validate_requirement_proposal,
)
from cgr.pulsate_api.scientific_runtime import ScientificObjectiveRuntime
from cgr.science import ArtifactReference, CreationProvenance


class MemoryPayloadStore:
    def __init__(self) -> None:
        self.payloads: dict[tuple[str, str], bytes] = {}

    def write(self, reference: ArtifactReference, payload: bytes) -> None:
        assert hashlib.sha256(payload).hexdigest() == reference.content_sha256
        self.payloads[
            (reference.artifact_identifier, reference.content_sha256)
        ] = payload

    def read(self, reference: ArtifactReference) -> bytes:
        return self.payloads[
            (reference.artifact_identifier, reference.content_sha256)
        ]


def _protein_reference(store: MemoryPayloadStore) -> ArtifactReference:
    payload = b"""ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N
ATOM      2  CA  ALA A   1       1.450   0.000   0.000  1.00  0.00           C
ATOM      3  C   ALA A   1       2.000   1.400   0.000  1.00  0.00           C
ATOM      4  O   ALA A   1       1.400   2.400   0.000  1.00  0.00           O
ATOM      5  N   GLY A   2       3.250   1.450   0.000  1.00  0.00           N
ATOM      6  CA  GLY A   2       3.900   2.700   0.000  1.00  0.00           C
ATOM      7  C   GLY A   2       5.400   2.500   0.000  1.00  0.00           C
ATOM      8  O   GLY A   2       6.000   3.500   0.000  1.00  0.00           O
TER
END
"""
    reference = ArtifactReference(
        artifact_identifier="scientist-structure-analysis-protein",
        schema_version=CapabilityVersion(major=1, minor=0, patch=0),
        artifact_type="protein_structure",
        media_type="chemical/x-pdb",
        content_sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        provenance=CreationProvenance(
            producer="test.fixture",
            producer_version=CapabilityVersion(major=1, minor=0, patch=0),
        ),
    )
    store.write(reference, payload)
    return reference


def _analysis_requirements():
    proposal = ScientificRequirementProposal(
        proposal_identifier="requirement-proposal-structure-analysis",
        requirements=(
            {
                "operation": "analyze_structure",
                "requested_output": "structure_analysis",
                "supporting_quote": "Analyze this structure",
            },
        ),
        summary="Analyze one supplied structure.",
        provider_kind="controlled_test_provider",
        model_name="replaceable-model",
    )
    return validate_requirement_proposal(
        proposal,
        available_input_types=("molecular_structure",),
    )


def _execute_analysis(tmp_path, store, source, input_type):
    repository = ScientificExecutionRepository(tmp_path / "executions")
    repository.start()
    record = repository.create(
        ScientificObjectiveCompileRequest(
            question="Analyze this structure.",
            input_references=(
                ScientificInputReference(
                    reference_identifier="analysis-structure",
                    artifact_type=input_type,
                    artifact_identifier=source.artifact_identifier,
                ),
            ),
            artifact_references=(source,),
            research_requirements=_analysis_requirements(),
        )
    )
    runtime = ScientificObjectiveRuntime(
        root=tmp_path / "workflow",
        execution_repository=repository,
        capability_registry=structure_analysis_registry(store=store),
    )
    runtime.start()
    return runtime.execute(record.execution_identifier)


def test_question_executes_verified_general_protein_structure_analysis(
    tmp_path,
) -> None:
    store = MemoryPayloadStore()
    source = _protein_reference(store)

    completed = _execute_analysis(
        tmp_path,
        store,
        source,
        "protein_structure",
    )

    assert completed.status == "succeeded"
    assert completed.verified
    assert completed.scientist_result is not None
    assert completed.scene_identifier is not None
    analysis_reference = next(
        item
        for item in completed.artifact_references
        if item.artifact_type == "molecular_structure_analysis"
    )
    analysis = json.loads(store.read(analysis_reference))
    canonical_structure = next(
        item
        for item in completed.artifact_references
        if item.artifact_type == "molecular_structure"
    )
    assert analysis["schema"] == "pulsate.structure-analysis/v1"
    assert analysis["analysis_kind"] == "pdb_coordinate_inventory"
    assert analysis["source_structure_artifact_identifier"] == (
        canonical_structure.artifact_identifier
    )
    assert analysis["atom_count"] == 8
    assert analysis["residue_count"] == 2
    assert analysis["chain_count"] == 1
    assert analysis["element_counts"] == {"C": 4, "N": 2, "O": 2}
    assert analysis["bounding_box_angstrom"]["x"] == {
        "maximum": 6.0,
        "minimum": 0.0,
    }
    verification = next(
        item
        for item in completed.artifact_references
        if item.artifact_type == "scientific_verification_report"
    )
    assert json.loads(store.read(verification))["passed"] is True


def test_question_executes_verified_general_molecule_structure_analysis(
    tmp_path,
) -> None:
    store = MemoryPayloadStore()
    payload = b"CCO"
    source = ArtifactReference(
        artifact_identifier="scientist-structure-analysis-molecule",
        schema_version=CapabilityVersion(major=1, minor=0, patch=0),
        artifact_type="ligand_structure",
        media_type="chemical/x-daylight-smiles",
        content_sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        provenance=CreationProvenance(
            producer="test.fixture",
            producer_version=CapabilityVersion(major=1, minor=0, patch=0),
        ),
    )
    store.write(source, payload)

    completed = _execute_analysis(
        tmp_path,
        store,
        source,
        "ligand_structure",
    )

    assert completed.status == "succeeded"
    assert completed.verified
    analysis_reference = next(
        item
        for item in completed.artifact_references
        if item.artifact_type == "molecular_structure_analysis"
    )
    analysis = json.loads(store.read(analysis_reference))
    assert analysis["analysis_kind"] == "molecular_graph_descriptors"
    assert analysis["canonical_smiles"] == "CCO"
    assert analysis["formula"] == "C2H6O"
    assert analysis["heavy_atom_count"] == 3
    assert analysis["formal_charge"] == 0
    assert analysis["molecular_weight_da"] > 46.0
