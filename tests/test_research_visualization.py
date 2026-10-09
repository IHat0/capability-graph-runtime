"""Major C evidence-grounded research visualization regressions."""

from __future__ import annotations

import hashlib
import json
import pytest
from datetime import UTC, datetime

from cgr.kernel.contracts import CapabilityVersion
from cgr.pulsate_api.research_scene import project_research_complex_scene
from cgr.pulsate_api.research_sessions import (
    ResearchConversationTurn,
    ResearchSession,
)
from cgr.pulsate_api.research_visualization import build_research_visualization
from cgr.pulsate_api.scientific_objectives import ScientificInputReference
from cgr.science import ArtifactReference, CreationProvenance


_VERSION = CapabilityVersion(major=1, minor=0, patch=0)


def _pdb_payload(offset: float = 0.0) -> bytes:
    def atom(serial: int, name: str, x: float, element: str) -> str:
        return (
            f"ATOM  {serial:5d} {name:<4} LIG A{1:4d}    "
            f"{x:8.3f}{11.0:8.3f}{12.0:8.3f}{1.0:6.2f}{20.0:6.2f}"
            f"          {element:>2}  \n"
        )

    return (
        atom(1, "C1", 10.0 + offset, "C")
        + atom(2, "O1", 11.0 + offset, "O")
        + "CONECT    1    2\nEND\n"
    ).encode()


def _reference(
    artifact_type: str,
    payload: bytes,
    *,
    media_type: str = "application/vnd.pulsate.test+json",
    parents: tuple[ArtifactReference, ...] = (),
) -> ArtifactReference:
    digest = hashlib.sha256(payload).hexdigest()
    return ArtifactReference(
        artifact_identifier=f"{artifact_type.replace('_', '-')}-{digest[:32]}",
        schema_version=_VERSION,
        artifact_type=artifact_type,
        media_type=media_type,
        content_sha256=digest,
        byte_size=len(payload),
        provenance=CreationProvenance(
            producer="major-c-tests",
            producer_version=_VERSION,
            execution_identifier="major-c-test-execution",
        ),
        parents=tuple(item.pointer for item in parents),
    )


class _Store:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads

    def read(self, reference: ArtifactReference) -> bytes:
        return self.payloads[reference.artifact_identifier]


def _session(protein: ArtifactReference) -> ResearchSession:
    now = datetime(2026, 8, 14, tzinfo=UTC)
    return ResearchSession(
        session_identifier=f"research-session-{'a' * 32}",
        tenant_identifier_sha256="b" * 64,
        created_at=now,
        updated_at=now,
        revision=1,
        status="understanding",
        conversation=(
            ResearchConversationTurn(
                turn_identifier="turn-major-c-test",
                role="scientist",
                content="Investigate this exact structure.",
                created_at=now,
            ),
        ),
        input_references=(
            ScientificInputReference(
                reference_identifier="input-major-c-protein",
                artifact_type="protein_structure",
                artifact_identifier=protein.artifact_identifier,
            ),
        ),
        artifact_references=(protein,),
        scientist_summary="Understanding the request.",
    )


@pytest.mark.parametrize('document,status,value', [
    ({'passed':True},'verified',True),
    ({'passed':False},'failed',False),
    ({'verification_scope':'No result yet'},'inconclusive','Not evaluated'),
])
def test_verification_overlay_reads_the_persisted_report_not_missing_metadata(document,status,value):
    protein_payload=_pdb_payload();protein=_reference('protein_structure',protein_payload,media_type='chemical/x-pdb')
    payload=json.dumps(document).encode();report=_reference('scientific_verification_report',payload)
    workspace=build_research_visualization(session=_session(protein),
        store=_Store({protein.artifact_identifier:protein_payload,report.artifact_identifier:payload}),
        artifact_references=(protein,report))
    indicator=next(o for o in workspace['overlays'] if o['kind']=='verification_indicator')
    assert indicator['verification_status']==status
    assert indicator['value']==value


