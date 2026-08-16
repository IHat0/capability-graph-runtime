"""General requirement interpretation and deterministic composition coverage."""

from __future__ import annotations

import json

from cgr.pulsate_api.scientific_objectives import (
    ScientificInputReference,
    compile_scientific_objective,
    plan_scientific_objective,
)
from cgr.pulsate_api.scientific_requirements import (
    ProviderNeutralScientificRequirementInterpreter,
    validate_requirement_proposal,
)


class _RequirementProvider:
    provider_kind = "controlled_test_provider"
    model_name = "replaceable-model"

    def __init__(self, requirements: list[dict[str, str]]) -> None:
        self.requirements = requirements

    def complete(self, _messages: list[dict[str, str]]) -> str:
        return json.dumps({"requirements": self.requirements})


def test_llm_proposes_only_exact_quote_grounded_general_requirements() -> None:
    question = "Generate candidates, dock candidates, verify and rank results."
    interpreter = ProviderNeutralScientificRequirementInterpreter(
        _RequirementProvider(
            [
                {
                    "operation": "generate_candidates",
                    "requested_output": "generated_candidates",
                    "supporting_quote": "Generate candidates",
                },
                {
                    "operation": "dock_candidates",
                    "requested_output": "docking_evidence",
                    "supporting_quote": "dock candidates",
                },
                {
                    "operation": "verify_and_rank_results",
                    "requested_output": "verified_candidate_ranking",
                    "supporting_quote": "verify and rank results",
                },
            ]
        )  # type: ignore[arg-type]
    )

    proposal = interpreter.propose(question)

    assert proposal is not None
    assert tuple(item.operation for item in proposal.requirements) == (
        "generate_candidates",
        "dock_candidates",
        "verify_and_rank_results",
    )


def test_ungrounded_or_mismatched_requirement_proposal_fails_closed() -> None:
    ungrounded = ProviderNeutralScientificRequirementInterpreter(
        _RequirementProvider(
            [
                {
                    "operation": "generate_candidates",
                    "requested_output": "generated_candidates",
                    "supporting_quote": "text the scientist never supplied",
                }
            ]
        )  # type: ignore[arg-type]
    )
    mismatched = ProviderNeutralScientificRequirementInterpreter(
        _RequirementProvider(
            [
                {
                    "operation": "generate_candidates",
                    "requested_output": "docking_evidence",
                    "supporting_quote": "Generate candidates",
                }
            ]
        )  # type: ignore[arg-type]
    )

    assert ungrounded.propose("Generate candidates.") is None
    assert mismatched.propose("Generate candidates.") is None


def test_confirmed_operations_compose_the_registered_discovery_loop() -> None:
    question = (
        "Identify binding regions, generate candidates, dock candidates, refine "
        "promising candidates, verify and rank results, and design next generation."
    )
    provider = _RequirementProvider(
        [
            {
                "operation": operation,
                "requested_output": output,
                "supporting_quote": quote,
            }
            for operation, output, quote in (
                (
                    "identify_binding_regions",
                    "binding_region_evidence",
                    "Identify binding regions",
                ),
                ("generate_candidates", "generated_candidates", "generate candidates"),
                ("dock_candidates", "docking_evidence", "dock candidates"),
                (
                    "refine_promising_candidates",
                    "refined_candidates",
                    "refine promising candidates",
                ),
                (
                    "verify_and_rank_results",
                    "verified_candidate_ranking",
                    "verify and rank results",
                ),
                (
                    "design_next_generation",
                    "next_generation_design",
                    "design next generation",
                ),
            )
        ]
    )
    proposal = ProviderNeutralScientificRequirementInterpreter(
        provider  # type: ignore[arg-type]
    ).propose(question)
    assert proposal is not None
    requirements = validate_requirement_proposal(
        proposal,
        available_input_types=("protein_structure",),
    )
    protein = ScientificInputReference(
        reference_identifier="input-protein",
        artifact_type="protein_structure",
        artifact_identifier="artifact-protein",
    )

    objective = compile_scientific_objective(
        question,
        input_references=(protein,),
        research_requirements=requirements,
    )
    plan = plan_scientific_objective(objective)

    capabilities = tuple(item.capability_name for item in plan.steps)
    assert objective.task_type == "composed_research"
    assert plan.executable
    assert "discovery.campaign_iterate" in capabilities
    assert "discovery.design_loop_bind" in capabilities
    assert "discovery.design_loop_trace" in capabilities
    assert "scientific_verification.candidate_ranking" in capabilities
    assert capabilities.index("discovery.design_loop_trace") < capabilities.index(
        "scientific_verification.candidate_ranking"
    )


