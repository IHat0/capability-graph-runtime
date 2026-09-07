"""General requirement interpretation and deterministic composition coverage."""

from __future__ import annotations

import json
import math

import pytest

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


def test_case_only_model_quote_is_recovered_as_literal_scientist_text():
    question = "Prioritize these candidates."
    interpreter = ProviderNeutralScientificRequirementInterpreter(_RequirementProvider([{
        "operation": "verify_and_rank_results", "requested_output": "verified_candidate_ranking",
        "supporting_quote": "prioritize these candidates",
    }]))
    proposal = interpreter.propose(question)
    assert proposal is not None
    assert proposal.requirements[0].supporting_quote == "Prioritize these candidates"


def test_case_recovery_does_not_accept_an_invented_quote():
    interpreter = ProviderNeutralScientificRequirementInterpreter(_RequirementProvider([{
        "operation": "verify_and_rank_results", "requested_output": "verified_candidate_ranking",
        "supporting_quote": "prioritize unrelated molecules",
    }]))
    assert interpreter.propose("Prioritize these candidates.") is None


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

def test_vqe_ground_state_energy_uses_generic_molecular_composition() -> None:
    question = "What is the VQE energy of Lithium Hydride with a bond length of 1.6 \u00c5?"
    proposal = ProviderNeutralScientificRequirementInterpreter(
        _RequirementProvider(
            [
                {
                    "operation": "calculate_vqe_ground_state_energy",
                    "requested_output": "variational_ground_state_energy",
                    "supporting_quote": "VQE energy",
                }
            ]
        )  # type: ignore[arg-type]
    ).propose(question)

    assert proposal is not None
    requirements = validate_requirement_proposal(
        proposal,
        available_input_types=(),
    )
    objective = compile_scientific_objective(
        question,
        research_requirements=requirements,
    )
    plan = plan_scientific_objective(objective)

    capabilities = tuple(step.capability_name for step in plan.steps)
    assert requirements.capability_profile == "molecular_ground_state_vqe"
    assert plan.executable
    assert "electronic.molecular_system_resolve" in capabilities
    assert "quantum.exact_diagonalize" in capabilities
    assert "quantum.vqe_execute" in capabilities
    assert "scientific_verification.molecular_ground_state" in capabilities
    assert "molecular.protein_protonation_prepare" not in capabilities
    assert "scientific_verification.metal_centre" not in capabilities


def test_generic_vqe_ibm_request_preserves_terminal_authorization_gate() -> None:
    question = (
        "Use VQE on IBM Quantum for Lithium Hydride with a bond length "
        "of 1.6 Angstrom."
    )
    proposal = ProviderNeutralScientificRequirementInterpreter(
        _RequirementProvider(
            [
                {
                    "operation": "calculate_vqe_ground_state_energy",
                    "requested_output": "variational_ground_state_energy",
                    "supporting_quote": "VQE on IBM Quantum",
                }
            ]
        )  # type: ignore[arg-type]
    ).propose(question)

    assert proposal is not None
    requirements = validate_requirement_proposal(
        proposal,
        available_input_types=(),
    )
    objective = compile_scientific_objective(
        question,
        research_requirements=requirements,
    )
    plan = plan_scientific_objective(objective)

    assert objective.quantum_execution_target == "ibm_quantum"
    assert not plan.executable
    assert not plan.ibm_job_submission_planned
    assert plan.steps[-1].capability_name == "quantum.ibm_execution_prepare"
    assert plan.steps[-1].authorization_required

_H2_SWEEP_ACCEPTANCE = (
    "Run a 5-point potential energy scan on molecular hydrogen (H2) across bond "
    "distances of 0.5 Å, 0.74 Å, 1.0 Å, 1.5 Å, and 2.0 Å. Compute the "
    "ground-state energy at each geometry point using VQE with a UCCSD ansatz, "
    "compare each against exact classical diagonalization, and generate an "
    "auditable multi-point execution receipt."
)

