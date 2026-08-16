"""Major C evidence-grounded research visualization regressions."""

from __future__ import annotations

import hashlib
import json
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
