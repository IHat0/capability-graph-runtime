"""Compatibility bridge from the Phase 8 product path into Phases 3 and 4.

The Phase 8 scientist API predates the canonical ``ScientificObjective`` and
``CandidateResearchPlan`` contracts.  This module keeps the executable Phase 8
handlers intact while giving every scientist request one canonical objective,
candidate plan, and Phase 4 workflow graph.  It is deliberately pure: it does
not acquire structures, infer missing scientific values, or execute work.
"""

from __future__ import annotations

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import (
    ArtifactPointer,
    CandidatePlanArtifactFlow,
    CandidatePlanAssignment,
    CandidateResearchPlan,
    CapabilityFeasibilityResult,
    CapabilityVerificationRequirement,
    CreationProvenance,
    FeasibilityFinding,
    FeasibilityStatus,
    PlanningFactSet,
    ResourceAvailabilitySnapshot,
    ScientificObjective,
    ScientificVerificationOutcome,
    sha256_fingerprint,
)
from cgr.workflow_graph import (
    FailureAction,
    RetryPolicy,
    WorkflowCompilationPolicy,
    WorkflowGraphCompiler,
    WorkflowGraphDefinition,
)

from .scientific_capability_catalogue import (
    ScientificCapabilityDefinition,
    phase8_scientific_capability_catalogue,
    public_capability_name,
)
from .scientific_compute_routing import ScientificComputeRouter
from .scientific_objectives import (
    ScientificWorkflowPlan,
    StructuredScientificObjective,
)

UNIFICATION_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
UNIFICATION_PROVENANCE = CreationProvenance(
    producer="pulsate-scientific-unification",
    producer_version=UNIFICATION_VERSION,
    source="phase8-compatibility",
)

_REQUESTED_PROPERTIES: dict[str, tuple[str, ...]] = {
    "covalent_transition_state": (
        "reaction_path",
        "transition_state",
    ),
    "metal_active_site_quantum": (
        "active_site_ground_state",
    ),
    "bond_dissociation_scan": (
        "potential_energy_curve",
    ),
    "solvated_conformer_comparison": (
        "conformer_energy_comparison",
    ),
    "protein_ligand_discovery": (
        "binding_pose",
        "ranked_molecular_candidates",
    ),
    "composed_research": (
        "scientist_confirmed_research_outputs",
    ),
}


def _stable_identifier(prefix: str, value: object) -> str:
    return f"{prefix}-{sha256_fingerprint(value)[:32]}"


def unresolved_requirement_identifiers(
    objective: StructuredScientificObjective,
) -> tuple[str, ...]:
    """Return stable identities for unresolved scientist-facing questions."""

    return tuple(
        sorted(
            _stable_identifier("requirement", reason)
            for reason in objective.ambiguity_hypotheses
        )
    )


def canonical_scientific_objective(
    objective: StructuredScientificObjective,
    *,
    project_identifier: str,
) -> ScientificObjective:
    """Translate reviewed Phase 8 intent without manufacturing missing facts."""

    constraints: list[str] = []
    if objective.solvent is not None:
        constraints.append(f"solvent={objective.solvent}")
    if objective.scan_point_count is not None:
        constraints.append(f"scan_point_count={objective.scan_point_count}")
    if objective.scan_start_angstrom is not None:
        constraints.append(
            f"scan_start_angstrom={objective.scan_start_angstrom}"
        )
    if objective.scan_end_angstrom is not None:
        constraints.append(f"scan_end_angstrom={objective.scan_end_angstrom}")
    constraints.append(
        f"quantum_execution_target={objective.quantum_execution_target}"
    )

    requested_properties = (
        objective.research_requirements.requested_outputs
        if objective.research_requirements is not None
        else _REQUESTED_PROPERTIES[objective.task_type]
    )
    if objective.research_requirements is not None:
        constraints.extend(
            "research_operation=" + operation
            for operation in objective.research_requirements.operations
        )
    return ScientificObjective(
        objective_identifier=objective.objective_identifier,
        schema_version=UNIFICATION_VERSION,
        objective_type=objective.task_type,
        original_description=objective.original_request,
        normalized_description=objective.original_request,
        project_identifier=project_identifier,
        structure_identifiers=tuple(
            item.artifact_identifier for item in objective.input_references
        ),
        requested_property_identifiers=requested_properties,
        scientific_constraints=tuple(constraints),
        assumptions=(),
        provenance=UNIFICATION_PROVENANCE,
    )