def test_quantity_and_method_operations_use_registered_component_composition() -> None:
    question = "Estimate relevant quantities and compare computational methods."
    proposal = ProviderNeutralScientificRequirementInterpreter(
        _RequirementProvider(
            [
                {
                    "operation": "estimate_relevant_quantities",
                    "requested_output": "quantity_estimates",
                    "supporting_quote": "Estimate relevant quantities",
                },
                {
                    "operation": "compare_computational_methods",
                    "requested_output": "method_comparison",
                    "supporting_quote": "compare computational methods",
                },
            ]
        )  # type: ignore[arg-type]
    ).propose(question)

    assert proposal is not None
    requirements = validate_requirement_proposal(
        proposal,
        available_input_types=(),
    )

    assert requirements.capability_profile == "protein_ligand_discovery"
    assert "discovery_design_loop_contract" in requirements.required_artifact_types
    assert "discovery_design_loop_trace" in requirements.required_artifact_types


def test_confirmed_requirements_are_part_of_objective_identity() -> None:
    question = "Generate candidates and dock candidates."
    proposals = []
    for operation, output, quote in (
        ("generate_candidates", "generated_candidates", "Generate candidates"),
        ("dock_candidates", "docking_evidence", "dock candidates"),
    ):
        proposal = ProviderNeutralScientificRequirementInterpreter(
            _RequirementProvider(
                [
                    {
                        "operation": operation,
                        "requested_output": output,
                        "supporting_quote": quote,
                    }
                ]
            )  # type: ignore[arg-type]
        ).propose(question)
        assert proposal is not None
        proposals.append(
            validate_requirement_proposal(
                proposal,
                available_input_types=("protein_structure",),
            )
        )
    protein = ScientificInputReference(
        reference_identifier="input-protein",
        artifact_type="protein_structure",
        artifact_identifier="artifact-protein",
    )

    first = compile_scientific_objective(
        question,
        input_references=(protein,),
        research_requirements=proposals[0],
    )
    second = compile_scientific_objective(
        question,
        input_references=(protein,),
        research_requirements=proposals[1],
    )

    assert first.objective_identifier != second.objective_identifier


def test_general_structure_analysis_composes_a_real_verified_executor_path() -> None:
    question = "Analyze this structure."
    proposal = ProviderNeutralScientificRequirementInterpreter(
        _RequirementProvider(
            [
                {
                    "operation": "analyze_structure",
                    "requested_output": "structure_analysis",
                    "supporting_quote": "Analyze this structure",
                }
            ]
        )  # type: ignore[arg-type]
    ).propose(question)
    assert proposal is not None
    requirements = validate_requirement_proposal(
        proposal,
        available_input_types=("protein_structure",),
    )
    protein = ScientificInputReference(
        reference_identifier="input-protein",
        artifact_type="protein_structure",
        artifact_identifier="artifact-protein",
    )

    objective = compile_scientific_objective(
        question,
        input_references=(protein,),
        research_requirements=requirements,
    )
    plan = plan_scientific_objective(objective)

    assert requirements.capability_profile == "structure_analysis"
    assert plan.executable
    assert tuple(step.capability_name for step in plan.steps) == (
        "molecular.structure_ingestion",
        "molecular.structure_analyze",
        "scientific_verification.structure_analysis",
        "molecular.scene_project",
        "scientist.result_assemble",
    )
