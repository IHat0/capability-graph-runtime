"""Native scientist-facing Phase 8 end-to-end acceptance tests."""

from __future__ import annotations

import hashlib
import json

import pytest

from cgr.electronic_structure import (
    ElectronicFrequencyAnalysis,
    ElectronicQMMMHybridResult,
    ElectronicQMRegionPreparation,
    ElectronicReactionPathResult,
    ElectronicTransitionStateSearch,
    PySCFElectronicStructureAdapter,
)
from cgr.kernel.contracts import CapabilityVersion
from cgr.discovery import DiscoveryCampaignRun
from cgr.molecular import MeekoDockingPreparationAdapter, RDKitCheminformaticsAdapter
from cgr.quantum_workflow import QiskitQuantumWorkflowAdapter
from cgr.pulsate_api.phase8_scientific_handlers import (
    aqueous_conformer_registry,
    bond_dissociation_registry,
    covalent_transition_state_registry,
    metal_active_site_registry,
    protein_ligand_discovery_registry,
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


class MemoryPrivateStateStore:
    def __init__(self) -> None:
        self.payloads: dict[str, bytes] = {}

    def read(self, reference: ArtifactReference) -> bytes:
        return self.payloads[reference.artifact_identifier]

    def write(self, reference: ArtifactReference, payload: bytes) -> None:
        value = bytes(payload)
        existing = self.payloads.get(reference.artifact_identifier)
        if existing is not None and existing != value:
            raise ValueError("Private state identifier is already bound to other bytes.")
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


def _pdb_atom(
    serial: int,
    name: str,
    residue: str,
    sequence: int,
    x: float,
    y: float,
    z: float,
    element: str,
    charge: str = "",
) -> str:
    return (
        f"HETATM{serial:5d} {name:<4s} {residue:>3s} A{sequence:4d}    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}{1.0:6.2f}{0.0:6.2f}          "
        f"{element:>2s}{charge:>2s}\n"
    )


def test_acceptance_1_covalent_qmmm_transition_state(tmp_path) -> None:
    pytest.importorskip("rdkit")
    pytest.importorskip("openmm")
    pytest.importorskip("pyscf")
    pytest.importorskip("geometric")
    from rdkit import Chem
    from rdkit.Chem import AllChem

    # The active-site structure contains one chemically resolvable Cys SG.  The
    # separate ligand is an ethyl methyl thioether: connectivity, rather than a
    # fixture atom index, uniquely identifies the methyl electrophile and S
    # leaving group used by the semantic resolver.
    protein = "".join((
        _pdb_atom(1, "O", "CYS", 1, 0.0, 0.0, -7.9, "O"),
        _pdb_atom(2, "C", "CYS", 1, 0.0, 0.0, -6.7, "C"),
        _pdb_atom(3, "CA", "CYS", 1, 0.0, 0.0, -5.3, "C"),
        _pdb_atom(4, "CB", "CYS", 1, 0.0, 0.0, -3.8, "C"),
        _pdb_atom(5, "SG", "CYS", 1, 0.0, 0.0, -2.1, "S"),
        "TER\nEND\n",
    )).encode()
    ligand = Chem.AddHs(Chem.MolFromSmiles("CCSC"))
    assert AllChem.EmbedMolecule(ligand, randomSeed=17) == 0
    sulfur = next(atom for atom in ligand.GetAtoms() if atom.GetAtomicNum() == 16)
    target = next(
        atom for atom in sulfur.GetNeighbors()
        if atom.GetAtomicNum() == 6
        and sum(neighbor.GetAtomicNum() == 1 for neighbor in atom.GetNeighbors()) == 3
    )
    scaffold = next(atom for atom in sulfur.GetNeighbors() if atom.GetIdx() != target.GetIdx())
    distal = next(
        atom for atom in scaffold.GetNeighbors()
        if atom.GetAtomicNum() == 6 and atom.GetIdx() != sulfur.GetIdx()
    )
    conformer = ligand.GetConformer()
    heavy_positions = {
        target.GetIdx(): (0.0, 0.0, 0.0),
        sulfur.GetIdx(): (0.0, 0.0, 2.1),
        scaffold.GetIdx(): (0.0, 0.0, 3.8),
        distal.GetIdx(): (0.0, 0.0, 5.3),
    }
    for index, position in heavy_positions.items():
        conformer.SetAtomPosition(index, position)
    offsets = ((0.95, 0.0, 0.2), (-0.48, 0.83, 0.2), (-0.48, -0.83, 0.2))
    for atom in ligand.GetAtoms():
        if atom.GetAtomicNum() != 1:
            continue
        parent = atom.GetNeighbors()[0].GetIdx()
        parent_position = conformer.GetAtomPosition(parent)
        sibling_index = tuple(
            neighbor.GetIdx() for neighbor in ligand.GetAtomWithIdx(parent).GetNeighbors()
            if neighbor.GetAtomicNum() == 1
        ).index(atom.GetIdx())
        dx, dy, dz = offsets[sibling_index]
        conformer.SetAtomPosition(
            atom.GetIdx(),
            (parent_position.x + dx, parent_position.y + dy, parent_position.z + dz),
        )
    ligand_payload = Chem.MolToMolBlock(ligand).encode()

    store = MemoryPayloadStore()
    private = MemoryPrivateStateStore()
    protein_reference = _input_artifact(
        store,
        identifier="scientist-cysteine-active-site",
        artifact_type="protein_structure",
        media_type="chemical/x-pdb",
        payload=protein,
    )
    ligand_reference = _input_artifact(
        store,
        identifier="scientist-methyl-thioether-ligand",
        artifact_type="ligand_structure",
        media_type="chemical/x-mdl-molfile",
        payload=ligand_payload,
    )
    repository = ScientificExecutionRepository(tmp_path / "executions")
    repository.start()
    record = repository.create(
        ScientificObjectiveCompileRequest(
            question=(
                "Determine the transition state for covalent bond formation between "
                "this ligand and the catalytic nucleophile in this protease and verify "
                "that it connects reactant and product."
            ),
            input_references=(
                ScientificInputReference(
                    reference_identifier="protease",
                    artifact_type="protein_structure",
                    artifact_identifier=protein_reference.artifact_identifier,
                ),
                ScientificInputReference(
                    reference_identifier="ligand",
                    artifact_type="ligand_structure",
                    artifact_identifier=ligand_reference.artifact_identifier,
                ),
            ),
            artifact_references=(protein_reference, ligand_reference),
        )
    )
    adapter = PySCFElectronicStructureAdapter(store, private_state_store=private)
    runtime = ScientificObjectiveRuntime(
        root=tmp_path / "workflow",
        execution_repository=repository,
        capability_registry=covalent_transition_state_registry(
            store=store,
            private_store=private,
            pyscf_adapter=adapter,
        ),
    )
    runtime.start()

    completed = runtime.execute(record.execution_identifier)

    assert completed.status == "succeeded", [
        (node.capability_name, node.status, node.error_code, node.error_message)
        for node in completed.node_executions
    ]
    assert completed.verified
    assert completed.scene_identifier is not None
    assert completed.scientist_result is not None
    preparation_reference = next(
        item for item in completed.artifact_references
        if item.artifact_type == "electronic_qm_region_preparation"
    )
    preparation = ElectronicQMRegionPreparation.model_validate_json(
        store.read(preparation_reference)
    )
    assert len(preparation.boundary_links) >= 2
    hybrid_reference = next(
        item for item in completed.artifact_references
        if item.artifact_type == "electronic_qmmm_hybrid_result"
    )
    hybrid = ElectronicQMMMHybridResult.model_validate_json(store.read(hybrid_reference))
    assert hybrid.no_double_counting_verified
    assert hybrid.link_atom_gradient_projected
    search_reference = next(
        item for item in completed.artifact_references
        if item.artifact_type == "electronic_transition_state_search"
    )
    search = ElectronicTransitionStateSearch.model_validate_json(
        store.read(search_reference)
    )
    assert search.optimization_surface == "hybrid_qmmm"
    assert search.hybrid_formulation_identifier == hybrid.formulation
    assert search.hybrid_gradient_evaluation_count > 1
    assert len(search.energy_history_hartree) == search.hybrid_gradient_evaluation_count
    assert search.final_energy_hartree == search.energy_history_hartree[-1]
    assert search.full_particle_count == hybrid.particle_count
    assert search.optimized_full_geometry_angstrom.shape == (hybrid.particle_count, 3)
    assert search.final_full_gradient_hartree_per_bohr.shape == (hybrid.particle_count, 3)
    assert len(search.movable_particle_indices) == 3
    assert not search.restrained_particle_indices
    assert search.frozen_particle_indices
    assert set(search.movable_particle_indices) | set(search.frozen_particle_indices) == set(
        range(hybrid.particle_count)
    )
    assert search.region_selection_method == (
        "semantic_reacting_triad_movable_full_environment_frozen"
    )
    frequency_reference = next(
        item for item in completed.artifact_references
        if item.artifact_type == "electronic_frequency_analysis"
    )
    frequency = ElectronicFrequencyAnalysis.model_validate_json(store.read(frequency_reference))
    assert frequency.characterization_surface == "hybrid_qmmm"
    assert frequency.hessian_method == "finite_difference_hybrid_gradients"
    assert frequency.hybrid_gradient_evaluation_count == 6 * len(
        search.movable_particle_indices
    )
    assert frequency.exactly_one_significant_imaginary_mode
    imaginary = next(mode for mode in frequency.modes if mode.significant_imaginary)
    assert imaginary.reaction_coordinate_participation is not None
    assert imaginary.reaction_coordinate_participation > 0.1
    path_reference = next(
        item for item in completed.artifact_references
        if item.artifact_type == "electronic_reaction_path_result"
    )
    path = ElectronicReactionPathResult.model_validate_json(store.read(path_reference))
    assert path.path_surface == "hybrid_qmmm"
    assert path.method == "hybrid_qmmm_mass_weighted_steepest_descent"
    assert path.hybrid_gradient_evaluation_count >= len(path.points)
    assert path.forward_endpoint_hybrid_energy_hartree < search.final_energy_hartree
    assert path.reverse_endpoint_hybrid_energy_hartree < search.final_energy_hartree
    assert all(point.hybrid_energy_hartree == point.energy_hartree for point in path.points)
    assert all(point.full_geometry_angstrom is not None for point in path.points)
    assert path.path_confirmation_passed
    assert all(
        forbidden not in record.objective.model_dump_json()
        for forbidden in (
            "atom_index", "qm_particle", "link_atom", "ts_geometry", "workflow_graph"
        )
    )


def test_acceptance_2_metal_active_site_local_vqe(tmp_path) -> None:
    pytest.importorskip("pyscf")
    pytest.importorskip("qiskit")
    pytest.importorskip("qiskit_nature")
    pytest.importorskip("qiskit_algorithms")

    # Compact Cu(II) diammine first-shell model.  Atom serials are ordinary PDB
    # identity, never scientist-supplied computational indices.
    pdb = "".join(
        (
            _pdb_atom(1, "CU", "CU", 1, 0.0, 0.0, 0.0, "Cu", "2+"),
            _pdb_atom(2, "N1", "AMM", 2, 2.0, 0.0, 0.0, "N"),
            _pdb_atom(3, "H11", "AMM", 2, 2.3, 0.94, 0.0, "H"),
            _pdb_atom(4, "H12", "AMM", 2, 2.3, -0.47, 0.81, "H"),
            _pdb_atom(5, "H13", "AMM", 2, 2.3, -0.47, -0.81, "H"),
            _pdb_atom(6, "N2", "AMM", 3, -2.0, 0.0, 0.0, "N"),
            _pdb_atom(7, "H21", "AMM", 3, -2.3, 0.94, 0.0, "H"),
            _pdb_atom(8, "H22", "AMM", 3, -2.3, -0.47, 0.81, "H"),
            _pdb_atom(9, "H23", "AMM", 3, -2.3, -0.47, -0.81, "H"),
            "END\n",
        )
    ).encode("utf-8")
    store = MemoryPayloadStore()
    source = _input_artifact(
        store,
        identifier="scientist-cu-ammine-active-site",
        artifact_type="protein_structure",
        media_type="chemical/x-pdb",
        payload=pdb,
    )
    repository = ScientificExecutionRepository(tmp_path / "executions")
    repository.start()
    record = repository.create(
        ScientificObjectiveCompileRequest(
            question=(
                "Study the metal-centred active site of this enzyme with an "
                "automatically selected active space and prepare/run the appropriate "
                "VQE workflow."
            ),
            input_references=(
                ScientificInputReference(
                    reference_identifier="cu-active-site",
                    artifact_type="protein_structure",
                    artifact_identifier=source.artifact_identifier,
                ),
            ),
            artifact_references=(source,),
        )
    )
    runtime = ScientificObjectiveRuntime(
        root=tmp_path / "workflow",
        execution_repository=repository,
        capability_registry=metal_active_site_registry(
            store=store,
            pyscf_adapter=PySCFElectronicStructureAdapter(store),
            qiskit_adapter=QiskitQuantumWorkflowAdapter(store),
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
    semantic_reference = next(
        item for item in completed.artifact_references
        if item.artifact_type == "semantic_target_selection"
    )
    semantic = json.loads(store.read(semantic_reference))
    assert semantic["metal_element"] == "Cu"
    assert semantic["coordination_number"] == 2
    assert len(semantic["state_alternatives"]) == 2
    assert sum(item["selected"] for item in semantic["state_alternatives"]) == 1
    preparation_reference = next(
        item for item in completed.artifact_references
        if item.artifact_type == "electronic_qm_region_preparation"
    )
    preparation = json.loads(store.read(preparation_reference))
    assert preparation["charge_spin_assessment"]["spin_candidates"] == [1, 3, 5]
    assert any(
        item.artifact_type == "variational_ground_state_result"
        for item in completed.artifact_references
    )
    assert not completed.plan.ibm_job_submission_planned
    assert all(
        forbidden not in record.objective.model_dump_json()
        for forbidden in (
            "atom_index",
            "orbital_index",
            "hamiltonian",
            "d_orbital",
            "workflow_graph",
        )
    )


def test_acceptance_5_autonomous_multi_generation_drug_discovery(tmp_path) -> None:
    pytest.importorskip("rdkit")
    pytest.importorskip("meeko")
    pytest.importorskip("vina")

    # A complete three-residue standard peptide keeps native Meeko/Vina runtime
    # practical while exercising the same receptor-preparation and docking code.
    protein = b"""ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N
ATOM      2  CA  ALA A   1       1.450   0.000   0.000  1.00  0.00           C
ATOM      3  C   ALA A   1       2.000   1.400   0.000  1.00  0.00           C
ATOM      4  O   ALA A   1       1.400   2.400   0.000  1.00  0.00           O
ATOM      5  CB  ALA A   1       1.900  -0.800   1.200  1.00  0.00           C
ATOM      6  N   ALA A   2       3.250   1.450   0.000  1.00  0.00           N
ATOM      7  CA  ALA A   2       3.900   2.700   0.000  1.00  0.00           C
ATOM      8  C   ALA A   2       5.400   2.500   0.000  1.00  0.00           C
ATOM      9  O   ALA A   2       6.000   3.500   0.000  1.00  0.00           O
ATOM     10  CB  ALA A   2       3.300   3.600   1.100  1.00  0.00           C
ATOM     11  N   ALA A   3       6.000   1.350   0.000  1.00  0.00           N
ATOM     12  CA  ALA A   3       7.400   1.000   0.000  1.00  0.00           C
ATOM     13  C   ALA A   3       8.100   2.300   0.000  1.00  0.00           C
ATOM     14  O   ALA A   3       7.600   3.400   0.000  1.00  0.00           O
ATOM     15  CB  ALA A   3       7.700  -0.100   1.000  1.00  0.00           C
ATOM     16  OXT ALA A   3       9.300   2.200   0.000  1.00  0.00           O
TER
END
"""
    store = MemoryPayloadStore()
    source = _input_artifact(
        store,
        identifier="scientist-discovery-protein",
        artifact_type="protein_structure",
        media_type="chemical/x-pdb",
        payload=protein,
    )
    repository = ScientificExecutionRepository(tmp_path / "executions")
    repository.start()
    record = repository.create(
        ScientificObjectiveCompileRequest(
            question="Discover and optimize a drug candidate for this protein.",
            input_references=(
                ScientificInputReference(
                    reference_identifier="discovery-protein",
                    artifact_type="protein_structure",
                    artifact_identifier=source.artifact_identifier,
                ),
            ),
            artifact_references=(source,),
        )
    )
    runtime = ScientificObjectiveRuntime(
        root=tmp_path / "workflow",
        execution_repository=repository,
        capability_registry=protein_ligand_discovery_registry(
            store=store,
            meeko_adapter=MeekoDockingPreparationAdapter(store),
        ),
    )
    runtime.start()

    completed = runtime.execute(record.execution_identifier)

    assert completed.status == "succeeded", [
        (node.capability_name, node.status, node.error_code, node.error_message)
        for node in completed.node_executions
    ]
    assert completed.verified
    assert completed.scene_identifier is not None
    assert completed.scientist_result is not None
    campaign_reference = next(
        item for item in completed.artifact_references
        if item.artifact_type == "discovery_campaign_result"
    )
    campaign = DiscoveryCampaignRun.model_validate_json(store.read(campaign_reference))
    assert campaign.completed
    assert len(campaign.state.generations) == 2
    assert campaign.state.usage.candidates_generated >= 2
    assert campaign.state.usage.expensive_evaluations >= 2
    assert campaign.state.lineage.edges
    assert campaign.state.generations[-1].selected_candidate_identifiers
    assert completed.checkpoint_identifiers == (campaign.checkpoint.checkpoint_identifier,)
    docking_evidence = [
        json.loads(store.read(item))
        for item in completed.artifact_references
        if item.artifact_type == "molecular_candidate_docking_evaluation"
    ]
    assert len(docking_evidence) >= 2
    assert all(item["returned_pose_count"] >= 1 for item in docking_evidence)
    assert all(item["score_semantics"] == "autodock_vina_score_not_binding_free_energy" for item in docking_evidence)
    assert any(
        item.artifact_type == "molecular_candidate_docking_poses_pdbqt"
        for item in completed.artifact_references
    )
    assert all(
        forbidden not in record.objective.model_dump_json()
        for forbidden in (
            "candidate_smiles", "docking_pose", "candidate_ranking", "workflow_graph"
        )
    )