_H2O_ACCEPTANCE = (
    "Calculate the ground-state electronic energy of a neutral water molecule "
    "(H2O) with O–H bond lengths of 0.957 Å and an H–O–H bond angle of 104.5 "
    "degrees. Use an STO-3G basis set and Restricted Hartree–Fock as the "
    "reference. Freeze the oxygen 1s core orbital, then construct a 4-electron, "
    "4-orbital active space from the remaining valence orbitals. Map the "
    "active-space Hamiltonian using Jordan–Wigner, run a statevector VQE "
    "calculation with a UCCSD ansatz, compare the VQE result against exact "
    "diagonalization of the same active-space Hamiltonian, and report the RHF, "
    "active-space exact, and VQE energies separately with all frozen-core and "
    "inactive-orbital energy contributions identified."
)

_NH3_ACCEPTANCE = (
    "Compute the ground-state energy of neutral ammonia (NH3) at a symmetric "
    "trigonal-pyramidal geometry with N–H bond lengths of 1.012 Å and H–N–H "
    "bond angles of 106.7 degrees. Use an STO-3G basis and a Restricted "
    "Hartree–Fock reference. Construct a chemically valid 6-electron, 6-orbital "
    "valence active space, map the resulting fermionic Hamiltonian with the "
    "parity transformation, run a statevector VQE calculation using a UCCSD "
    "ansatz, and independently diagonalize the same active-space Hamiltonian "
    "exactly. Report the Hartree–Fock energy, exact active-space ground-state "
    "energy, VQE energy, VQE–exact error, qubit count before and after any valid "
    "symmetry reduction, convergence information, and an auditable record of "
    "every scientific assumption or derived parameter."
)


def _validated_objective(question: str, requirements: list[dict[str, str]]):
    proposal = ProviderNeutralScientificRequirementInterpreter(
        _RequirementProvider(requirements)  # type: ignore[arg-type]
    ).propose(question)
    assert proposal is not None
    validated = validate_requirement_proposal(proposal, available_input_types=())
    objective = compile_scientific_objective(
        question, research_requirements=validated
    )
    return validated, objective, plan_scientific_objective(objective)


def _angle_degrees(left, center, right) -> float:
    first = tuple(left[index] - center[index] for index in range(3))
    second = tuple(right[index] - center[index] for index in range(3))
    dot = sum(a * b for a, b in zip(first, second))
    first_norm = math.sqrt(sum(value * value for value in first))
    second_norm = math.sqrt(sum(value * value for value in second))
    return math.degrees(math.acos(dot / (first_norm * second_norm)))


def test_parameterized_ground_state_acceptance_is_generic_multi_point_composition() -> None:
    requirements, objective, plan = _validated_objective(
        _H2_SWEEP_ACCEPTANCE,
        [
            {
                "operation": "scan_parameterized_ground_state_energy",
                "requested_output": "parameter_sweep_ground_state_energies",
                "supporting_quote": "potential energy scan",
            },
            {
                "operation": "calculate_vqe_ground_state_energy",
                "requested_output": "variational_ground_state_energy",
                "supporting_quote": "VQE",
            },
        ],
    )
    specification = objective.molecular_ground_state_specification
    assert specification is not None
    assert requirements.capability_profile == "molecular_ground_state_vqe_sweep"
    assert specification.molecular_formula == "H2"
    assert specification.element_symbols == ("H", "H")
    assert tuple(point.parameter_value for point in specification.geometry_points) == (
        0.5, 0.74, 1.0, 1.5, 2.0,
    )
    assert specification.is_parameter_sweep
    assert specification.mapper == "jordan_wigner"
    assert specification.ansatz == "uccsd"
    assert "multi_point_execution_receipt" in specification.requested_outputs
    assert plan.executable
    assert tuple(step.capability_name for step in plan.steps) == (
        "electronic.molecular_parameter_sweep_resolve",
        "scientific_computation.molecular_ground_state_sweep",
        "scientific_verification.molecular_ground_state_sweep",
        "molecular.scene_project",
        "scientist.result_assemble",
    )


