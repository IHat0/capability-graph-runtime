"""Evidence-grounded discovery visualization for one durable research session."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol

from cgr.science import ArtifactReference

from .research_scene import project_research_scene, visualizable_research_artifact
from .research_sessions import ResearchSession


class ResearchVisualizationError(ValueError):
    """Raised when persisted visualization evidence is inconsistent."""


class ResearchVisualizationPayloadStore(Protocol):
    def read(self, reference: ArtifactReference) -> bytes: ...


_JSON_VISUALIZATION_ARTIFACT_TYPES = frozenset(
    {
        "binding_pocket",
        "discovery_design_loop_trace",
        "electronic_conformer_comparison",
        "electronic_qm_region_preparation",
        "electronic_reaction_path_result",
        "electronic_transition_state_search",
        "molecular_simulation_system",
        "molecular_trajectory",
        "molecular_candidate_docking_evaluation",
        "molecular_computational_overlay",
        "molecular_interaction_analysis",
        "molecular_scene_state",
        "scientific_verification_report",
        "potential_energy_curve",
    }
)
_REPORT_ARTIFACT_TYPES = frozenset(
    {
        "discovery_design_loop_contract",
        "discovery_design_loop_trace",
        "molecular_scene_state",
        "scientific_verification_report",
    }
)


def _json_payload(
    reference: ArtifactReference,
    store: ResearchVisualizationPayloadStore,
) -> dict[str, object]:
    payload = store.read(reference)
    if len(payload) > 8 * 1024 * 1024:
        raise ResearchVisualizationError("Visualization evidence exceeds its size limit.")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ResearchVisualizationError(
            "Visualization evidence is not valid JSON."
        ) from None
    if not isinstance(value, dict):
        raise ResearchVisualizationError(
            "Visualization evidence must be a JSON object."
        )
    return value


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, dict) else {}


def _structure_role(reference: ArtifactReference) -> str:
    if reference.artifact_type in {
        "docking_receptor_pdbqt",
        "predicted_protein_structure",
        "protein_structure",
        "prepared_receptor",
    }:
        return "protein"
    if reference.artifact_type in {
        "ligand_structure",
        "prepared_ligand",
        "molecular_candidate_docking_poses_pdbqt",
        "docking_pose_pdbqt",
    }:
        return "ligand_or_candidate"
    return "molecular_structure"


def _candidate_artifact_bindings(
    trace: Mapping[str, object],
) -> tuple[dict[str, str], dict[str, dict[str, object]]]:
    artifact_candidates: dict[str, str] = {}
    candidates: dict[str, dict[str, object]] = {}
    selected = set(_strings(trace.get("selected_candidate_identifiers")))
    raw_candidates = trace.get("candidates")
    if not isinstance(raw_candidates, list):
        return artifact_candidates, candidates
    for raw in raw_candidates:
        candidate = _mapping(raw)
        identifier = _string(candidate.get("candidate_identifier"))
        if identifier is None:
            continue
        evidence_artifacts: set[str] = set()
        evidence = candidate.get("evidence")
        if isinstance(evidence, list):
            for raw_record in evidence:
                record = _mapping(raw_record)
                artifacts = record.get("artifacts")
                if not isinstance(artifacts, list):
                    continue
                for raw_pointer in artifacts:
                    pointer = _mapping(raw_pointer)
                    artifact_identifier = _string(
                        pointer.get("artifact_identifier")
                    )
                    if artifact_identifier is not None:
                        evidence_artifacts.add(artifact_identifier)
                        artifact_candidates[artifact_identifier] = identifier
        selection = _mapping(candidate.get("selection"))
        candidates[identifier] = {
            "candidate_identifier": identifier,
            "generation": candidate.get("generation", 0),
            "parent_candidate_identifiers": _strings(
                candidate.get("parent_candidate_identifiers")
            ),
            "transformation": candidate.get("transformation"),
            "properties_and_calculations": candidate.get(
                "properties_and_calculations", []
            ),
            "verification": candidate.get("verification", []),
            "selection_rationale": selection.get("rationale"),
            "selected": identifier in selected,
            "structure_artifact_identifiers": sorted(evidence_artifacts),
        }
    return artifact_candidates, candidates


def build_research_visualization(
    *,
    session: ResearchSession,
    store: ResearchVisualizationPayloadStore,
    artifact_references: tuple[ArtifactReference, ...] | None = None,
) -> dict[str, object]:
    """Compose one read-only workspace solely from persisted session evidence."""

    all_references = artifact_references or session.artifact_references
    references = {item.artifact_identifier: item for item in all_references}
    if len(references) != len(all_references):
        raise ResearchVisualizationError(
            "Visualization artifact identities are inconsistent."
        )
    documents: dict[str, dict[str, object]] = {}
    for reference in all_references:
        if (
            reference.artifact_type in _JSON_VISUALIZATION_ARTIFACT_TYPES
            and "json" in reference.media_type.casefold()
        ):
            documents[reference.artifact_identifier] = _json_payload(reference, store)

    trace_reference = next(
        (
            item
            for item in all_references
            if item.artifact_type == "discovery_design_loop_trace"
        ),
        None,
    )
    trace = (
        documents.get(trace_reference.artifact_identifier, {})
        if trace_reference is not None
        else {}
    )
    artifact_candidates, candidate_map = _candidate_artifact_bindings(trace)

    structure_atom_ids: dict[str, list[str]] = {}
    for reference in all_references:
        if not visualizable_research_artifact(reference):
            continue
        projected = project_research_scene(
            session_identifier=session.session_identifier,
            reference=reference,
            payload=store.read(reference),
        )
        structure_atom_ids[reference.artifact_identifier] = [
            str(item["source_atom_identifier"])
            for item in projected["atoms"]
        ]

    def source_structure(reference: ArtifactReference) -> str | None:
        pending = [item.artifact_identifier for item in reference.parents]
        visited: set[str] = set()
        while pending:
            identifier = pending.pop(0)
            if identifier in visited:
                continue
            visited.add(identifier)
            if identifier in structure_atom_ids:
                return identifier
            parent = references.get(identifier)
            if parent is not None:
                pending.extend(item.artifact_identifier for item in parent.parents)
        return next(iter(structure_atom_ids), None)

    structures: list[dict[str, object]] = []
    for reference in all_references:
        if not visualizable_research_artifact(reference):
            continue
        candidate_identifier = artifact_candidates.get(reference.artifact_identifier)
        conformation_count = 1
        if reference.media_type == "chemical/x-pdbqt":
            payload = store.read(reference)
            model_count = sum(
                1
                for line in payload.decode("utf-8").splitlines()
                if line[:6].strip().upper() == "MODEL"
            )
            conformation_count = max(1, model_count)
        structure = {
            "artifact_identifier": reference.artifact_identifier,
            "artifact_type": reference.artifact_type,
            "media_type": reference.media_type,
            "label": (
                candidate_identifier
                or _string(reference.metadata.get("display_label"))
                or reference.artifact_type.replace("_", " ")
            ),
            "role": _structure_role(reference),
            "candidate_identifier": candidate_identifier,
            "generation": (
                candidate_map.get(candidate_identifier, {}).get("generation")
                if candidate_identifier is not None
                else None
            ),
            "selected": bool(
                candidate_identifier
                and candidate_map.get(candidate_identifier, {}).get("selected")
            ),
            "conformation_count": conformation_count,
            "source_kind": _string(
                reference.metadata.get("acquisition_source_kind")
            ),
            "source_identifier": _string(
                reference.metadata.get("acquisition_source_identifier")
            ),
            "confidence": _string(
                reference.metadata.get("evidence_resolution_confidence")
            ),
            "content_sha256": reference.content_sha256,
        }
        structures.append(structure)

    selections: list[dict[str, object]] = []
    interactions: list[dict[str, object]] = []
    overlays: list[dict[str, object]] = []
    verification_artifacts: list[str] = []
    for artifact_identifier, document in documents.items():
        reference = references[artifact_identifier]
        if reference.artifact_type == "binding_pocket":
            selections.append(
                {
                    "selection_identifier": f"selection-{artifact_identifier}",
                    "label": "Computed binding pocket",
                    "kind": "binding_pocket",
                    "structure_artifact_identifier": document.get(
                        "source_protein_artifact_identifier"
                    ),
                    "atom_identifiers": [],
                    "residue_identifiers": _strings(
                        document.get("selected_residue_identifiers")
                    ),
                    "evidence_artifact_identifier": artifact_identifier,
                }
            )
            overlays.append(
                {
                    "overlay_identifier": f"overlay-{artifact_identifier}",
                    "kind": "binding_pocket",
                    "label": "Docking search region",
                    "structure_artifact_identifier": document.get(
                        "source_protein_artifact_identifier"
                    ),
                    "candidate_identifier": None,
                    "value": document.get("size_angstrom"),
                    "unit": "angstrom",
                    "atom_identifiers": [],
                    "residue_identifiers": _strings(
                        document.get("selected_residue_identifiers")
                    ),
                    "verification_status": "computed",
                    "uncertainty": None,
                    "evidence_artifact_identifier": artifact_identifier,
                    "method": document.get("definition_method"),
                }
            )
        elif reference.artifact_type == "molecular_interaction_analysis":
            raw_interactions = document.get("interactions")
            if isinstance(raw_interactions, list):
                for raw in raw_interactions:
                    interaction = dict(_mapping(raw))
                    if interaction:
                        interaction["evidence_artifact_identifier"] = artifact_identifier
                        interactions.append(interaction)
        elif reference.artifact_type == "molecular_candidate_docking_evaluation":
            candidate_identifier = _string(document.get("candidate_identifier"))
            scores = document.get("vina_scores_kcal_per_mol")
            if candidate_identifier is not None and isinstance(scores, list) and scores:
                overlays.append(
                    {
                        "overlay_identifier": f"overlay-{artifact_identifier}",
                        "kind": "docking_score",
                        "label": "Best Vina pose score",
                        "structure_artifact_identifier": None,
                        "candidate_identifier": candidate_identifier,
                        "value": scores[0],
                        "unit": "kcal/mol",
                        "atom_identifiers": [],
                        "residue_identifiers": [],
                        "verification_status": "computed",
                        "uncertainty": "Ranking score; not a binding free energy.",
                        "evidence_artifact_identifier": artifact_identifier,
                        "method": "autodock_vina",
                    }
                )
        elif reference.artifact_type == "molecular_computational_overlay":
            raw_overlays = document.get("overlays")
            if isinstance(raw_overlays, list):
                for raw in raw_overlays:
                    overlay = dict(_mapping(raw))
                    if overlay:
                        overlay["evidence_artifact_identifier"] = artifact_identifier
                        overlays.append(overlay)
        elif reference.artifact_type == "molecular_scene_state":
            structure_identifier = source_structure(reference)
            source_atoms = structure_atom_ids.get(structure_identifier or "", [])
            selected_indices = [
                item
                for item in document.get("selected_atoms", [])
                if isinstance(item, int) and 0 <= item < len(source_atoms)
            ] if isinstance(document.get("selected_atoms"), list) else []
            selected_atoms = [source_atoms[index] for index in selected_indices]
            if selected_atoms:
                semantic = _mapping(document.get("semantic_selection"))
                overlays.append(
                    {
                        "overlay_identifier": f"overlay-selection-{artifact_identifier}",
                        "kind": "active_region",
                        "label": str(
                            semantic.get("target_kind", "Resolved interaction region")
                        ).replace("_", " "),
                        "structure_artifact_identifier": structure_identifier,
                        "candidate_identifier": None,
                        "value": len(selected_atoms),
                        "unit": "atoms",
                        "atom_identifiers": selected_atoms,
                        "residue_identifiers": [],
                        "verification_status": str(
                            document.get("computational_state", "computed")
                        ),
                        "uncertainty": None,
                        "evidence_artifact_identifier": artifact_identifier,
                        "method": "persisted_semantic_target_selection",
                    }
                )
        elif reference.artifact_type == "electronic_conformer_comparison":
            structure_identifier = source_structure(reference)
            results = _mapping(document.get("results"))
            for conformer, raw_result in results.items():
                result = _mapping(raw_result)
                overlays.append(
                    {
                        "overlay_identifier": f"overlay-{artifact_identifier}-{conformer}",
                        "kind": "conformer_energy",
                        "label": f"{conformer} conformer energy",
                        "structure_artifact_identifier": structure_identifier,
                        "candidate_identifier": None,
                        "value": result.get("solvated_total_energy_hartree"),
                        "unit": "hartree",
                        "atom_identifiers": [],
                        "residue_identifiers": [],
                        "verification_status": "computed",
                        "uncertainty": (
                            "Electronic energy; thermal and Gibbs corrections are not included."
                        ),
                        "evidence_artifact_identifier": artifact_identifier,
                        "method": (
                            f"{document.get('solvent_model')}/"
                            f"{document.get('reference_method')}/"
                            f"{document.get('basis_set')}"
                        ),
                    }
                )
        elif reference.artifact_type == "scientific_verification_report":
            verification_artifacts.append(artifact_identifier)
            outcome = _string(document.get("overall_outcome"))
            overlays.append(
                {
                    "overlay_identifier": f"overlay-{artifact_identifier}",
                    "kind": "verification_indicator",
                    "label": str(document.get("objective_family", "Scientific verification")).replace("_", " "),
                    "structure_artifact_identifier": source_structure(reference),
                    "candidate_identifier": None,
                    "value": outcome or bool(reference.metadata.get("passed")),
                    "unit": None,
                    "atom_identifiers": [],
                    "residue_identifiers": [],
                    "verification_status": outcome or (
                        "verified" if reference.metadata.get("passed") is True else "failed"
                    ),
                    "uncertainty": document.get("uncertainty"),
                    "evidence_artifact_identifier": artifact_identifier,
                    "method": reference.provenance.producer,
                }
            )
        elif reference.artifact_type == "electronic_qm_region_preparation":
            structure_identifier = source_structure(reference)
            source_atoms = structure_atom_ids.get(structure_identifier or "", [])
            raw_qm_indices = document.get("selected_particle_indices")
            qm_indices = (
                [item for item in raw_qm_indices if isinstance(item, int)]
                if isinstance(raw_qm_indices, list)
                else []
            )
            qm_atoms = [
                source_atoms[index]
                for index in qm_indices
                if 0 <= index < len(source_atoms)
            ]
            mm_atoms = [
                atom for index, atom in enumerate(source_atoms) if index not in set(qm_indices)
            ]
            for kind, label, atom_ids in (
                ("qm_region", "QM region", qm_atoms),
                ("mm_region", "MM environment", mm_atoms),
            ):
                overlays.append(
                    {
                        "overlay_identifier": f"overlay-{kind}-{artifact_identifier}",
                        "kind": kind,
                        "label": label,
                        "structure_artifact_identifier": structure_identifier,
                        "candidate_identifier": None,
                        "value": len(atom_ids),
                        "unit": "atoms",
                        "atom_identifiers": atom_ids,
                        "residue_identifiers": [],
                        "verification_status": "computed",
                        "uncertainty": None,
                        "evidence_artifact_identifier": artifact_identifier,
                        "method": document.get("selection_policy"),
                    }
                )
        elif reference.artifact_type == "molecular_simulation_system":
            structure_identifier = source_structure(reference)
            source_atoms = structure_atom_ids.get(structure_identifier or "", [])
            raw_charges = document.get("particle_partial_charges_e")
            if isinstance(raw_charges, list):
                for index, value in enumerate(raw_charges):
                    if index >= len(source_atoms) or not isinstance(value, (int, float)):
                        continue
                    overlays.append(
                        {
                            "overlay_identifier": f"overlay-charge-{artifact_identifier}-{index}",
                            "kind": "partial_charge",
                            "label": "Partial charge",
                            "structure_artifact_identifier": structure_identifier,
                            "candidate_identifier": None,
                            "value": value,
                            "unit": "e",
                            "atom_identifiers": [source_atoms[index]],
                            "residue_identifiers": [],
                            "verification_status": "computed",
                            "uncertainty": None,
                            "evidence_artifact_identifier": artifact_identifier,
                            "method": document.get("partial_charge_source"),
                        }
                    )
        elif reference.artifact_type in {
            "molecular_trajectory",
            "electronic_reaction_path_result",
            "electronic_transition_state_search",
            "potential_energy_curve",
        }:
            structure_identifier = source_structure(reference)
            raw_frames = document.get("snapshots")
            raw_points = document.get("points")
            count = (
                len(raw_frames)
                if isinstance(raw_frames, list)
                else len(raw_points)
                if isinstance(raw_points, list)
                else document.get("point_count", document.get("frame_count"))
            )
            overlays.append(
                {
                    "overlay_identifier": f"overlay-{artifact_identifier}",
                    "kind": (
                        "trajectory"
                        if reference.artifact_type == "molecular_trajectory"
                        else "reaction_or_displacement_path"
                    ),
                    "label": reference.artifact_type.replace("_", " "),
                    "structure_artifact_identifier": structure_identifier,
                    "candidate_identifier": None,
                    "value": count,
                    "unit": "frames" if reference.artifact_type == "molecular_trajectory" else "points",
                    "atom_identifiers": [],
                    "residue_identifiers": [],
                    "verification_status": "computed",
                    "uncertainty": document.get("uncertainty"),
                    "evidence_artifact_identifier": artifact_identifier,
                    "method": reference.provenance.producer,
                }
            )

    candidates = sorted(
        candidate_map.values(),
        key=lambda item: (
            int(item.get("generation", 0)),
            str(item.get("candidate_identifier", "")),
        ),
    )
    comparisons: list[dict[str, object]] = []
    for candidate in candidates:
        identifier = str(candidate["candidate_identifier"])
        for parent in candidate["parent_candidate_identifiers"]:
            comparisons.append(
                {
                    "comparison_identifier": f"comparison-{parent}-{identifier}",
                    "kind": "before_after_transformation",
                    "left_identifier": parent,
                    "right_identifier": identifier,
                    "differences": candidate.get("transformation"),
                    "evidence_artifact_identifier": (
                        trace_reference.artifact_identifier
                        if trace_reference is not None
                        else None
                    ),
                }
            )
    for left_position, left in enumerate(structures):
        for right in structures[left_position + 1 :]:
            if left["role"] != right["role"]:
                continue
            role = str(left["role"])
            comparisons.append(
                {
                    "comparison_identifier": (
                        f"comparison-{left['artifact_identifier']}-"
                        f"{right['artifact_identifier']}"
                    ),
                    "kind": (
                        "protein_pocket_comparison"
                        if role == "protein"
                        else "pose_or_structure_comparison"
                    ),
                    "left_identifier": left["artifact_identifier"],
                    "right_identifier": right["artifact_identifier"],
                    "differences": {
                        "left_content_sha256": left["content_sha256"],
                        "right_content_sha256": right["content_sha256"],
                    },
                    "evidence_artifact_identifier": left["artifact_identifier"],
                }
            )
    for left_position, left in enumerate(overlays):
        for right in overlays[left_position + 1 :]:
            if (
                left.get("kind") != right.get("kind")
                or left.get("method") == right.get("method")
            ):
                continue
            left_target = left.get("candidate_identifier") or left.get(
                "structure_artifact_identifier"
            )
            right_target = right.get("candidate_identifier") or right.get(
                "structure_artifact_identifier"
            )
            if not isinstance(left_target, str) or not isinstance(right_target, str):
                continue
            comparisons.append(
                {
                    "comparison_identifier": (
                        f"comparison-method-{left['overlay_identifier']}-"
                        f"{right['overlay_identifier']}"
                    ),
                    "kind": "computational_method_comparison",
                    "left_identifier": left_target,
                    "right_identifier": right_target,
                    "differences": {
                        "left_method": left.get("method"),
                        "right_method": right.get("method"),
                        "left_value": left.get("value"),
                        "right_value": right.get("value"),
                    },
                    "evidence_artifact_identifier": left.get(
                        "evidence_artifact_identifier"
                    ),
                }
            )

    export_items = [
        {
            "artifact_identifier": reference.artifact_identifier,
            "artifact_type": reference.artifact_type,
            "media_type": reference.media_type,
            "content_sha256": reference.content_sha256,
            "category": (
                "structure"
                if visualizable_research_artifact(reference)
                else "report"
                if reference.artifact_type in _REPORT_ARTIFACT_TYPES
                else "evidence"
            ),
        }
        for reference in all_references
    ]
    return {
        "schema_version": "pulsate.research-visualization/v1",
        "session_identifier": session.session_identifier,
        "revision": session.revision,
        "scene_identifier": session.scene_identifier,
        "structures": structures,
        "selections": selections,
        "interactions": interactions,
        "overlays": overlays,
        "candidates": candidates,
        "lineage": trace.get("lineage", []),
        "comparisons": comparisons,
        "verification_artifact_identifiers": verification_artifacts,
        "export_items": export_items,
        "grounding_policy": "persisted_artifact_or_deterministic_computation_only",
    }


__all__ = [
    "ResearchVisualizationError",
    "build_research_visualization",
]