def _selected_definitions(
    objective: StructuredScientificObjective,
) -> dict[str, list[ScientificCapabilityDefinition]]:
    required = list(
        objective.research_requirements.required_artifact_types
        if objective.research_requirements is not None
        else ("scientist_facing_result",)
    )
    if objective.quantum_execution_target == "ibm_quantum":
        required.append("authorization_request")
    definitions = phase8_scientific_capability_catalogue().compose(
        task_type=(
            objective.research_requirements.capability_profile
            if objective.research_requirements is not None
            else objective.task_type
        ),
        available_artifact_types=("scientist_input",),
        required_artifact_types=tuple(required),
    )
    selected: dict[str, list[ScientificCapabilityDefinition]] = {}
    for definition in definitions:
        selected.setdefault(
            public_capability_name(definition.capability_name), []
        ).append(definition)
    return selected


def canonical_candidate_plan(
    *,
    objective: StructuredScientificObjective,
    canonical_objective: ScientificObjective,
    phase8_plan: ScientificWorkflowPlan,
    input_artifacts: tuple[ArtifactPointer, ...] = (),
    additional_unresolved_requirements: tuple[str, ...] = (),
    compute_router: ScientificComputeRouter | None = None,
    resource_snapshot: ResourceAvailabilitySnapshot | None = None,
) -> CandidateResearchPlan:
    """Preserve the Phase 8 DAG as an explicit canonical candidate plan.

    Phase 8 dependencies are represented by explicit canonical artifact-flow
    edges.  This preserves the exact existing DAG even when multiple steps
    refine the same domain artifact type.
    """

    base_requirements = tuple(
        sorted(
            set(unresolved_requirement_identifiers(objective)).union(
                additional_unresolved_requirements
            )
        )
    )
    compute_router = compute_router or ScientificComputeRouter()
    resource_snapshot = resource_snapshot or compute_router.resource_snapshot()
    resource_requirements: set[str] = set()
    definitions = _selected_definitions(objective)
    assignments: list[CandidatePlanAssignment] = []
    artifact_flows: list[CandidatePlanArtifactFlow] = []
    approval_requirements: set[str] = set()
    step_by_identifier = {
        step.step_identifier: step for step in phase8_plan.steps
    }

    for step in phase8_plan.steps:
        matching = definitions.get(step.capability_name, [])
        definition = matching.pop(0) if matching else None
        produced_types = (
            definition.produced_artifact_types if definition else ()
        )
        input_types = tuple(
            dict.fromkeys(
                definition.accepted_artifact_types
                if definition is not None
                else (() if step.depends_on else ("scientist_input",))
            )
        )
        route = compute_router.route(
            step.capability_name,
            requested_quantum_target=objective.quantum_execution_target,
            authorization_required=step.authorization_required,
            resources=resource_snapshot,
        )
        target = route.execution_target
        step_approvals: tuple[str, ...] = ()
        if step.authorization_required:
            step_approvals = ("approval.ibm-quantum-submission",)
            approval_requirements.update(step_approvals)

        route_requirements: tuple[str, ...] = ()
        if not route.available:
            route_requirements = (
                _stable_identifier(
                    "requirement-execution-target",
                    (step.capability_name, target),
                ),
            )
            resource_requirements.update(route_requirements)
        current_requirements = tuple(
            sorted(set(base_requirements).union(route_requirements))
        )
        finding_values = [
            FeasibilityFinding(
                finding_identifier=_stable_identifier(
                    "finding-compute-route",
                    (step.step_identifier, target, route.available),
                ),
                finding_type="compute_route",
                status=(
                    FeasibilityStatus.CONDITIONAL
                    if not route.available or step.authorization_required
                    else FeasibilityStatus.FEASIBLE
                ),
                subject_identifier=target,
                capability_name=step.capability_name,
                capability_version=UNIFICATION_VERSION,
                explanation=route.reason,
                related_prerequisite_identifiers=route_requirements,
                evidence_artifacts=input_artifacts,
            )
        ]
        if base_requirements:
            finding_values.append(
                FeasibilityFinding(
                    finding_identifier=_stable_identifier(
                        "finding", (step.step_identifier, base_requirements)
                    ),
                    finding_type="scientist_clarification_required",
                    status=FeasibilityStatus.CONDITIONAL,
                    subject_identifier=canonical_objective.objective_identifier,
                    capability_name=step.capability_name,
                    capability_version=UNIFICATION_VERSION,
                    explanation=(
                        "The capability remains conditional until the open "
                        "scientist questions are resolved."
                    ),
                    related_prerequisite_identifiers=base_requirements,
                    evidence_artifacts=input_artifacts,
                )
            )
        findings = tuple(finding_values)
        status = max(
            (item.status for item in findings),
            key=lambda item: item.precedence,
            default=FeasibilityStatus.FEASIBLE,
        )
        feasibility = CapabilityFeasibilityResult(
            evaluation_identifier=_stable_identifier(
                "evaluation",
                (
                    canonical_objective.objective_identifier,
                    step.step_identifier,
                ),
            ),
            objective_identifier=canonical_objective.objective_identifier,
            capability_name=step.capability_name,
            capability_version=UNIFICATION_VERSION,
            status=status,
            execution_target=target,
            findings=findings,
            unresolved_requirement_identifiers=current_requirements,
            required_verifications=(
                (
                    CapabilityVerificationRequirement(
                        verifier_identifier=(
                            "scientific-verification-"
                            + step.capability_name.replace(".", "-")
                        ),
                        subject_artifact_type=(
                            definition.produced_artifact_types[0]
                            if definition is not None
                            else "scientific-evidence"
                        ),
                        acceptable_outcomes=(
                            ScientificVerificationOutcome.PASSED,
                        ),
                        required_after_execution=True,
                        blocking=True,
                    ),
                )
                if step.verification_required
                else ()
            ),
            required_approvals=step_approvals,
            evidence_artifacts=input_artifacts,
        )
        assignments.append(
            CandidatePlanAssignment(
                assignment_identifier=step.step_identifier,
                objective_identifier=canonical_objective.objective_identifier,
                capability_name=step.capability_name,
                capability_version=UNIFICATION_VERSION,
                execution_target=target,
                feasibility=feasibility,
                input_artifacts=(input_artifacts if not step.depends_on else ()),
                input_artifact_types=input_types,
                produced_artifact_types=produced_types,
            )
        )
        if definition is not None:
            for dependency_identifier in step.depends_on:
                dependency = step_by_identifier[dependency_identifier]
                shared_types = tuple(
                    artifact_type
                    for artifact_type in definition.accepted_artifact_types
                    if artifact_type in dependency.produces_artifact_types
                )
                for artifact_type in shared_types:
                    artifact_flows.append(
                        CandidatePlanArtifactFlow(
                            flow_identifier=_stable_identifier(
                                "artifact-flow",
                                (
                                    dependency_identifier,
                                    step.step_identifier,
                                    artifact_type,
                                ),
                            ),
                            source_assignment_identifier=dependency_identifier,
                            destination_assignment_identifier=step.step_identifier,
                            artifact_type=artifact_type,
                        )
                    )

    planning_identity = {
        "budget": objective.budget.model_dump(mode="json"),
        "quantum_execution_target": objective.quantum_execution_target,
        "phase8_plan": phase8_plan.plan_identifier,
        "resource_snapshot": resource_snapshot.fingerprint,
    }
    requirements = tuple(
        sorted(set(base_requirements).union(resource_requirements))
    )
    return CandidateResearchPlan(
        plan_identifier=_stable_identifier(
            "candidate-plan", phase8_plan.plan_identifier
        ),
        schema_version=UNIFICATION_VERSION,
        objective_identifier=canonical_objective.objective_identifier,
        objective_fingerprint=canonical_objective.fingerprint,
        project_identifier=canonical_objective.project_identifier,
        molecular_system_identifiers=canonical_objective.molecular_system_identifiers,
        planning_constraints_fingerprint=sha256_fingerprint(planning_identity),
        resource_snapshot_identifier=resource_snapshot.snapshot_identifier,
        resource_snapshot_fingerprint=resource_snapshot.fingerprint,
        planning_facts=PlanningFactSet(),
        selected_assignments=tuple(assignments),
        artifact_flows=tuple(artifact_flows),
        unresolved_requirement_identifiers=requirements,
        approval_requirements=tuple(sorted(approval_requirements)),
        evidence_artifacts=input_artifacts,
        provenance=UNIFICATION_PROVENANCE,
    )