def test_visualization_composes_candidates_contacts_overlays_and_exports() -> None:
    protein_payload = _pdb_payload()
    pose_payload = _pdb_payload(0.25)
    protein = _reference(
        "protein_structure", protein_payload, media_type="chemical/x-pdb"
    )
    pose = _reference(
        "molecular_candidate_docking_poses_pdbqt",
        pose_payload,
        media_type="chemical/x-pdbqt",
    )
    candidate_identifier = "candidate-major-c"
    trace_payload = json.dumps(
        {
            "selected_candidate_identifiers": [candidate_identifier],
            "candidates": [
                {
                    "candidate_identifier": candidate_identifier,
                    "generation": 1,
                    "parent_candidate_identifiers": ["candidate-parent"],
                    "transformation": {"transformation_kind": "substitution"},
                    "properties_and_calculations": [],
                    "evidence": [
                        {
                            "artifacts": [
                                {
                                    "artifact_identifier": pose.artifact_identifier,
                                    "content_sha256": pose.content_sha256,
                                }
                            ]
                        }
                    ],
                    "verification": [],
                    "selection": {"rationale": "Verified ranking."},
                },
                {
                    "candidate_identifier": "candidate-parent",
                    "generation": 0,
                    "parent_candidate_identifiers": [],
                    "transformation": None,
                    "properties_and_calculations": [],
                    "evidence": [],
                    "verification": [],
                    "selection": None,
                },
            ],
            "lineage": [],
        },
        sort_keys=True,
    ).encode()
    trace = _reference("discovery_design_loop_trace", trace_payload)
    interaction_payload = json.dumps(
        {
            "interactions": [
                {
                    "interaction_identifier": "interaction-major-c",
                    "interaction_type": "hydrophobic_contact",
                    "candidate_identifier": candidate_identifier,
                    "structure_artifact_identifiers": [
                        protein.artifact_identifier,
                        pose.artifact_identifier,
                    ],
                    "atom_identifiers": ["atom-000001-1", "atom-000001-1"],
                    "residue_identifiers": ["chain-a-residue-1-lig"],
                    "distance_angstrom": 0.25,
                    "angle_degree": None,
                    "calculation_method": "deterministic_distance_rule",
                }
            ]
        }
    ).encode()
    interaction = _reference("molecular_interaction_analysis", interaction_payload)
    overlay_payload = json.dumps(
        {
            "overlays": [
                {
                    "overlay_identifier": "overlay-major-c",
                    "kind": "candidate_property",
                    "label": "Docking score",
                    "structure_artifact_identifier": pose.artifact_identifier,
                    "candidate_identifier": candidate_identifier,
                    "value": -7.2,
                    "unit": "kcal/mol",
                    "atom_identifiers": [],
                    "residue_identifiers": [],
                    "verification_status": "verified",
                    "uncertainty": "Ranking score only.",
                    "method": "autodock_vina",
                }
            ]
        }
    ).encode()
    overlay = _reference("molecular_computational_overlay", overlay_payload)
    references = (protein, pose, trace, interaction, overlay)
    workspace = build_research_visualization(
        session=_session(protein),
        store=_Store(
            {
                item.artifact_identifier: payload
                for item, payload in zip(
                    references,
                    (
                        protein_payload,
                        pose_payload,
                        trace_payload,
                        interaction_payload,
                        overlay_payload,
                    ),
                    strict=True,
                )
            }
        ),
        artifact_references=references,
    )

    assert workspace["grounding_policy"] == (
        "persisted_artifact_or_deterministic_computation_only"
    )
    assert workspace["structures"][1]["candidate_identifier"] == candidate_identifier
    assert workspace["candidates"][1]["selected"] is True
    assert workspace["interactions"][0]["evidence_artifact_identifier"] == (
        interaction.artifact_identifier
    )
    assert workspace["overlays"][0]["verification_status"] == "verified"
    assert workspace["comparisons"][0]["kind"] == "before_after_transformation"
    assert {item["category"] for item in workspace["export_items"]} == {
        "evidence",
        "report",
        "structure",
    }


def test_complex_scene_keeps_structure_and_atom_identities_separate() -> None:
    left_payload = _pdb_payload()
    right_payload = _pdb_payload(1.5)
    left = _reference(
        "protein_structure", left_payload, media_type="chemical/x-pdb"
    )
    right = _reference(
        "ligand_structure", right_payload, media_type="chemical/x-pdb"
    )

    scene = project_research_complex_scene(
        session_identifier=f"research-session-{'c' * 32}",
        evidence=((left, left_payload), (right, right_payload)),
    )

    assert scene["scene_stage"] == "research_comparison"
    assert len(scene["atoms"]) == 4
    assert len({item["atom_identifier"] for item in scene["atoms"]}) == 4
    assert {item["structure_artifact_identifier"] for item in scene["atoms"]} == {
        left.artifact_identifier,
        right.artifact_identifier,
    }


