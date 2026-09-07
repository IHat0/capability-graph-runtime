"""Provider-neutral research requirements and deterministic capability composition."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cgr.science.canonical import validate_identifier

from .natural_language import NaturalLanguageModelProvider

ScientificResearchOperation = Literal[
    "analyze_structure",
    "generate_protein_candidates",
    "identify_binding_regions",
    "compare_structures",
    "generate_candidates",
    "dock_candidates",
    "refine_promising_candidates",
    "calculate_vqe_ground_state_energy",
    "scan_parameterized_ground_state_energy",
    "estimate_relevant_quantities",
    "compare_computational_methods",
    "verify_and_rank_results",
    "design_next_generation",
]

ScientificRequestedOutput = Literal[
    "structure_analysis",
    "generated_protein_candidates",
    "binding_region_evidence",
    "structure_comparison",
    "generated_candidates",
    "docking_evidence",
    "refined_candidates",
    "variational_ground_state_energy",
    "parameter_sweep_ground_state_energies",
    "quantity_estimates",
    "method_comparison",
    "verified_candidate_ranking",
    "next_generation_design",
]

ScientificCapabilityProfile = Literal[
    "structure_analysis",
    "de_novo_protein_design",
    "covalent_transition_state",
    "metal_active_site_quantum",
    "bond_dissociation_scan",
    "solvated_conformer_comparison",
    "protein_ligand_discovery",
    "molecular_ground_state_vqe",
    "molecular_ground_state_vqe_sweep",
]


_OUTPUT_BY_OPERATION: dict[ScientificResearchOperation, ScientificRequestedOutput] = {
    "analyze_structure": "structure_analysis",
    "generate_protein_candidates": "generated_protein_candidates",
    "identify_binding_regions": "binding_region_evidence",
    "compare_structures": "structure_comparison",
    "generate_candidates": "generated_candidates",
    "dock_candidates": "docking_evidence",
    "refine_promising_candidates": "refined_candidates",
    "calculate_vqe_ground_state_energy": "variational_ground_state_energy",
    "scan_parameterized_ground_state_energy": "parameter_sweep_ground_state_energies",
    "estimate_relevant_quantities": "quantity_estimates",
    "compare_computational_methods": "method_comparison",
    "verify_and_rank_results": "verified_candidate_ranking",
    "design_next_generation": "next_generation_design",
}

_DISCOVERY_OPERATIONS = frozenset(
    {
        "identify_binding_regions",
        "generate_candidates",
        "dock_candidates",
        "refine_promising_candidates",
        "verify_and_rank_results",
        "design_next_generation",
        "estimate_relevant_quantities",
        "compare_computational_methods",
    }
)


class ScientificRequirement(BaseModel):
    """One model-proposed operation grounded in exact scientist text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: ScientificResearchOperation
    requested_output: ScientificRequestedOutput
    supporting_quote: str = Field(min_length=1, max_length=2048)

    @field_validator("supporting_quote")
    @classmethod
    def normalized_quote(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @model_validator(mode="after")
    def output_matches_operation(self) -> ScientificRequirement:
        if self.requested_output != _OUTPUT_BY_OPERATION[self.operation]:
            raise ValueError("The requested output does not match the research operation.")
        return self


class ScientificRequirementProposal(BaseModel):
    """A non-executable LLM proposal grounded in literal scientist text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_identifier: str
    requirements: tuple[ScientificRequirement, ...] = Field(min_length=1, max_length=16)
    summary: str = Field(min_length=1, max_length=1024)
    provider_kind: str
    model_name: str = Field(min_length=1, max_length=512)

    @field_validator("proposal_identifier", "provider_kind")
    @classmethod
    def identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="research requirement proposal")

    @field_validator("requirements")
    @classmethod
    def unique_operations(
        cls, value: tuple[ScientificRequirement, ...]
    ) -> tuple[ScientificRequirement, ...]:
        operations = [item.operation for item in value]
        if len(operations) != len(set(operations)):
            raise ValueError("Research requirement operations must be unique.")
        return value


class ValidatedResearchRequirements(BaseModel):
    """Quote-grounded requirements deterministically mapped to CGR outputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operations: tuple[ScientificResearchOperation, ...] = Field(min_length=1, max_length=16)
    requested_outputs: tuple[ScientificRequestedOutput, ...] = Field(
        min_length=1, max_length=16
    )
    required_artifact_types: tuple[str, ...] = Field(min_length=1, max_length=32)
    capability_profile: ScientificCapabilityProfile
    source_proposal_identifier: str

    @field_validator("source_proposal_identifier")
    @classmethod
    def proposal_identity(cls, value: str) -> str:
        return validate_identifier(value, label="research requirement proposal")


def validate_requirement_proposal(
    proposal: ScientificRequirementProposal,
    *,
    available_input_types: tuple[str, ...],
) -> ValidatedResearchRequirements:
    """Map quote-grounded semantic operations to registered artifact requirements."""

    operations = tuple(item.operation for item in proposal.requirements)
    outputs = tuple(item.requested_output for item in proposal.requirements)
    operation_set = set(operations)
    del available_input_types

    if operation_set == {"analyze_structure"}:
        profile: ScientificCapabilityProfile = "structure_analysis"
        required = [
            "molecular_structure_analysis",
            "scientific_verification_report",
        ]
    elif "scan_parameterized_ground_state_energy" in operation_set and operation_set <= {
        "scan_parameterized_ground_state_energy",
        "calculate_vqe_ground_state_energy",
        "compare_computational_methods",
    }:
        profile = "molecular_ground_state_vqe_sweep"
        required = [
            "ground_state_parameter_sweep_result",
            "multi_point_execution_receipt",
            "scientific_verification_report",
        ]
    elif "calculate_vqe_ground_state_energy" in operation_set and operation_set <= {
        "calculate_vqe_ground_state_energy",
        "compare_computational_methods",
    }:
        profile = "molecular_ground_state_vqe"
        required = [
            "variational_ground_state_result",
            "exact_diagonalization_result",
            "molecular_ground_state_execution_receipt",
            "scientific_verification_report",
        ]
    elif operation_set == {"generate_protein_candidates"}:
        profile = "de_novo_protein_design"
        required = [
            "protein_design_verification_report",
            "molecular_scene_state",
        ]
    elif operation_set == {"compare_structures"} or operation_set == {
        "compare_structures",
        "estimate_relevant_quantities",
    }:
        profile: ScientificCapabilityProfile = "solvated_conformer_comparison"
        required = ["scientific_verification_report"]
    elif operation_set and operation_set <= _DISCOVERY_OPERATIONS:
        profile: ScientificCapabilityProfile = "protein_ligand_discovery"
        required = [
            "discovery_design_loop_contract",
            "discovery_design_loop_trace",
        ]
        if "identify_binding_regions" in operation_set:
            required.append("binding_pocket")
        if "verify_and_rank_results" in operation_set:
            required.append("scientific_verification_report")
    else:
        raise ValueError(
            "No registered composition produces all requested outputs for these operations: "
            + ", ".join(sorted(operation_set))
            + ". The request was not reduced to a smaller workflow."
        )

    required.append("scientist_facing_result")
    return ValidatedResearchRequirements(
        operations=operations,
        requested_outputs=outputs,
        required_artifact_types=tuple(dict.fromkeys(required)),
        capability_profile=profile,
        source_proposal_identifier=proposal.proposal_identifier,
    )


class ProviderNeutralScientificRequirementInterpreter:
    """Let a replaceable model propose semantics while CGR controls composition."""

    def __init__(self, provider: NaturalLanguageModelProvider | None) -> None:
        self.provider = provider

    def capability(self) -> dict[str, object]:
        return {
            "available": self.provider is not None,
            "provider_kind": self.provider.provider_kind if self.provider else None,
            "model_name": self.provider.model_name if self.provider else None,
            "requires_exact_quotes": True,
            "requires_scientist_confirmation": False,
            "auto_acceptance_policy": (
                "literal_quotes_and_deterministic_capability_composition"
            ),
            "operations": tuple(_OUTPUT_BY_OPERATION),
        }

    def propose(self, scientist_text: str) -> ScientificRequirementProposal | None:
        if self.provider is None:
            return None
        try:
            content = self.provider.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "Translate the scientist request into bounded research operations. "
                            "Return exactly one JSON object with a requirements array, or {}. "
                            "Each item must contain only operation, requested_output, and "
                            "supporting_quote. operation must be one of: "
                            + ", ".join(_OUTPUT_BY_OPERATION)
                            + ". requested_output must be the matching value from this map: "
                            + json.dumps(_OUTPUT_BY_OPERATION, sort_keys=True)
                            + ". supporting_quote must be a literal non-empty substring of the "
                            "scientist request that supports that operation. Preserve an explicitly "
                            "stated VQE method, but never choose a method the scientist did not state. "
                            "Do not invent entities or values, authorize work, or add commentary. "
                            "Classify the requested outcome, not every prerequisite substep. "
                            "Comparing or prioritizing compounds for a protein target means "
                            "dock_candidates and verify_and_rank_results. compare_structures "
                            "means a structural/conformer comparison, not candidate prioritization. "
                            "analyze_structure means an inventory or descriptor report. "
                            "identify_binding_regions means locating a binding site. "
                            "generate_candidates and design_next_generation require an explicit "
                            "request to create NEW molecular identities, not evaluate supplied ones. "
                            "generate_protein_candidates requires creating NEW protein sequences; "
                            "the presence of a protein target never implies protein generation. "
                            "refine_promising_candidates requires additional optimization, not "
                            "merely choosing the most promising supplied compound. "
                            "Return {} when no operation is explicit."
                        ),
                    },
                    {"role": "user", "content": scientist_text},
                ]
            )
            parsed = json.loads(content)
            if set(parsed) != {"requirements"}:
                return None
            # Recover a unique literal source span when the model changes only case.
            # The persisted supporting quote remains scientist-authored text.
            for item in parsed["requirements"]:
                quote = item.get("supporting_quote")
                if isinstance(quote, str) and quote not in scientist_text:
                    matches = list(re.finditer(re.escape(quote), scientist_text, re.IGNORECASE))
                    if len(matches) != 1:
                        return None
                    item["supporting_quote"] = matches[0].group()
            requirements = tuple(
                ScientificRequirement.model_validate(item)
                for item in parsed["requirements"]
            )
            if not requirements or any(
                item.supporting_quote not in scientist_text for item in requirements
            ):
                return None
            canonical = json.dumps(
                [item.model_dump(mode="json") for item in requirements],
                sort_keys=True,
                separators=(",", ":"),
            )
            return ScientificRequirementProposal(
                proposal_identifier=(
                    "requirement-proposal-"
                    + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
                ),
                requirements=requirements,
                summary=(
                    "Pulsate proposed general research operations and requested outputs. "
                    "CGR will validate them deterministically before selecting capabilities."
                ),
                provider_kind=self.provider.provider_kind,
                model_name=self.provider.model_name,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, RuntimeError):
            return None


__all__ = [
    "ProviderNeutralScientificRequirementInterpreter",
    "ScientificCapabilityProfile",
    "ScientificRequestedOutput",
    "ScientificRequirement",
    "ScientificRequirementProposal",
    "ScientificResearchOperation",
    "ValidatedResearchRequirements",
    "validate_requirement_proposal",
]