def test_polyatomic_ground_state_acceptance_preserves_explicit_controls() -> None:
    requirements, objective, plan = _validated_objective(
        _H2O_ACCEPTANCE,
        [
            {
                "operation": "calculate_vqe_ground_state_energy",
                "requested_output": "variational_ground_state_energy",
                "supporting_quote": "VQE",
            },
            {
                "operation": "compare_computational_methods",
                "requested_output": "method_comparison",
                "supporting_quote": "compare the VQE result",
            },
        ],
    )
    specification = objective.molecular_ground_state_specification
    assert specification is not None
    assert requirements.capability_profile == "molecular_ground_state_vqe"
    assert specification.molecular_formula == "H2O"
    assert specification.element_symbols == ("H", "H", "O")
    point = specification.geometry_points[0]
    assert tuple(atom.element_symbol for atom in point.atoms) == ("O", "H", "H")
    coordinates = tuple(
        (atom.x_angstrom, atom.y_angstrom, atom.z_angstrom) for atom in point.atoms
    )
    assert math.dist(coordinates[0], coordinates[1]) == pytest.approx(0.957)
    assert math.dist(coordinates[0], coordinates[2]) == pytest.approx(0.957)
    assert _angle_degrees(coordinates[1], coordinates[0], coordinates[2]) == pytest.approx(104.5)
    assert (specification.molecular_charge, specification.spin) == (0, 0)
    assert specification.basis_set == "sto-3g"
    assert specification.reference_method == "rhf"
    assert (specification.active_electron_count, specification.active_spatial_orbital_count) == (4, 4)
    assert specification.requested_frozen_core_orbital_count == 1
    assert specification.mapper == "jordan_wigner"
    assert specification.ansatz == "uccsd"
    assert specification.execution_model == "exact_statevector_expectation"
    assert "inactive_space_energy_contributions" in specification.requested_outputs
    assert plan.executable
    capabilities = tuple(step.capability_name for step in plan.steps)
    assert capabilities.index("electronic.hartree_fock") < capabilities.index("electronic.active_space_select")
    assert capabilities.index("quantum.exact_diagonalize") < capabilities.index("scientific_verification.molecular_ground_state")


def test_parity_mapper_acceptance_composes_without_losing_request() -> None:
    requirements, objective, plan = _validated_objective(
        _NH3_ACCEPTANCE,
        [
            {
                "operation": "calculate_vqe_ground_state_energy",
                "requested_output": "variational_ground_state_energy",
                "supporting_quote": "VQE",
            },
            {
                "operation": "compare_computational_methods",
                "requested_output": "method_comparison",
                "supporting_quote": "independently diagonalize",
            },
        ],
    )
    specification = objective.molecular_ground_state_specification
    assert specification is not None
    assert requirements.capability_profile == "molecular_ground_state_vqe"
    assert specification.molecular_formula == "NH3"
    assert specification.element_symbols == ("N", "H", "H", "H")
    point = specification.geometry_points[0]
    coordinates = tuple(
        (atom.x_angstrom, atom.y_angstrom, atom.z_angstrom) for atom in point.atoms
    )
    assert all(
        math.dist(coordinates[0], coordinates[index]) == pytest.approx(1.012)
        for index in (1, 2, 3)
    )
    assert _angle_degrees(coordinates[1], coordinates[0], coordinates[2]) == pytest.approx(106.7)
    assert (specification.active_electron_count, specification.active_spatial_orbital_count) == (6, 6)
    assert specification.mapper == "parity"
    assert specification.ansatz == "uccsd"
    assert plan.executable
    assert not objective.clarification_required
    assert "quantum.fermion_to_qubit_map" in tuple(
        step.capability_name for step in plan.steps
    )