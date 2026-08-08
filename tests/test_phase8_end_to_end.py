"""Native scientist-facing Phase 8 end-to-end acceptance tests."""

from __future__ import annotations

import hashlib
import json

import pytest

from cgr.electronic_structure import PySCFElectronicStructureAdapter
from cgr.kernel.contracts import CapabilityVersion
from cgr.molecular import RDKitCheminformaticsAdapter
from cgr.pulsate_api.phase8_scientific_handlers import (
    aqueous_conformer_registry,
    bond_dissociation_registry,
)
from cgr.pulsate_api.scientific_executions import (
    ScientificExecutionRepository,
    ScientificObjectiveCompileRequest,
)
from cgr.pulsate_api.scientific_objectives import ScientificInputReference
from cgr.pulsate_api.scientific_runtime import ScientificObjectiveRuntime
from cgr.science import ArtifactReference, CreationProvenance

_VERSION = CapabilityVersion(major=1, minor=0, patch=0)


class MemoryPayloadStore:
    def __init__(self) -> None:
        self.payloads: dict[str, bytes] = {}

    def read(self, reference: ArtifactReference) -> bytes:
        return self.payloads[reference.artifact_identifier]

    def write(self, reference: ArtifactReference, payload: bytes) -> None:
        value = bytes(payload)
        existing = self.payloads.get(reference.artifact_identifier)
        if existing is not None and existing != value:
            raise ValueError("Artifact identifier is already bound to other bytes.")
        self.payloads[reference.artifact_identifier] = value


def _input_artifact(
    store: MemoryPayloadStore,
    *,
    identifier: str,
    artifact_type: str,
    media_type: str,
    payload: bytes,
) -> ArtifactReference:
    reference = ArtifactReference(
        artifact_identifier=identifier,
        schema_version=_VERSION,
        artifact_type=artifact_type,
        media_type=media_type,
        content_sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        provenance=CreationProvenance(
            producer="test.scientist-input",
            producer_version=_VERSION,
        ),
    )
    store.write(reference, payload)
    return reference


def test_acceptance_4_aqueous_axial_equatorial_conformer_comparison(tmp_path) -> None:
    pytest.importorskip("rdkit")
    pytest.importorskip("pyscf")
    from rdkit import Chem
    from rdkit.Chem import AllChem

    # The scientist supplies only a molecular structure.  Production handlers
    # regenerate and classify the compared 3D states; no conformer indices are
    # present in the request or fixture metadata.
    molecule = Chem.AddHs(Chem.MolFromSmiles("OC1CCCCC1"))
    AllChem.Compute2DCoords(molecule)
    payload = Chem.MolToMolBlock(molecule).encode("utf-8")
    store = MemoryPayloadStore()
    source = _input_artifact(
        store,
        identifier="scientist-cyclohexanol-structure",
        artifact_type="molecular_structure",
        media_type="chemical/x-mdl-molfile",
        payload=payload,
    )

    repository = ScientificExecutionRepository(tmp_path / "executions")
    repository.start()
    record = repository.create(
        ScientificObjectiveCompileRequest(
            question="Which conformer is preferred in water: the axial or equatorial form?",
            input_references=(
                ScientificInputReference(
                    reference_identifier="cyclohexanol",
                    artifact_type="molecular_structure",
                    artifact_identifier=source.artifact_identifier,
                ),
            ),
            artifact_references=(source,),
        )
    )
    rdkit_adapter = RDKitCheminformaticsAdapter(store)
    pyscf_adapter = PySCFElectronicStructureAdapter(store)
    runtime = ScientificObjectiveRuntime(
        root=tmp_path / "workflow",
        execution_repository=repository,
        capability_registry=aqueous_conformer_registry(
            store=store,
            rdkit_adapter=rdkit_adapter,
            pyscf_adapter=pyscf_adapter,
        ),
    )
    runtime.start()

    completed = runtime.execute(record.execution_identifier)

    assert completed.status == "succeeded"
    assert completed.verified
    assert completed.scene_identifier is not None
    assert completed.scientist_result is not None
    assert completed.scientist_result.verification_status == "passed"
    assert "not a Gibbs free-energy comparison" in completed.scientist_result.scientific_result
    comparison_reference = next(
        item for item in completed.artifact_references
        if item.artifact_type == "electronic_conformer_comparison"
    )
    comparison = json.loads(store.read(comparison_reference))
    assert comparison["preferred_conformer"] in {"axial", "equatorial"}
    assert comparison["quantity"] == "solvated_electronic_energy"
    assert comparison["gibbs_free_energy"] is False
    assert len(
        [
            item for item in completed.artifact_references
            if item.artifact_type == "electronic_implicit_solvent_result"
        ]
    ) == 2
    assert all(
        forbidden not in record.objective.model_dump_json()
        for forbidden in (
            "atom_index",
            "orbital_index",
            "conformer_index",
            "workflow_graph",
        )
    )


def test_acceptance_3_fifteen_point_bond_dissociation_pes(tmp_path) -> None:
    pytest.importorskip("rdkit")
    pytest.importorskip("pyscf")
    from rdkit import Chem
    from rdkit.Chem import AllChem

    molecule = Chem.AddHs(Chem.MolFromSmiles("CC"))
    AllChem.Compute2DCoords(molecule)
    payload = Chem.MolToMolBlock(molecule).encode("utf-8")
    store = MemoryPayloadStore()
    source = _input_artifact(
        store,
        identifier="scientist-ethane-structure",
        artifact_type="molecular_structure",
        media_type="chemical/x-mdl-molfile",
        payload=payload,
    )
    repository = ScientificExecutionRepository(tmp_path / "executions")
    repository.start()
    record = repository.create(
        ScientificObjectiveCompileRequest(
            question=(
                "Calculate a 15-point potential-energy scan for this bond from "
                "1.2 Angstrom to 2.8 Angstrom and use an active space appropriate "
                "for bond breaking."
            ),
            input_references=(
                ScientificInputReference(
                    reference_identifier="ethane",
                    artifact_type="molecular_structure",
                    artifact_identifier=source.artifact_identifier,
                ),
            ),
            artifact_references=(source,),
        )
    )
    runtime = ScientificObjectiveRuntime(
        root=tmp_path / "workflow",
        execution_repository=repository,
        capability_registry=bond_dissociation_registry(
            store=store,
            rdkit_adapter=RDKitCheminformaticsAdapter(store),
            pyscf_adapter=PySCFElectronicStructureAdapter(store),
        ),
    )
    runtime.start()

    completed = runtime.execute(record.execution_identifier)

    assert completed.status == "succeeded", [
        (node.capability_name, node.status, node.error_code)
        for node in completed.node_executions
    ]
    assert completed.verified
    assert completed.scene_identifier is not None
    assert completed.scientist_result is not None
    curve_reference = next(
        item for item in completed.artifact_references
        if item.artifact_type == "potential_energy_curve"
    )
    curve = json.loads(store.read(curve_reference))
    assert curve["point_count"] == 15
    assert len(curve["points"]) == 15
    assert curve["points"][0]["requested_distance_angstrom"] == 1.2
    assert curve["points"][-1]["requested_distance_angstrom"] == 2.8
    assert curve["automatic_active_space"] is True
    assert all(
        point["distance_residual_angstrom"] <= 0.05 for point in curve["points"]
    )
    assert len(
        {
            point["active_space_selection_identifier"] for point in curve["points"]
        }
    ) == 15
    assert all(
        forbidden not in record.objective.model_dump_json()
        for forbidden in (
            "atom_index",
            "orbital_index",
            "active_orbital",
            "workflow_graph",
        )
    )