def canonical_workflow_graph(
    plan: CandidateResearchPlan,
    *,
    tenant_identifier: str | None = None,
    graph_identifier: str | None = None,
    maximum_replans: int = 0,
) -> WorkflowGraphDefinition:
    """Compile the compatibility plan through the real Phase 4 compiler."""

    return WorkflowGraphCompiler().compile(
        plan,
        created_by_principal="pulsate-research-controller",
        tenant_identifier=tenant_identifier,
        graph_identifier=graph_identifier,
        policy=WorkflowCompilationPolicy(
            retry_policy=RetryPolicy(max_attempts=maximum_replans + 1),
            failure_action=(
                FailureAction.RETRY
                if maximum_replans > 0
                else FailureAction.STOP
            ),
        ),
    )


def canonical_execution_graph(
    *,
    objective: StructuredScientificObjective,
    phase8_plan: ScientificWorkflowPlan,
    execution_identifier: str,
    project_identifier: str,
    input_artifacts: tuple[ArtifactPointer, ...] = (),
    tenant_identifier: str | None = None,
    additional_unresolved_requirements: tuple[str, ...] = (),
    compute_router: ScientificComputeRouter | None = None,
    resource_snapshot: ResourceAvailabilitySnapshot | None = None,
) -> tuple[ScientificObjective, CandidateResearchPlan, WorkflowGraphDefinition]:
    """Compile the one canonical objective, plan, and executable Phase 4 graph."""

    canonical_objective = canonical_scientific_objective(
        objective,
        project_identifier=project_identifier,
    )
    candidate_plan = canonical_candidate_plan(
        objective=objective,
        canonical_objective=canonical_objective,
        phase8_plan=phase8_plan,
        input_artifacts=input_artifacts,
        additional_unresolved_requirements=additional_unresolved_requirements,
        compute_router=compute_router,
        resource_snapshot=resource_snapshot,
    )
    graph = canonical_workflow_graph(
        candidate_plan,
        tenant_identifier=tenant_identifier,
        graph_identifier=(
            "scientific-canonical-graph-"
            + execution_identifier.rsplit("-", 1)[-1]
        ),
        maximum_replans=objective.budget.maximum_replans,
    )
    return canonical_objective, candidate_plan, graph


def input_artifact_pointers(
    references: tuple[object, ...],
) -> tuple[ArtifactPointer, ...]:
    """Extract exact pointers from artifact references without reading payloads."""

    pointers: list[ArtifactPointer] = []
    for reference in references:
        pointer = getattr(reference, "pointer", None)
        if not isinstance(pointer, ArtifactPointer):
            raise TypeError("Canonical input evidence requires artifact references.")
        pointers.append(pointer)
    return tuple(pointers)


__all__ = [
    "UNIFICATION_VERSION",
    "canonical_candidate_plan",
    "canonical_execution_graph",
    "canonical_scientific_objective",
    "canonical_workflow_graph",
    "input_artifact_pointers",
    "unresolved_requirement_identifiers",
]
