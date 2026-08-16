"""Broad scientist-facing intent and deterministic planning tests."""

from __future__ import annotations

import pytest

from cgr.pulsate_api.scientific_objectives import (
    CovalentReactionTarget,
    ScientificInputReference,
    compile_scientific_objective,
    plan_scientific_objective,
)


def _covalent_target() -> CovalentReactionTarget:
    return CovalentReactionTarget(
        protein_chain_label="A",
        protein_residue_sequence="145",
        protein_residue_name="CYS",
        protein_atom_name="SG",
        protein_nucleophile_formal_charge=-1,
        ligand_reaction_smarts="[C:1]-[S:2]",
        selected_total_qm_charge=-1,
        selected_spin=0,
    )


def _input(identifier: str, kind: str) -> ScientificInputReference:
    return ScientificInputReference(
        reference_identifier=identifier,
        artifact_type=kind,
        artifact_identifier=f"artifact-{identifier}",
    )


@pytest.mark.parametrize(
    ("question", "inputs", "task", "required_capabilities"),
    (
        (
            "Determine the transition state for covalent bond formation between this ligand and the catalytic nucleophile in this protease and verify that it connects reactant and product.",
            (_input("protein-1", "protein_structure"), _input("ligand-1", "ligand_structure")),
            "covalent_transition_state",
            {"electronic.transition_state_search", "electronic.frequency_analyze", "electronic.reaction_path_confirm"},
        ),
        (
            "Study the metal-centred active site of this enzyme with an automatically selected active space and run the appropriate VQE workflow locally.",
            (_input("protein-1", "protein_structure"),),
            "metal_active_site_quantum",
            {"electronic.active_space_select", "quantum.vqe_execute"},
        ),
        (
            "Calculate a 15-point potential-energy scan for this bond from 1.2 Angstrom to 2.8 Angstrom and use an active space appropriate for bond breaking.",
            (_input("molecule-1", "molecular_structure"),),
            "bond_dissociation_scan",
            {"molecular.constrained_distance_scan", "electronic.active_space_select"},
        ),
        (
            "Which conformer is preferred in water: the axial or equatorial form?",
            (_input("molecule-1", "molecular_structure"),),
            "solvated_conformer_comparison",
            {"molecular.conformer_generate", "electronic.implicit_solvent_hartree_fock"},
        ),
        (
            "Discover and optimize a drug candidate for this protein.",
            (_input("protein-1", "protein_structure"),),
            "protein_ligand_discovery",
            {
                "molecular.binding_pocket_resolve",
                "discovery.campaign_initialize",
                "discovery.campaign_iterate",
                "molecular.scene_project",
            },
        ),
    ),
)
def test_phase8_requests_compile_to_validated_composed_plans(
    question: str,
    inputs: tuple[ScientificInputReference, ...],
    task: str,
    required_capabilities: set[str],
) -> None:
    objective = compile_scientific_objective(
        question,
        input_references=inputs,
        covalent_reaction_target=(
            _covalent_target() if task == "covalent_transition_state" else None
        ),
    )
    plan = plan_scientific_objective(objective)

    assert objective.task_type == task
    assert not objective.clarification_required
    assert plan.executable
    assert required_capabilities.issubset(
        {step.capability_name for step in plan.steps}
    )
    assert not plan.ibm_job_submission_planned
    assert all(
        "atom_index" not in step.model_dump_json()
        and "orbital_index" not in step.model_dump_json()
        for step in plan.steps
    )


def test_ibm_request_prepares_authorization_gate_and_never_plans_submission() -> None:
    objective = compile_scientific_objective(
        "Study the metal-centered active site of this protein using VQE on IBM Quantum.",
        input_references=(_input("protein-1", "protein_structure"),),
    )
    plan = plan_scientific_objective(objective)

    assert objective.quantum_execution_target == "ibm_quantum"
    assert not plan.executable
    assert not plan.ibm_job_submission_planned
    assert plan.steps[-1].authorization_required
    assert "authorization" in plan.blocking_reasons[-1]


def test_missing_semantic_structure_requires_clarification() -> None:
    objective = compile_scientific_objective(
        "Discover and optimize a drug candidate for this protein."
    )
    plan = plan_scientific_objective(objective)

    assert objective.clarification_required
    assert not plan.executable
    assert "protein" in plan.blocking_reasons[0]


def test_missing_scientific_controls_remain_absent_until_scientist_reply() -> None:
    conformer = compile_scientific_objective(
        "Which conformer is preferred: axial or equatorial?",
        input_references=(_input("molecule-1", "molecular_structure"),),
    )
    scan = compile_scientific_objective(
        "Calculate a bond dissociation scan for this bond.",
        input_references=(_input("molecule-1", "molecular_structure"),),
    )

    assert conformer.clarification_required
    assert conformer.solvent is None
    assert scan.clarification_required
    assert scan.scan_point_count is None
    assert scan.scan_start_angstrom is None
    assert scan.scan_end_angstrom is None


def test_covalent_execution_requires_reviewed_reaction_semantics() -> None:
    objective = compile_scientific_objective(
        "Find the covalent transition state for this protein ligand reaction.",
        input_references=(
            _input("protein-1", "protein_structure"),
            _input("ligand-1", "ligand_structure"),
        ),
    )
    plan = plan_scientific_objective(objective)

    assert objective.clarification_required
    assert not plan.executable
    assert any("reaction atom" in reason for reason in plan.blocking_reasons)


def test_internal_index_injection_is_rejected() -> None:
    with pytest.raises(ValueError, match="internal indices"):
        compile_scientific_objective(
            "Calculate a bond dissociation scan with atom_index=4 and atom_index=7."
        )