@pytest.mark.parametrize('classical', [False, True])
def test_constructed_result_keeps_unlike_energies_separate_and_links_generated_structure(classical):
    structure_payload = (
        '\n  independently generated\n\n  2  1  0  0  0  0            999 V2000\n'
        '    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n'
        '    1.4000    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0\n'
        '  1  2  1  0  0  0  0\nM  END\n$$$$\n'
    ).encode()
    structure = _reference('constructed_molecule_sdf', structure_payload, media_type='chemical/x-mdl-sdfile')
    documents = {
        'molecular_construction_evidence': {'name': 'generated fixture', 'formula': 'CO',
            'atom_count_including_hydrogens': 2, 'generated_conformers': 3, 'converged_conformers': 2,
            'sdf_artifact_identifier': structure.artifact_identifier, 'selected_conformer': {'energy': 7.25}},
        'molecular_identity_verification': {'passed': True},
        'molecular_ground_state_execution_receipt': {'scientific_controls': {'active_electron_count': 2, 'active_spatial_orbital_count': 2},
            'mapping': {'number_of_qubits_after_reduction': 4}, 'hartree_fock': {'total_energy_hartree': -90.0},
            'exact': {'total_energy_hartree': -90.1}, 'vqe': {'total_energy_hartree': -90.09},
            'verification': {'absolute_difference_hartree': .01}},
        'quantum_hardware_proposal': {'status': 'backend_preparation_pending'},
    }
    if classical:
        documents.pop('molecular_ground_state_execution_receipt')
        documents.pop('quantum_hardware_proposal')
        documents['molecular_construction_evidence']['xyz_artifact_identifier'] = 'generated-xyz'
        documents['molecular_construction_execution_receipt'] = {
            'hartree_fock': {'total_energy_hartree': -90.0},
            'computation_selection': {'reason': 'Classical methods are sufficient for structural construction.'},
            'workflow': ['identity', 'construction', 'conformers', 'optimization', 'RHF', 'identity verification'],
        }
    payloads = {structure.artifact_identifier: structure_payload}
    references = [structure]
    for kind, document in documents.items():
        payload = json.dumps(document).encode()
        reference = _reference(kind, payload)
        references.append(reference)
        payloads[reference.artifact_identifier] = payload
    workspace = build_research_visualization(session=_session(structure), store=_Store(payloads), artifact_references=tuple(references))
    result = workspace['construction_summary']
    assert result['structure_artifact_identifier'] == structure.artifact_identifier
    assert result['identity_verified'] is True
    assert result['energies'][0] == {'label': 'Optimized force-field geometry', 'value': 7.25, 'unit': 'kcal/mol'}
    assert result['energies'][1]['value'] == -90.0
    assert result['energies'][1]['unit'] == 'Hartree'
    if classical:
        assert result['selected_compute'] == 'classical'
        assert result['xyz_artifact_identifier'] == 'generated-xyz'
        assert len(result['energies']) == 2
        assert 'logical_qubits' not in result
        assert 'hardware_status' not in result
    else:
        assert result['hardware_status'] == 'backend_preparation_pending'


def test_prospective_cards_are_projected_from_the_exact_persisted_assessment():
    document = {'schema': 'pulsate.prospective-assessment/v1',
        'candidates': [{'name': 'compound fixture', 'recommendation': 'insufficient_evidence'}],
        'verification_scope': 'Integrity only.'}
    payload = json.dumps(document).encode()
    reference = _reference('prospective_candidate_assessment', payload)
    workspace = build_research_visualization(session=_session(reference), store=_Store({reference.artifact_identifier: payload}),
                                             artifact_references=(reference,))
    assert workspace['prospective_assessment'] == dict(document, assessment_artifact_identifier=reference.artifact_identifier,
                                                       assessment_sha256=reference.content_sha256)
    assert workspace['construction_summary'] is None


def test_nested_native_download_matches_its_own_persisted_assessment_not_another_candidate():
    native={'schema_version':'pulsate.virtual-organism/v1','candidate':{'name':'Synthetic'},'runs':[]}
    native_payload=json.dumps(native).encode()
    native_ref=_reference('virtual_organism_assessment',native_payload)
    document={'virtual_investigation':{'candidates':[{'candidate_identifier':'fixture',
        'native_artifact_identifiers':[native_ref.artifact_identifier],'virtual_organism':native}]}}
    payload=json.dumps(document).encode()
    ref=_reference('prospective_candidate_assessment',payload)
    store=_Store({ref.artifact_identifier:payload,native_ref.artifact_identifier:native_payload})
    workspace=build_research_visualization(session=_session(ref),store=store,artifact_references=(ref,native_ref))
    shown=workspace['prospective_assessment']['virtual_investigation']['candidates'][0]['virtual_organism']
    assert shown['assessment_artifact_identifier']==native_ref.artifact_identifier
    assert shown['assessment_sha256']==native_ref.content_sha256
    assert 'assessment_sha256' not in json.loads(store.read(ref))['virtual_investigation']['candidates'][0]['virtual_organism']
    document['virtual_investigation']['candidates'][0]['virtual_organism']['runs']=[{'invented':True}]
    corrupt=json.dumps(document).encode()
    ref=_reference('prospective_candidate_assessment',corrupt)
    store.payloads[ref.artifact_identifier]=corrupt
    with pytest.raises(ValueError,match='exact verified assessment'):
        build_research_visualization(session=_session(ref),store=store,artifact_references=(ref,native_ref))
