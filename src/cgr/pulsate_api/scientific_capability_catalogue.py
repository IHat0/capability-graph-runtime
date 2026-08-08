"""Artifact-driven capability composition for scientist objectives.

The catalogue is deliberately declarative.  Objective interpretation chooses
scientific goals; a bounded backward search then selects registered capability
providers from their artifact contracts.  Adding a provider therefore does not
require adding another hard-coded workflow branch.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cgr.science.canonical import validate_identifier


class ScientificCapabilityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_name: str
    accepted_artifact_types: tuple[str, ...] = ()
    produced_artifact_types: tuple[str, ...] = Field(min_length=1)
    supported_task_types: tuple[str, ...] = ()
    verification_required: bool = False
    authorization_required: bool = False
    priority: int = Field(default=100, ge=0, le=10_000)

    @field_validator("capability_name")
    @classmethod
    def capability_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="scientific capability name")

    @field_validator(
        "accepted_artifact_types", "produced_artifact_types", "supported_task_types"
    )
    @classmethod
    def identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(validate_identifier(item) for item in value)
        if len(normalized) != len(set(normalized)):
            raise ValueError("Scientific capability declarations must be unique.")
        return normalized

    @model_validator(mode="after")
    def authorization_is_explicit(self) -> Self:
        if self.authorization_required and "authorization_request" not in self.produced_artifact_types:
            raise ValueError("Authorization capabilities must produce an authorization request.")
        return self


class ScientificCapabilityCompositionError(ValueError):
    """Raised when required scientific artifacts cannot be composed safely."""


class ScientificCapabilityCatalogue:
    """Immutable provider index with deterministic bounded backward planning."""

    def __init__(self, definitions: Iterable[ScientificCapabilityDefinition]) -> None:
        values = tuple(definitions)
        names = [item.capability_name for item in values]
        if len(names) != len(set(names)):
            raise ValueError("Scientific capability names must be unique.")
        self.definitions = tuple(sorted(values, key=lambda item: item.capability_name))

    def compose(
        self,
        *,
        task_type: str,
        available_artifact_types: tuple[str, ...],
        required_artifact_types: tuple[str, ...],
    ) -> tuple[ScientificCapabilityDefinition, ...]:
        available = set(available_artifact_types)
        selected: dict[str, ScientificCapabilityDefinition] = {}
        visiting: set[str] = set()

        def provide(artifact_type: str) -> None:
            if artifact_type in available:
                return
            if artifact_type in visiting:
                raise ScientificCapabilityCompositionError(
                    "Scientific capability dependencies contain a cycle."
                )
            candidates = tuple(
                definition
                for definition in self.definitions
                if artifact_type in definition.produced_artifact_types
                and (
                    not definition.supported_task_types
                    or task_type in definition.supported_task_types
                )
            )
            if not candidates:
                raise ScientificCapabilityCompositionError(
                    f"No registered capability produces {artifact_type}."
                )
            provider = min(candidates, key=lambda item: (item.priority, item.capability_name))
            visiting.add(artifact_type)
            for required in provider.accepted_artifact_types:
                provide(required)
            visiting.remove(artifact_type)
            selected[provider.capability_name] = provider
            available.update(provider.produced_artifact_types)

        for required in required_artifact_types:
            provide(required)

        ordered: list[ScientificCapabilityDefinition] = []
        remaining = dict(selected)
        produced = set(available_artifact_types)
        while remaining:
            ready = tuple(
                definition
                for definition in remaining.values()
                if set(definition.accepted_artifact_types).issubset(produced)
            )
            if not ready:
                raise ScientificCapabilityCompositionError(
                    "Scientific capability dependencies could not be ordered."
                )
            for definition in sorted(ready, key=lambda item: (item.priority, item.capability_name)):
                ordered.append(definition)
                produced.update(definition.produced_artifact_types)
                remaining.pop(definition.capability_name)
        return tuple(ordered)


_TS = ("covalent_transition_state",)
_METAL = ("metal_active_site_quantum",)
_PES = ("bond_dissociation_scan",)
_CONFORMER = ("solvated_conformer_comparison",)
_DISCOVERY = ("protein_ligand_discovery",)


def phase8_scientific_capability_catalogue() -> ScientificCapabilityCatalogue:
    """Return the production Phase 8 artifact contract catalogue."""

    definitions = (
        ScientificCapabilityDefinition(
            capability_name="molecular.structure_ingestion",
            accepted_artifact_types=("scientist_input",),
            produced_artifact_types=("molecular_structure",),
            priority=10,
        ),
        ScientificCapabilityDefinition(
            capability_name="molecular.protein_protonation_prepare",
            accepted_artifact_types=("molecular_structure",),
            produced_artifact_types=("prepared_protein", "protonation_state_evidence"),
            supported_task_types=(*_TS, *_METAL, *_DISCOVERY),
            verification_required=True,
            priority=20,
        ),
        ScientificCapabilityDefinition(
            capability_name="molecular.protonation_prepare",
            accepted_artifact_types=("molecular_structure",),
            produced_artifact_types=("prepared_ligand", "ligand_charge_state_evidence"),
            supported_task_types=_TS,
            verification_required=True,
            priority=20,
        ),
        ScientificCapabilityDefinition(
            capability_name="molecular.semantic_target_resolve",
            accepted_artifact_types=("molecular_structure",),
            produced_artifact_types=("semantic_target_selection",),
            priority=25,
        ),
        ScientificCapabilityDefinition(
            capability_name="electronic.qm_region_prepare",
            accepted_artifact_types=("prepared_protein", "semantic_target_selection"),
            produced_artifact_types=("electronic_qm_region_preparation", "charge_spin_alternatives"),
            supported_task_types=(*_TS, *_METAL),
            verification_required=True,
            priority=30,
        ),
        ScientificCapabilityDefinition(
            capability_name="electronic.qmmm_embedding_prepare",
            accepted_artifact_types=("electronic_qm_region_preparation",),
            produced_artifact_types=("qmmm_embedding_foundation",),
            supported_task_types=_TS,
            priority=40,
        ),
        ScientificCapabilityDefinition(
            capability_name="electronic.active_space_select",
            accepted_artifact_types=("electronic_qm_region_preparation", "semantic_target_selection"),
            produced_artifact_types=("electronic_active_space_selection",),
            supported_task_types=(*_TS, *_METAL),
            verification_required=True,
            priority=45,
        ),
        ScientificCapabilityDefinition(
            capability_name="electronic.active_space_select",
            accepted_artifact_types=("molecular_structure", "semantic_target_selection"),
            produced_artifact_types=("electronic_active_space_selection",),
            supported_task_types=_PES,
            verification_required=True,
            priority=45,
        ),
        ScientificCapabilityDefinition(
            capability_name="electronic.active_space_construct",
            accepted_artifact_types=("electronic_active_space_selection",),
            produced_artifact_types=("electronic_active_space",),
            supported_task_types=(*_TS, *_METAL, *_PES),
            priority=50,
        ),
        ScientificCapabilityDefinition(
            capability_name="electronic.qmmm_hybrid_execute",
            accepted_artifact_types=("qmmm_embedding_foundation", "electronic_active_space"),
            produced_artifact_types=("qmmm_hybrid_energy_gradient",),
            supported_task_types=_TS,
            verification_required=True,
            priority=55,
        ),
        ScientificCapabilityDefinition(
            capability_name="electronic.transition_state_initial_path",
            accepted_artifact_types=("qmmm_hybrid_energy_gradient", "semantic_target_selection"),
            produced_artifact_types=("transition_state_initial_path",),
            supported_task_types=_TS,
            priority=60,
        ),
        ScientificCapabilityDefinition(
            capability_name="electronic.transition_state_search",
            accepted_artifact_types=("transition_state_initial_path",),
            produced_artifact_types=("electronic_transition_state_search",),
            supported_task_types=_TS,
            priority=65,
        ),
        ScientificCapabilityDefinition(
            capability_name="electronic.frequency_analyze",
            accepted_artifact_types=("electronic_transition_state_search",),
            produced_artifact_types=("electronic_frequency_analysis",),
            supported_task_types=_TS,
            verification_required=True,
            priority=70,
        ),
        ScientificCapabilityDefinition(
            capability_name="electronic.reaction_path_confirm",
            accepted_artifact_types=("electronic_frequency_analysis", "semantic_target_selection"),
            produced_artifact_types=("electronic_reaction_path_result",),
            supported_task_types=_TS,
            verification_required=True,
            priority=75,
        ),
        ScientificCapabilityDefinition(
            capability_name="scientific_verification.transition_state",
            accepted_artifact_types=("electronic_reaction_path_result", "electronic_frequency_analysis"),
            produced_artifact_types=("scientific_verification_report",),
            supported_task_types=_TS,
            verification_required=True,
            priority=80,
        ),
        ScientificCapabilityDefinition(
            capability_name="quantum_workflow.fermionic_hamiltonian_map",
            accepted_artifact_types=("electronic_active_space",),
            produced_artifact_types=("mapped_qubit_hamiltonian",),
            supported_task_types=_METAL,
            priority=60,
        ),
        ScientificCapabilityDefinition(
            capability_name="quantum_workflow.vqe",
            accepted_artifact_types=("mapped_qubit_hamiltonian",),
            produced_artifact_types=("variational_ground_state_result",),
            supported_task_types=_METAL,
            verification_required=True,
            priority=70,
        ),
        ScientificCapabilityDefinition(
            capability_name="scientific_verification.metal_centre",
            accepted_artifact_types=("variational_ground_state_result", "charge_spin_alternatives"),
            produced_artifact_types=("scientific_verification_report",),
            supported_task_types=_METAL,
            verification_required=True,
            priority=80,
        ),
        ScientificCapabilityDefinition(
            capability_name="quantum.ibm_execution_prepare",
            accepted_artifact_types=("mapped_qubit_hamiltonian",),
            produced_artifact_types=("authorization_request",),
            supported_task_types=_METAL,
            authorization_required=True,
            # Keep this terminal handoff after the scientist-facing local result.
            # It prepares an authorization request and never submits a job.
            priority=110,
        ),
        ScientificCapabilityDefinition(
            capability_name="molecular.constrained_distance_scan",
            accepted_artifact_types=("molecular_structure", "semantic_target_selection"),
            produced_artifact_types=("molecular_distance_scan",),
            supported_task_types=_PES,
            verification_required=True,
            priority=30,
        ),
        ScientificCapabilityDefinition(
            capability_name="electronic.bond_dissociation_pes_execute",
            accepted_artifact_types=("molecular_distance_scan", "electronic_active_space"),
            produced_artifact_types=("potential_energy_curve",),
            supported_task_types=_PES,
            priority=60,
        ),
        ScientificCapabilityDefinition(
            capability_name="scientific_verification.potential_energy_curve",
            accepted_artifact_types=("potential_energy_curve",),
            produced_artifact_types=("scientific_verification_report",),
            supported_task_types=_PES,
            verification_required=True,
            priority=80,
        ),
        ScientificCapabilityDefinition(
            capability_name="molecular.conformer_generate",
            accepted_artifact_types=("molecular_structure",),
            produced_artifact_types=("molecular_conformer_set",),
            supported_task_types=_CONFORMER,
            priority=30,
        ),
        ScientificCapabilityDefinition(
            capability_name="electronic.implicit_solvent_hartree_fock",
            accepted_artifact_types=("molecular_conformer_set",),
            produced_artifact_types=("electronic_implicit_solvent_result",),
            supported_task_types=_CONFORMER,
            priority=60,
        ),
        ScientificCapabilityDefinition(
            capability_name="scientific_verification.conformer_comparison",
            accepted_artifact_types=("electronic_implicit_solvent_result",),
            produced_artifact_types=("scientific_verification_report",),
            supported_task_types=_CONFORMER,
            verification_required=True,
            priority=80,
        ),
        ScientificCapabilityDefinition(
            capability_name="molecular.binding_pocket_resolve",
            accepted_artifact_types=("prepared_protein", "semantic_target_selection"),
            produced_artifact_types=("binding_pocket",),
            supported_task_types=_DISCOVERY,
            priority=30,
        ),
        ScientificCapabilityDefinition(
            capability_name="discovery.campaign_initialize",
            accepted_artifact_types=("binding_pocket",),
            produced_artifact_types=("discovery_campaign",),
            supported_task_types=_DISCOVERY,
            priority=40,
        ),
        ScientificCapabilityDefinition(
            capability_name="molecular.conformer_generate",
            accepted_artifact_types=("discovery_campaign",),
            produced_artifact_types=("molecular_conformer_set",),
            supported_task_types=_DISCOVERY,
            priority=45,
        ),
        ScientificCapabilityDefinition(
            capability_name="molecular.ligand_parameterize",
            accepted_artifact_types=("molecular_conformer_set",),
            produced_artifact_types=("molecular_ligand_parameterization",),
            supported_task_types=_DISCOVERY,
            verification_required=True,
            priority=50,
        ),
        ScientificCapabilityDefinition(
            capability_name="molecular.ligand_openmm_system_construct",
            accepted_artifact_types=(
                "molecular_ligand_parameterization",
                "molecular_conformer_set",
            ),
            produced_artifact_types=(
                "molecular_environment",
                "molecular_simulation_system",
            ),
            supported_task_types=_DISCOVERY,
            verification_required=True,
            priority=55,
        ),
        ScientificCapabilityDefinition(
            capability_name="molecular.docking_pose_generate",
            accepted_artifact_types=("molecular_simulation_system", "binding_pocket"),
            produced_artifact_types=("molecular_docking_result", "docking_pose_pdbqt"),
            supported_task_types=_DISCOVERY,
            verification_required=True,
            priority=60,
        ),
        ScientificCapabilityDefinition(
            capability_name="discovery.campaign_iterate",
            accepted_artifact_types=("discovery_campaign", "molecular_docking_result"),
            produced_artifact_types=("discovery_campaign_result", "discovery_campaign_checkpoint"),
            supported_task_types=_DISCOVERY,
            verification_required=True,
            priority=70,
        ),
        ScientificCapabilityDefinition(
            capability_name="scientific_verification.candidate_ranking",
            accepted_artifact_types=("discovery_campaign_result",),
            produced_artifact_types=("scientific_verification_report",),
            supported_task_types=_DISCOVERY,
            verification_required=True,
            priority=80,
        ),
        ScientificCapabilityDefinition(
            capability_name="molecular.scene_project",
            accepted_artifact_types=("scientific_verification_report", "molecular_structure"),
            produced_artifact_types=("molecular_scene_state",),
            priority=90,
        ),
        ScientificCapabilityDefinition(
            capability_name="scientist.result_assemble",
            accepted_artifact_types=("scientific_verification_report", "molecular_scene_state"),
            produced_artifact_types=("scientist_facing_result",),
            priority=100,
        ),
    )
    # One capability identity may have different task-specific artifact contracts.
    # Collapse only exact duplicate identities by assigning stable internal aliases.
    expanded: list[ScientificCapabilityDefinition] = []
    counts: dict[str, int] = {}
    for definition in definitions:
        count = counts.get(definition.capability_name, 0)
        counts[definition.capability_name] = count + 1
        if count:
            definition = definition.model_copy(
                update={"capability_name": f"{definition.capability_name}.route-{count + 1}"}
            )
        expanded.append(definition)
    return ScientificCapabilityCatalogue(expanded)


def public_capability_name(name: str) -> str:
    """Remove the internal route suffix used for task-specific contracts."""

    return name.split(".route-", 1)[0]
