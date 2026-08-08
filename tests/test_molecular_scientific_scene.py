"""Phase 8 scientist-facing molecular scene state regressions."""

import pytest

from cgr.molecular import (
    MolecularScientificSceneState,
    ScientificSceneCandidateLineage,
    ScientificScenePose,
    ScientificSceneSelection,
)


def test_scene_represents_regions_poses_lineage_and_computation_state() -> None:
    scene = MolecularScientificSceneState(
        scene_identifier="scene-discovery-result",
        execution_identifier="scientific-execution-0123456789abcdef0123456789abcdef",
        task_type="protein_ligand_discovery",
        structure_identifiers=("protein-structure", "candidate-structure"),
        selections=(
            ScientificSceneSelection(
                selection_identifier="selection-pocket",
                role="pocket",
                structure_identifier="protein-structure",
                residue_identifiers=("residue-a-10", "residue-a-11"),
            ),
            ScientificSceneSelection(
                selection_identifier="selection-pose",
                role="docking_pose",
                structure_identifier="candidate-structure",
                atom_identifiers=("candidate-atom-1", "candidate-atom-2"),
            ),
        ),
        docking_poses=(ScientificScenePose(
            pose_identifier="pose-candidate-1",
            candidate_identifier="candidate-1",
            rank=1,
            vina_score_kcal_per_mol=-7.2,
            pose_artifact_identifier="artifact-pose-1",
            atom_mapping_embedded=True,
        ),),
        candidate_lineage=(
            ScientificSceneCandidateLineage(
                candidate_identifier="seed-1",
                generation=0,
                status="verified",
            ),
            ScientificSceneCandidateLineage(
                candidate_identifier="candidate-1",
                parent_candidate_identifiers=("seed-1",),
                generation=1,
                selected=True,
                status="ranked",
            ),
        ),
        computational_state="succeeded",
        result_state="verified",
    )

    assert scene.docking_poses[0].vina_score_kcal_per_mol == -7.2
    assert scene.candidate_lineage[1].parent_candidate_identifiers == ("seed-1",)


def test_scene_rejects_unresolved_candidate_parent() -> None:
    with pytest.raises(ValueError, match="parents must resolve"):
        MolecularScientificSceneState(
            scene_identifier="scene-invalid",
            execution_identifier="scientific-execution-0123456789abcdef0123456789abcdef",
            task_type="protein_ligand_discovery",
            structure_identifiers=("protein-structure",),
            candidate_lineage=(ScientificSceneCandidateLineage(
                candidate_identifier="candidate-1",
                parent_candidate_identifiers=("missing-parent",),
                generation=1,
                status="generated",
            ),),
            computational_state="running",
            result_state="pending",
        )
