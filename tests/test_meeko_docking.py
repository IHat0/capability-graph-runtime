"""Native Meeko preparation completes the Vina input path."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from cgr.kernel.contracts import CapabilityVersion, ExecutionContext, ExecutionStatus, HealthStatus
from cgr.molecular import (
    CONFORMER_GENERATION,
    DOCKING_LIGAND_PREPARE,
    DOCKING_POSE_GENERATE,
    DOCKING_RECEPTOR_PREPARE,
    FORCE_FIELD_SELECT,
    PREPARE,
    PROTEIN_PROTONATION_PREPARE,
    SMILES_PARSE,
    MeekoDockingPreparationAdapter,
    OpenMMClassicalSimulationAdapter,
    RDKitCheminformaticsAdapter,
    VinaDockingAdapter,
)
from cgr.science import ArtifactPointer, ArtifactReference, CapabilityInvocation, CreationProvenance
from test_rdkit_cheminformatics import MemoryPayloadStore
from test_openmm_classical_simulation import MemoryPrivateStateStore

VERSION = CapabilityVersion(major=1, minor=0, patch=0)
EXPERIMENT = ArtifactPointer(
    artifact_identifier="experiment.phase8-meeko",
    content_sha256="e" * 64,
)
FIXTURES = Path(__file__).parent / "fixtures" / "molecular-ingestion"


def _invoke(adapter, capability_name, *, inputs=(), parameters=None, execution="execution.phase8-meeko"):
    descriptor = next(
        item.descriptor for item in adapter.declaration.capabilities
        if item.descriptor.capability_name == capability_name
    )
    return adapter.invoke(CapabilityInvocation(
        capability=descriptor,
        input_artifacts=inputs,
        experiment=EXPERIMENT,
        context=ExecutionContext(execution_id=execution),
        parameters=parameters or {},
    ))


def test_repeated_preparation_keeps_distinct_execution_provenance():
    store = MemoryPayloadStore()
    adapter = MeekoDockingPreparationAdapter(store)
    descriptor = adapter.declaration.capabilities[0].descriptor
    references = []
    for execution in ("execution.first", "execution.second"):
        invocation = CapabilityInvocation(
            capability=descriptor, experiment=EXPERIMENT,
            input_artifacts=(),
            context=ExecutionContext(execution_id=execution),
        )
        references.append(adapter._write(invocation, "docking_receptor_pdbqt", b"same bytes", ()))
    assert references[0].artifact_identifier != references[1].artifact_identifier
    assert references[0].content_sha256 == references[1].content_sha256
    assert references[0].provenance.execution_identifier != references[1].provenance.execution_identifier


def test_long_preparation_diagnostics_remain_controlled():
    result = MeekoDockingPreparationAdapter._failure("preparation_failed", "Invalid receptor",
                                                   details={"stderr_tail": "x" * 4000})
    assert result.status is ExecutionStatus.FAILED
    assert len(result.failure.details["stderr_tail"]) == 1024


def test_meeko_declaration_is_import_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "meeko", None)
    adapter = MeekoDockingPreparationAdapter(MemoryPayloadStore())

    assert adapter.health().status is HealthStatus.UNAVAILABLE
    assert {item.descriptor.capability_name for item in adapter.declaration.capabilities} == {
        "molecular.docking_ligand_prepare",
        "molecular.docking_receptor_prepare",
    }


def test_native_meeko_ligand_preparation_embeds_atom_mapping() -> None:
    pytest.importorskip("rdkit")
    pytest.importorskip("meeko")
    store = MemoryPayloadStore()
    rdkit = RDKitCheminformaticsAdapter(store)
    graph = _invoke(rdkit, SMILES_PARSE, parameters={"smiles": "CCO"}).output_artifacts[0]
    prepared = _invoke(rdkit, PREPARE, inputs=(graph,)).output_artifacts[0]
    conformer_result = _invoke(
        rdkit,
        CONFORMER_GENERATION,
        inputs=(prepared,),
        parameters={"conformer_count": 1, "random_seed": 17, "maximum_iterations": 300},
    )
    assert conformer_result.status is ExecutionStatus.SUCCESS
    adapter = MeekoDockingPreparationAdapter(store)

    result = _invoke(
        adapter,
        DOCKING_LIGAND_PREPARE,
        inputs=(conformer_result.output_artifacts[0],),
        parameters={"conformer_index": 0},
    )

    assert result.status is ExecutionStatus.SUCCESS
    payload = store.read(result.output_artifacts[0])
    assert hashlib.sha256(payload).hexdigest() == result.output_artifacts[0].content_sha256
    assert b"ROOT" in payload
    assert b"TORSDOF" in payload
    assert b"REMARK INDEX MAP" in payload


def test_native_meeko_to_vina_pipeline_generates_real_ranked_poses() -> None:
    pytest.importorskip("rdkit")
    pytest.importorskip("openmm")
    pytest.importorskip("meeko")
    pytest.importorskip("vina")
    store = MemoryPayloadStore()
    source_payload = (FIXTURES / "alanine3.pdb").read_bytes()
    protein = ArtifactReference(
        artifact_identifier="protein-alanine3-native-docking",
        schema_version=VERSION,
        artifact_type="molecular_structure",
        media_type="chemical/x-pdb",
        content_sha256=hashlib.sha256(source_payload).hexdigest(),
        byte_size=len(source_payload),
        provenance=CreationProvenance(producer="test.fixture", producer_version=VERSION),
    )
    store.write(protein, source_payload)
    openmm = OpenMMClassicalSimulationAdapter(store, MemoryPrivateStateStore())
    force_field = _invoke(
        openmm,
        FORCE_FIELD_SELECT,
        parameters={
            "force_field_files": "amber14/protein.ff14SB.xml;amber14/tip3p.xml",
            "water_model": "tip3p",
        },
    ).output_artifacts[0]
    prepared_result = _invoke(
        openmm,
        PROTEIN_PROTONATION_PREPARE,
        inputs=(protein, force_field),
        parameters={
            "structure_format": "pdb",
            "target_ph": 7.4,
            "residue_variant_overrides": "none",
        },
    )
    assert prepared_result.status is ExecutionStatus.SUCCESS
    prepared_protein = next(
        item for item in prepared_result.output_artifacts
        if item.artifact_type == "prepared_molecular_structure"
    )
    # Meeko consumes chemically prepared PDB bytes under the generic structure type.
    prepared_protein = ArtifactReference.model_validate({
        **prepared_protein.model_dump(mode="json"),
        "artifact_type": "molecular_structure",
    })
    store.write(prepared_protein, store.read(next(
        item for item in prepared_result.output_artifacts
        if item.artifact_type == "prepared_molecular_structure"
    )))
    rdkit = RDKitCheminformaticsAdapter(store)
    ligand_graph = _invoke(rdkit, SMILES_PARSE, parameters={"smiles": "CCO"}).output_artifacts[0]
    ligand_prepared = _invoke(rdkit, PREPARE, inputs=(ligand_graph,)).output_artifacts[0]
    ligand_conformers = _invoke(
        rdkit,
        CONFORMER_GENERATION,
        inputs=(ligand_prepared,),
        parameters={"conformer_count": 1, "random_seed": 23, "maximum_iterations": 300},
    ).output_artifacts[0]
    meeko = MeekoDockingPreparationAdapter(store)
    receptor_result = _invoke(
        meeko,
        DOCKING_RECEPTOR_PREPARE,
        inputs=(prepared_protein,),
        parameters={
            "center_x_angstrom": 4.5,
            "center_y_angstrom": 1.5,
            "center_z_angstrom": 0.0,
            "size_x_angstrom": 20.0,
            "size_y_angstrom": 20.0,
            "size_z_angstrom": 20.0,
        },
    )
    ligand_result = _invoke(
        meeko,
        DOCKING_LIGAND_PREPARE,
        inputs=(ligand_conformers,),
        parameters={"conformer_index": 0},
    )
    assert receptor_result.status is ExecutionStatus.SUCCESS
    assert ligand_result.status is ExecutionStatus.SUCCESS
    vina = VinaDockingAdapter(store)
    docking = _invoke(
        vina,
        DOCKING_POSE_GENERATE,
        inputs=(receptor_result.output_artifacts[0], ligand_result.output_artifacts[0]),
        parameters={
            "center_x_angstrom": 4.5,
            "center_y_angstrom": 1.5,
            "center_z_angstrom": 0.0,
            "size_x_angstrom": 20.0,
            "size_y_angstrom": 20.0,
            "size_z_angstrom": 20.0,
            "random_seed": 29,
            "exhaustiveness": 1,
            "pose_count": 2,
            "energy_range_kcal_mol": 5.0,
            "candidate_identifier": "candidate-ethanol",
            "receptor_preparation_method": "meeko-0.7.1-gasteiger",
            "ligand_preparation_method": "meeko-0.7.1-gasteiger",
        },
    )

    assert docking.status is ExecutionStatus.SUCCESS
    assert docking.diagnostics["pose_count"] >= 1
    assert docking.diagnostics["binding_free_energy_calculated"] is False
