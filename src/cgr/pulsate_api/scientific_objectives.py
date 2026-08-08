"""Validated broad molecular-science intent and capability-plan bridge.

This is deliberately separate from the narrow approved quantum-experiment
compiler.  Natural-language text is classified into a non-executable object;
only validated semantic targets and deterministic capability dependencies are
allowed into a plan.
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cgr.science.canonical import validate_identifier

ScientificTask = Literal[
    "covalent_transition_state",
    "metal_active_site_quantum",
    "bond_dissociation_scan",
    "solvated_conformer_comparison",
    "protein_ligand_discovery",
]


class ScientificInputReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reference_identifier: str
    artifact_type: Literal[
        "protein_structure", "ligand_structure", "molecular_structure",
        "prepared_receptor", "prepared_ligand",
    ]
    artifact_identifier: str

    @field_validator("reference_identifier", "artifact_identifier")
    @classmethod
    def identifier(cls, value: str) -> str:
        return validate_identifier(value)


class ScientificSemanticTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protein_reference_identifier: str | None = None
    ligand_reference_identifier: str | None = None
    target_kind: Literal[
        "catalytic_nucleophile", "metal_coordination_shell", "named_bond",
        "conformer_labels", "binding_pocket",
    ]
    target_label: str
    chain_label: str | None = None
    residue_label: str | None = None
    bond_label: str | None = None

    @field_validator("target_label", "chain_label", "residue_label", "bond_label")
    @classmethod
    def bounded_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or len(normalized) > 256:
            raise ValueError("Semantic target labels must be bounded nonblank text.")
        if re.search(r"\b(?:atom|orbital|particle)[_-]?index\b", normalized, re.I):
            raise ValueError("Scientist intent cannot inject internal computational indices.")
        return normalized


class ScientificExecutionBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    maximum_wall_time_seconds: int = Field(default=3600, gt=0, le=604800)
    maximum_candidates: int = Field(default=64, gt=0, le=10000)
    maximum_generations: int = Field(default=3, gt=0, le=100)
    maximum_replans: int = Field(default=2, ge=0, le=10)


class StructuredScientificObjective(BaseModel):
    """Non-executable scientist intent with explicit uncertainty and safety."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    objective_identifier: str
    original_request: str = Field(min_length=1, max_length=8192)
    task_type: ScientificTask
    input_references: tuple[ScientificInputReference, ...] = ()
    semantic_target: ScientificSemanticTarget
    solvent: str | None = None
    scan_point_count: int | None = Field(default=None, ge=3, le=101)
    scan_start_angstrom: float | None = Field(default=None, gt=0)
    scan_end_angstrom: float | None = Field(default=None, gt=0)
    active_space_policy: Literal[
        "reaction_center_automatic", "metal_ligand_automatic", "not_requested"
    ]
    quantum_execution_target: Literal["none", "local_simulator", "ibm_quantum"]
    ibm_submission_authorized: Literal[False] = False
    budget: ScientificExecutionBudget = Field(default_factory=ScientificExecutionBudget)
    assumptions: tuple[str, ...] = ()
    ambiguity_hypotheses: tuple[str, ...] = ()
    clarification_required: bool

    @field_validator("objective_identifier")
    @classmethod
    def identifier(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("solvent")
    @classmethod
    def solvent_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if value not in {"water", "acetonitrile", "methanol", "dmso"}:
            raise ValueError("Requested solvent is not in the audited solvent catalogue.")
        return value

    @model_validator(mode="after")
    def consistent(self) -> Self:
        scan_values = (self.scan_point_count, self.scan_start_angstrom, self.scan_end_angstrom)
        if self.task_type == "bond_dissociation_scan":
            if any(value is None for value in scan_values):
                raise ValueError("Bond scans require point count and both endpoints.")
            if self.scan_start_angstrom >= self.scan_end_angstrom:  # type: ignore[operator]
                raise ValueError("Bond-scan endpoints must increase.")
        elif any(value is not None for value in scan_values):
            raise ValueError("Only bond scans may carry scan controls.")
        if self.task_type == "solvated_conformer_comparison" and self.solvent is None:
            raise ValueError("Solvated conformer comparison requires a solvent.")
        return self


class ScientificWorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step_identifier: str
    capability_name: str
    depends_on: tuple[str, ...] = ()
    produces_artifact_types: tuple[str, ...] = ()
    verification_required: bool = False
    authorization_required: bool = False

    @field_validator("step_identifier", "capability_name")
    @classmethod
    def identifier(cls, value: str) -> str:
        return validate_identifier(value)


class ScientificWorkflowPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_identifier: str
    objective_identifier: str
    steps: tuple[ScientificWorkflowStep, ...] = Field(min_length=1)
    executable: bool
    blocking_reasons: tuple[str, ...] = ()
    ibm_job_submission_planned: Literal[False] = False

    @model_validator(mode="after")
    def directed_acyclic(self) -> Self:
        identifiers = [step.step_identifier for step in self.steps]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Scientific plan step identifiers must be unique.")
        seen: set[str] = set()
        for step in self.steps:
            if not set(step.depends_on).issubset(seen):
                raise ValueError("Scientific plan dependencies must precede each step.")
            seen.add(step.step_identifier)
        if self.executable == bool(self.blocking_reasons):
            raise ValueError("Plan executability and blocking reasons disagree.")
        return self


_DISTANCE_RANGE = re.compile(
    r"(?P<start>\d+(?:\.\d+)?)\s*(?:å|a|angstroms?)?\s+to\s+"
    r"(?P<end>\d+(?:\.\d+)?)\s*(?:å|a|angstroms?)?",
    re.I,
)
_POINTS = re.compile(r"\b(?P<count>\d{1,3})[-\s]?point\b", re.I)


def compile_scientific_objective(
    request: str,
    *,
    input_references: tuple[ScientificInputReference, ...] = (),
    budget: ScientificExecutionBudget | None = None,
) -> StructuredScientificObjective:
    """Classify broad molecular intent without making it directly executable."""

    normalized = " ".join(request.strip().split())
    if not normalized or len(normalized) > 8192:
        raise ValueError("Scientific request must be bounded nonblank text.")
    lowered = normalized.casefold()
    if re.search(r"\b(?:atom|orbital|particle)[_-]?index\s*[:=]", lowered):
        raise ValueError("Natural-language intent cannot inject internal indices.")

    if "transition state" in lowered or ("covalent" in lowered and "reaction" in lowered):
        task: ScientificTask = "covalent_transition_state"
        target_kind = "catalytic_nucleophile"
        target_label = "covalent bond formation at catalytic nucleophile"
    elif any(token in lowered for token in ("metal-centred", "metal-centered", "metal active site")):
        task = "metal_active_site_quantum"
        target_kind = "metal_coordination_shell"
        target_label = "metal centre and first coordination shell"
    elif any(token in lowered for token in ("potential-energy scan", "potential energy scan", "bond-dissociation", "bond dissociation")):
        task = "bond_dissociation_scan"
        target_kind = "named_bond"
        target_label = "scientist-named bond"
    elif "conformer" in lowered and any(token in lowered for token in ("preferred", "compare", "comparison")):
        task = "solvated_conformer_comparison"
        target_kind = "conformer_labels"
        target_label = "named conformer states"
    elif any(token in lowered for token in ("discover", "drug candidate", "optimize a drug")) and "protein" in lowered:
        task = "protein_ligand_discovery"
        target_kind = "binding_pocket"
        target_label = "protein binding pocket"
    else:
        raise ValueError("The request is outside the bounded molecular Phase 8 domain.")

    protein_refs = [item.reference_identifier for item in input_references if item.artifact_type in {"protein_structure", "prepared_receptor"}]
    ligand_refs = [item.reference_identifier for item in input_references if item.artifact_type in {"ligand_structure", "prepared_ligand"}]
    ambiguities: list[str] = []
    needs_protein = task in {"covalent_transition_state", "metal_active_site_quantum", "protein_ligand_discovery"}
    needs_ligand = task == "covalent_transition_state"
    if needs_protein and len(protein_refs) != 1:
        ambiguities.append("Exactly one protein structure must be resolved.")
    if needs_ligand and len(ligand_refs) != 1:
        ambiguities.append("Exactly one ligand structure must be resolved.")

    scan_count = scan_start = scan_end = None
    if task == "bond_dissociation_scan":
        points = _POINTS.search(normalized)
        distance = _DISTANCE_RANGE.search(normalized)
        if points is None or distance is None:
            ambiguities.append("Bond scan point count and distance endpoints are required.")
            scan_count, scan_start, scan_end = 15, 1.2, 2.8
        else:
            scan_count = int(points.group("count"))
            scan_start = float(distance.group("start"))
            scan_end = float(distance.group("end"))

    solvent = None
    for name, aliases in {"water": ("water", "aqueous"), "acetonitrile": ("acetonitrile",), "methanol": ("methanol",), "dmso": ("dmso",)}.items():
        if any(alias in lowered for alias in aliases):
            solvent = name
            break
    if task == "solvated_conformer_comparison" and solvent is None:
        ambiguities.append("A solvent identity is required for solvated comparison.")
        solvent = "water"

    quantum = "none"
    if task == "metal_active_site_quantum":
        quantum = "ibm_quantum" if "ibm" in lowered else "local_simulator"
    digest = hashlib.sha256((normalized + repr(input_references)).encode()).hexdigest()[:32]
    active_policy = (
        "reaction_center_automatic" if task in {"covalent_transition_state", "bond_dissociation_scan"}
        else "metal_ligand_automatic" if task == "metal_active_site_quantum"
        else "not_requested"
    )
    return StructuredScientificObjective(
        objective_identifier=f"scientific-objective-{digest}", original_request=normalized,
        task_type=task, input_references=input_references,
        semantic_target=ScientificSemanticTarget(
            protein_reference_identifier=protein_refs[0] if len(protein_refs) == 1 else None,
            ligand_reference_identifier=ligand_refs[0] if len(ligand_refs) == 1 else None,
            target_kind=target_kind, target_label=target_label,
        ), solvent=solvent, scan_point_count=scan_count,
        scan_start_angstrom=scan_start, scan_end_angstrom=scan_end,
        active_space_policy=active_policy, quantum_execution_target=quantum,
        budget=budget or ScientificExecutionBudget(),
        assumptions=("Semantic entity resolution must precede any atom-index materialization.",),
        ambiguity_hypotheses=tuple(ambiguities), clarification_required=bool(ambiguities),
    )


def plan_scientific_objective(objective: StructuredScientificObjective) -> ScientificWorkflowPlan:
    """Compose declared capability names into a deterministic dependency DAG."""

    definitions: list[tuple[str, str, tuple[str, ...], bool, bool]] = []

    def add(name: str, produces: tuple[str, ...], *, verify: bool = False, authorize: bool = False) -> None:
        step_id = f"step-{len(definitions) + 1:02d}-{name.replace('.', '-')}"
        dependencies = (definitions[-1][0],) if definitions else ()
        definitions.append((step_id, name, produces, verify, authorize))
        dependency_by_id[step_id] = dependencies

    dependency_by_id: dict[str, tuple[str, ...]] = {}
    add("molecular.structure_ingestion", ("molecular_topology",))
    if objective.task_type in {"covalent_transition_state", "metal_active_site_quantum", "protein_ligand_discovery"}:
        add(
            "molecular.protein_protonation_prepare",
            ("molecular_protein_protonation_preparation", "prepared_molecular_structure"),
            verify=True,
        )
    if objective.task_type == "covalent_transition_state":
        add("molecular.protonation_prepare", ("molecular_protonation_preparation",))
        add("electronic.qm_region_prepare", ("electronic_qm_region_preparation",))
        add("electronic.qmmm_embedding_prepare", ("qmmm_embedding_foundation",))
        add("electronic.active_space_select", ("electronic_active_space_selection",), verify=True)
        add("electronic.transition_state_search", ("electronic_transition_state_search",))
        add("electronic.frequency_analyze", ("electronic_frequency_analysis",), verify=True)
        add("electronic.reaction_path_confirm", ("electronic_reaction_path_result",), verify=True)
    elif objective.task_type == "metal_active_site_quantum":
        add("electronic.qm_region_prepare", ("electronic_qm_region_preparation",))
        add("electronic.active_space_select", ("electronic_active_space_selection",), verify=True)
        add("electronic.active_space_construct", ("electronic_active_space",))
        add("quantum_workflow.fermionic_hamiltonian_map", ("mapped_qubit_hamiltonian",))
        add("quantum_workflow.vqe", ("variational_ground_state_result",), verify=True)
        if objective.quantum_execution_target == "ibm_quantum":
            add("quantum.ibm_execution_prepare", ("authorization_request",), authorize=True)
    elif objective.task_type == "bond_dissociation_scan":
        add("molecular.constrained_distance_scan", ("molecular_distance_scan",), verify=True)
        add("electronic.active_space_select", ("electronic_active_space_selection",), verify=True)
        add("electronic.active_space_construct", ("electronic_active_space",))
        add("scientific_verification.potential_energy_curve", ("scientific_verification_report",), verify=True)
    elif objective.task_type == "solvated_conformer_comparison":
        add("molecular.conformer_generate", ("molecular_conformer_set",))
        add("electronic.implicit_solvent_hartree_fock", ("electronic_implicit_solvent_result",))
        add("scientific_verification.conformer_comparison", ("scientific_verification_report",), verify=True)
    else:
        add("discovery.campaign_initialize", ("discovery_campaign_checkpoint",))
        add("molecular.conformer_generate", ("molecular_conformer_set",))
        add("molecular.ligand_parameterize", ("molecular_ligand_parameterization",), verify=True)
        add("molecular.docking_pose_generate", ("molecular_docking_result", "docking_pose_pdbqt"), verify=True)
        add("discovery.campaign_iterate", ("discovery_campaign_checkpoint",), verify=True)
        add("molecular.scene_project", ("molecular_scene",))

    steps = tuple(ScientificWorkflowStep(
        step_identifier=step_id, capability_name=name,
        depends_on=dependency_by_id[step_id], produces_artifact_types=produces,
        verification_required=verify, authorization_required=authorize,
    ) for step_id, name, produces, verify, authorize in definitions)
    blockers = list(objective.ambiguity_hypotheses)
    if any(step.authorization_required for step in steps):
        blockers.append("IBM execution requires a separate explicit human authorization decision.")
    digest = hashlib.sha256((objective.objective_identifier + repr(steps)).encode()).hexdigest()[:32]
    return ScientificWorkflowPlan(
        plan_identifier=f"scientific-plan-{digest}",
        objective_identifier=objective.objective_identifier, steps=steps,
        executable=not blockers, blocking_reasons=tuple(blockers),
        ibm_job_submission_planned=False,
    )
