from __future__ import annotations

from cgr.pulsate_api.scientific_objectives import (
    compile_scientific_objective,
    plan_scientific_objective,
)
from cgr.pulsate_api.scientific_requirements import (
    ScientificRequirement,
    ScientificRequirementProposal,
    validate_requirement_proposal,
)


def _requirements():
    return validate_requirement_proposal(
        ScientificRequirementProposal(
            proposal_identifier="requirement-proposal-protein-design",
            requirements=(
                ScientificRequirement(
                    operation="generate_protein_candidates",
                    requested_output="generated_protein_candidates",
                    supporting_quote="Generate a de novo protein",
                ),
            ),
            summary="Generate computational protein candidates.",
            provider_kind="controlled_provider",
            model_name="replaceable-model",
        ),
        available_input_types=(),
    )


def test_protein_design_requirement_composes_the_three_engine_pipeline() -> None:
    requirements = _requirements()
    objective = compile_scientific_objective(
        "Generate a de novo protein with exactly 48 residues.",
        research_requirements=requirements,
    )
    plan = plan_scientific_objective(objective)

    assert requirements.capability_profile == "de_novo_protein_design"
    assert objective.semantic_target.target_kind == "designed_protein"
    assert objective.clarification_required is False
    assert plan.executable is True
    capabilities = tuple(item.capability_name for item in plan.steps)
    assert capabilities == (
        "protein.design_specification",
        "protein.backbone_generate",
        "protein.sequence_design",
        "protein.structure_predict",
        "scientific_verification.protein_design",
        "molecular.scene_project",
        "scientist.result_assemble",
    )


def test_protein_design_asks_for_length_only_when_it_is_missing() -> None:
    objective = compile_scientific_objective(
        "Generate a de novo protein candidate.",
        research_requirements=_requirements(),
    )
    plan = plan_scientific_objective(objective)

    assert objective.clarification_required is True
    assert objective.ambiguity_hypotheses == (
        "An exact protein length in residues is required for de novo backbone generation.",
    )
    assert plan.executable is False


def test_protein_design_rejects_conflicting_or_out_of_range_lengths() -> None:
    conflicting = compile_scientific_objective(
        "Generate a 48-residue candidate and a 72-residue candidate.",
        research_requirements=_requirements(),
    )
    out_of_range = compile_scientific_objective(
        "Generate a 9-residue candidate.",
        research_requirements=_requirements(),
    )

    assert conflicting.clarification_required is True
    assert "Multiple different protein lengths" in conflicting.ambiguity_hypotheses[0]
    assert out_of_range.clarification_required is True
    assert "between 20 and 2000" in out_of_range.ambiguity_hypotheses[0]
