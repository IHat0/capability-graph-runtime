"""Deterministic, non-executing scientific feasibility evaluation."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .artifacts import ArtifactPointer, CreationProvenance
from .canonical import sha256_fingerprint, validate_identifier
from .capabilities import (
    CapabilityEstimate,
    CapabilityEstimateType,
    CapabilityExecutionEnvelope,
    CapabilityFidelityDeclaration,
    CapabilityFidelityLevel,
    CapabilityLimit,
    CapabilityLimitOperator,
    CapabilityPrerequisite,
    CapabilityPrerequisiteType,
    CapabilityVerificationRequirement,
    ScientificCapabilityCatalog,
)
from .planning import (
    CandidatePlanAssignment,
    CandidateResearchPlan,
    CapabilityFeasibilityResult,
    FeasibilityFinding,
    FeasibilityStatus,
    PlanningConstraints,
    PlanningFact,
    PlanningFactSet,
    RejectedCapabilityAlternative,
    ScientificObjective,
)
from .resources import ResourceAvailabilitySnapshot, ResourceQuantity

_FIDELITY_ORDER = {
    CapabilityFidelityLevel.EXPLORATORY: 0,
    CapabilityFidelityLevel.SCREENING: 1,
    CapabilityFidelityLevel.QUANTITATIVE: 2,
    CapabilityFidelityLevel.REFERENCE: 3,
}
_RUNTIME_SECONDS_UNIT = "seconds"


def _stable_identifier(prefix: str, value: Any) -> str:
    return f"{prefix}.{sha256_fingerprint(value)}"


def _version_key(version: Any) -> tuple[int, int, int]:
    return version.major, version.minor, version.patch


def _ordered_artifacts(values: Iterable[ArtifactPointer]) -> tuple[ArtifactPointer, ...]:
    by_identity = {
        (item.artifact_identifier, item.content_sha256): item for item in values
    }
    return tuple(by_identity[key] for key in sorted(by_identity))


class _FindingCollector:
    def __init__(self, envelope: CapabilityExecutionEnvelope) -> None:
        self._envelope = envelope
        self._findings: dict[str, FeasibilityFinding] = {}

    def add(
        self,
        *,
        finding_type: str,
        status: FeasibilityStatus,
        subject_identifier: str,
        explanation: str,
        facts: tuple[PlanningFact, ...] = (),
        prerequisites: tuple[CapabilityPrerequisite, ...] = (),
        evidence: tuple[ArtifactPointer, ...] = (),
    ) -> None:
        payload = {
            "finding_type": finding_type,
            "status": status.value,
            "subject_identifier": subject_identifier,
            "capability_name": self._envelope.descriptor.capability_name,
            "capability_version": self._envelope.descriptor.version.model_dump(),
            "explanation": explanation,
            "facts": tuple(item.fact_identifier for item in facts),
            "prerequisites": tuple(
                item.prerequisite_identifier for item in prerequisites
            ),
        }
        finding = FeasibilityFinding(
            finding_identifier=_stable_identifier("finding", payload),
            finding_type=finding_type,
            status=status,
            subject_identifier=subject_identifier,
            capability_name=self._envelope.descriptor.capability_name,
            capability_version=self._envelope.descriptor.version,
            explanation=explanation,
            related_fact_identifiers=tuple(item.fact_identifier for item in facts),
            related_prerequisite_identifiers=tuple(
                item.prerequisite_identifier for item in prerequisites
            ),
            evidence_artifacts=_ordered_artifacts(
                (*evidence, *(pointer for fact in facts for pointer in fact.evidence_artifacts))
            ),
        )
        self._findings[finding.finding_identifier] = finding

    def values(self) -> tuple[FeasibilityFinding, ...]:
        return tuple(self._findings[key] for key in sorted(self._findings))


def _numeric(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _select_execution_target(
    *,
    requested: str | None,
    objective: ScientificObjective,
    constraints: PlanningConstraints,
    resources: ResourceAvailabilitySnapshot,
    envelope: CapabilityExecutionEnvelope,
    findings: _FindingCollector,
) -> str | None:
    if requested is not None:
        requested = validate_identifier(requested, label="execution target")
        if requested in constraints.forbidden_execution_targets:
            findings.add(
                finding_type="execution_target",
                status=FeasibilityStatus.INFEASIBLE,
                subject_identifier=requested,
                explanation="The selected execution target is forbidden by planning policy.",
            )
        elif (
            constraints.permitted_execution_targets
            and requested not in constraints.permitted_execution_targets
        ):
            findings.add(
                finding_type="execution_target",
                status=FeasibilityStatus.INFEASIBLE,
                subject_identifier=requested,
                explanation="The selected execution target is outside the permitted target set.",
            )
        elif envelope.execution_targets and requested not in envelope.execution_targets:
            findings.add(
                finding_type="execution_target",
                status=FeasibilityStatus.INFEASIBLE,
                subject_identifier=requested,
                explanation="The capability does not declare support for the selected target.",
            )
        elif requested in resources.unavailable_reasons:
            findings.add(
                finding_type="execution_target",
                status=FeasibilityStatus.INFEASIBLE,
                subject_identifier=requested,
                explanation="The selected execution target is explicitly unavailable.",
            )
        elif requested not in resources.available_execution_targets:
            findings.add(
                finding_type="execution_target",
                status=FeasibilityStatus.UNKNOWN,
                subject_identifier=requested,
                explanation="Declared availability for the selected target is absent.",
            )
        else:
            findings.add(
                finding_type="execution_target",
                status=FeasibilityStatus.FEASIBLE,
                subject_identifier=requested,
                explanation="The selected execution target satisfies declared target policy.",
            )
        return requested

    envelope_targets = set(envelope.execution_targets)
    permitted_targets = set(constraints.permitted_execution_targets)
    available_targets = set(resources.available_execution_targets)
    forbidden_targets = set(constraints.forbidden_execution_targets)

    if envelope_targets and permitted_targets and not envelope_targets.intersection(
        permitted_targets
    ):
        findings.add(
            finding_type="execution_target",
            status=FeasibilityStatus.INFEASIBLE,
            subject_identifier=objective.objective_identifier,
            explanation="Capability targets and permitted targets do not overlap.",
        )
        return None

    candidates = available_targets - forbidden_targets
    if envelope_targets:
        candidates.intersection_update(envelope_targets)
    if permitted_targets:
        candidates.intersection_update(permitted_targets)
    if candidates:
        selected = sorted(candidates)[0]
        findings.add(
            finding_type="execution_target",
            status=FeasibilityStatus.FEASIBLE,
            subject_identifier=selected,
            explanation="The lexicographically first declared compatible target was selected.",
        )
        return selected

    possible = envelope_targets or permitted_targets or (
        available_targets | set(resources.unavailable_reasons)
    )
    eligible = possible - forbidden_targets
    if envelope_targets and permitted_targets:
        eligible.intersection_update(envelope_targets.intersection(permitted_targets))
    elif envelope_targets:
        eligible.intersection_update(envelope_targets)
    elif permitted_targets:
        eligible.intersection_update(permitted_targets)

    if possible and not eligible:
        status = FeasibilityStatus.INFEASIBLE
        explanation = "Every declared execution target is forbidden by planning policy."
    elif eligible and eligible.issubset(resources.unavailable_reasons):
        status = FeasibilityStatus.INFEASIBLE
        explanation = "Every otherwise compatible execution target is explicitly unavailable."
    else:
        status = FeasibilityStatus.UNKNOWN
        explanation = "No compatible target can be selected from declared availability."
    findings.add(
        finding_type="execution_target",
        status=status,
        subject_identifier=objective.objective_identifier,
        explanation=explanation,
    )
    return None


def _compare_limit(fact: PlanningFact, limit: CapabilityLimit) -> bool | None:
    fact_value = fact.value
    threshold = limit.threshold
    if isinstance(fact_value, bool) or isinstance(threshold, bool):
        if not isinstance(fact_value, bool) or not isinstance(threshold, bool):
            return None
        if limit.operator == CapabilityLimitOperator.EQUAL:
            return fact_value == threshold
        if limit.operator == CapabilityLimitOperator.NOT_EQUAL:
            return fact_value != threshold
        return None
    if not _numeric(fact_value) or not _numeric(threshold):
        return None
    return {
        CapabilityLimitOperator.EQUAL: fact_value == threshold,
        CapabilityLimitOperator.NOT_EQUAL: fact_value != threshold,
        CapabilityLimitOperator.LESS_THAN: fact_value < threshold,
        CapabilityLimitOperator.LESS_THAN_OR_EQUAL: fact_value <= threshold,
        CapabilityLimitOperator.GREATER_THAN: fact_value > threshold,
        CapabilityLimitOperator.GREATER_THAN_OR_EQUAL: fact_value >= threshold,
    }[limit.operator]


def _evaluate_limits(
    *,
    objective: ScientificObjective,
    facts: PlanningFactSet,
    envelope: CapabilityExecutionEnvelope,
    findings: _FindingCollector,
) -> None:
    for limit in envelope.applicability_limits:
        matches = tuple(
            fact
            for fact in facts.facts
            if fact.dimension == limit.dimension and fact.unit == limit.unit
        )
        if len(matches) != 1:
            findings.add(
                finding_type="applicability_limit",
                status=FeasibilityStatus.UNKNOWN,
                subject_identifier=objective.objective_identifier,
                explanation=(
                    "No compatible fact exists for the applicability limit."
                    if not matches
                    else "Multiple subjects provide ambiguous applicability facts."
                ),
                facts=matches,
            )
            continue
        result = _compare_limit(matches[0], limit)
        if result is None:
            status = FeasibilityStatus.UNKNOWN
            explanation = "The applicability fact and threshold cannot be compared safely."
        elif result:
            status = FeasibilityStatus.FEASIBLE
            explanation = "The declared applicability limit is satisfied."
        else:
            status = FeasibilityStatus.INFEASIBLE
            explanation = "The declared applicability limit is contradicted."
        findings.add(
            finding_type="applicability_limit",
            status=status,
            subject_identifier=matches[0].subject_identifier,
            explanation=explanation,
            facts=matches,
        )


def _evaluate_resources(
    *,
    objective: ScientificObjective,
    resources: ResourceAvailabilitySnapshot,
    envelope: CapabilityExecutionEnvelope,
    findings: _FindingCollector,
) -> None:
    for requirement in envelope.resource_requirements:
        available = resources.lookup(requirement.dimension, requirement.unit)
        if available is None:
            status = FeasibilityStatus.UNKNOWN
            explanation = "Required resource availability was not declared."
        elif isinstance(requirement.value, bool) or isinstance(available.value, bool):
            if not isinstance(requirement.value, bool) or not isinstance(
                available.value, bool
            ):
                status = FeasibilityStatus.UNKNOWN
                explanation = "Required and available resource value types differ."
            elif requirement.value == available.value:
                status = FeasibilityStatus.FEASIBLE
                explanation = "The declared boolean resource requirement is satisfied."
            else:
                status = FeasibilityStatus.INFEASIBLE
                explanation = "The declared boolean resource requirement is contradicted."
        elif _numeric(requirement.value) and _numeric(available.value):
            if available.value >= requirement.value:
                status = FeasibilityStatus.FEASIBLE
                explanation = "Declared resource availability meets the requirement."
            else:
                status = FeasibilityStatus.INFEASIBLE
                explanation = "Declared resource availability is below the requirement."
        else:
            status = FeasibilityStatus.UNKNOWN
            explanation = "Required and available resources cannot be compared safely."
        findings.add(
            finding_type="resource_requirement",
            status=status,
            subject_identifier=resources.snapshot_identifier,
            explanation=explanation,
        )


def _evaluate_estimate_ceiling(
    estimate: CapabilityEstimate, ceiling: float | int
) -> tuple[FeasibilityStatus, str]:
    if estimate.lower_bound is not None and estimate.lower_bound > ceiling:
        return (
            FeasibilityStatus.INFEASIBLE,
            "The estimate lower bound exceeds the hard planning ceiling.",
        )
    if estimate.upper_bound is not None and estimate.upper_bound <= ceiling:
        return (
            FeasibilityStatus.FEASIBLE,
            "The estimate upper bound is within the hard planning ceiling.",
        )
    return (
        FeasibilityStatus.UNKNOWN,
        "The estimate does not bound the capability within the hard ceiling.",
    )


def _evaluate_ceiling(
    *,
    objective: ScientificObjective,
    ceiling: ResourceQuantity,
    envelope: CapabilityExecutionEnvelope,
    findings: _FindingCollector,
) -> None:
    requirements = tuple(
        item
        for item in envelope.resource_requirements
        if item.dimension == ceiling.dimension and item.unit == ceiling.unit
    )
    estimates = tuple(
        item
        for item in envelope.estimates
        if item.estimate_type.value == ceiling.dimension and item.unit == ceiling.unit
    )
    if requirements:
        requirement = requirements[0]
        if isinstance(requirement.value, bool) or isinstance(ceiling.value, bool):
            if isinstance(requirement.value, bool) and isinstance(ceiling.value, bool):
                satisfied = requirement.value == ceiling.value
                status = (
                    FeasibilityStatus.FEASIBLE
                    if satisfied
                    else FeasibilityStatus.INFEASIBLE
                )
                explanation = (
                    "The boolean requirement satisfies the planning ceiling."
                    if satisfied
                    else "The boolean requirement contradicts the planning ceiling."
                )
            else:
                status = FeasibilityStatus.UNKNOWN
                explanation = "The requirement and ceiling types cannot be compared."
        elif _numeric(requirement.value) and _numeric(ceiling.value):
            if requirement.value > ceiling.value:
                status = FeasibilityStatus.INFEASIBLE
                explanation = "The declared minimum requirement exceeds the ceiling."
            elif len(estimates) == 1:
                status, explanation = _evaluate_estimate_ceiling(
                    estimates[0], ceiling.value
                )
            else:
                status = FeasibilityStatus.UNKNOWN
                explanation = (
                    "The minimum requirement is within the ceiling, but no unique "
                    "compatible estimate provides a safe upper bound."
                )
        else:
            status = FeasibilityStatus.UNKNOWN
            explanation = "The requirement and ceiling cannot be compared safely."
    elif len(estimates) == 1 and _numeric(ceiling.value):
        status, explanation = _evaluate_estimate_ceiling(estimates[0], ceiling.value)
    else:
        status = FeasibilityStatus.UNKNOWN
        explanation = (
            "Multiple declared estimates make the hard ceiling ambiguous."
            if len(estimates) > 1
            else "No compatible requirement or estimate bounds the hard ceiling."
        )
    findings.add(
        finding_type="planning_ceiling",
        status=status,
        subject_identifier=objective.objective_identifier,
        explanation=explanation,
        evidence=_ordered_artifacts(
            pointer for estimate in estimates for pointer in estimate.evidence_artifacts
        ),
    )


def _evaluate_wall_time(
    *,
    objective: ScientificObjective,
    constraints: PlanningConstraints,
    envelope: CapabilityExecutionEnvelope,
    findings: _FindingCollector,
) -> None:
    if constraints.maximum_wall_time_seconds is None:
        return
    estimates = tuple(
        item
        for item in envelope.estimates
        if item.estimate_type == CapabilityEstimateType.RUNTIME
        and item.unit == _RUNTIME_SECONDS_UNIT
    )
    if len(estimates) == 1:
        status, explanation = _evaluate_estimate_ceiling(
            estimates[0], constraints.maximum_wall_time_seconds
        )
    else:
        status = FeasibilityStatus.UNKNOWN
        explanation = (
            "Multiple runtime estimates make the wall-time ceiling ambiguous."
            if estimates
            else "No seconds-based runtime estimate bounds the wall-time ceiling."
        )
    findings.add(
        finding_type="wall_time_ceiling",
        status=status,
        subject_identifier=objective.objective_identifier,
        explanation=explanation,
        evidence=_ordered_artifacts(
            pointer for estimate in estimates for pointer in estimate.evidence_artifacts
        ),
    )


def _matching_fidelity(
    objective: ScientificObjective,
    envelope: CapabilityExecutionEnvelope,
) -> tuple[CapabilityFidelityDeclaration | None, bool]:
    matches = tuple(
        sorted(
            (
                item
                for item in envelope.fidelity_declarations
                if item.objective_type == objective.objective_type
            ),
            key=lambda item: item.fingerprint,
        )
    )
    if not matches:
        return None, False
    levels = {item.fidelity_level for item in matches}
    if len(levels) != 1:
        return None, True
    return matches[0], False


def _evaluate_fidelity(
    *,
    objective: ScientificObjective,
    constraints: PlanningConstraints,
    envelope: CapabilityExecutionEnvelope,
    findings: _FindingCollector,
) -> CapabilityFidelityDeclaration | None:
    fidelity, ambiguous = _matching_fidelity(objective, envelope)
    if ambiguous:
        findings.add(
            finding_type="fidelity",
            status=FeasibilityStatus.UNKNOWN,
            subject_identifier=objective.objective_identifier,
            explanation="Objective-specific fidelity declarations disagree.",
        )
        return None
    if constraints.minimum_fidelity is not None:
        if fidelity is None or fidelity.fidelity_level == CapabilityFidelityLevel.UNKNOWN:
            status = FeasibilityStatus.UNKNOWN
            explanation = "Required objective-specific fidelity evidence is absent."
        elif _FIDELITY_ORDER[fidelity.fidelity_level] < _FIDELITY_ORDER[
            constraints.minimum_fidelity
        ]:
            status = FeasibilityStatus.INFEASIBLE
            explanation = "Declared capability fidelity is below the required minimum."
        else:
            status = FeasibilityStatus.FEASIBLE
            explanation = "Declared capability fidelity meets the required minimum."
        findings.add(
            finding_type="fidelity",
            status=status,
            subject_identifier=objective.objective_identifier,
            explanation=explanation,
            evidence=() if fidelity is None else fidelity.evidence_artifacts,
        )

    required_confidences = tuple(
        value
        for value in (objective.required_confidence, constraints.required_confidence)
        if value is not None
    )
    if required_confidences:
        required = max(required_confidences)
        confidence = (
            fidelity.expected_error.confidence
            if fidelity is not None and fidelity.expected_error is not None
            else None
        )
        if confidence is None:
            status = FeasibilityStatus.UNKNOWN
            explanation = "Required confidence evidence is absent."
        elif confidence < required:
            status = FeasibilityStatus.INFEASIBLE
            explanation = "Declared estimate confidence is below the required confidence."
        else:
            status = FeasibilityStatus.FEASIBLE
            explanation = "Declared estimate confidence meets the requirement."
        findings.add(
            finding_type="confidence",
            status=status,
            subject_identifier=objective.objective_identifier,
            explanation=explanation,
            evidence=(
                ()
                if fidelity is None or fidelity.expected_error is None
                else fidelity.expected_error.evidence_artifacts
            ),
        )
    return fidelity


def _explicit_prerequisite_fact(
    prerequisite: CapabilityPrerequisite, facts: PlanningFactSet
) -> PlanningFact | None:
    if prerequisite.prerequisite_type == CapabilityPrerequisiteType.ARTIFACT_TYPE:
        dimension = "artifact_type.available"
    elif prerequisite.prerequisite_type == CapabilityPrerequisiteType.REGION:
        dimension = "region.exists"
    else:
        dimension = f"prerequisite.{prerequisite.prerequisite_type.value}"
    return facts.lookup(prerequisite.required_identifier, dimension, "boolean")


def _evaluate_prerequisites(
    *,
    objective: ScientificObjective,
    facts: PlanningFactSet,
    envelope: CapabilityExecutionEnvelope,
    findings: _FindingCollector,
) -> set[str]:
    unresolved: set[str] = set()
    for prerequisite in envelope.prerequisites:
        fact = _explicit_prerequisite_fact(prerequisite, facts)
        structurally_declared = (
            prerequisite.prerequisite_type == CapabilityPrerequisiteType.REGION
            and prerequisite.required_identifier
            in objective.structural_region_identifiers
        )
        satisfied = structurally_declared or (fact is not None and fact.value is True)
        contradicted = fact is not None and fact.value is False
        if satisfied:
            status = FeasibilityStatus.FEASIBLE
            explanation = "Explicit planning evidence satisfies the prerequisite."
        else:
            unresolved.add(prerequisite.prerequisite_identifier)
            if contradicted and prerequisite.blocking:
                status = FeasibilityStatus.INFEASIBLE
                explanation = "Explicit evidence contradicts the blocking prerequisite."
            elif prerequisite.blocking:
                status = FeasibilityStatus.UNKNOWN
                explanation = "Evidence for the blocking prerequisite is absent."
            else:
                status = FeasibilityStatus.CONDITIONAL
                explanation = "The nonblocking prerequisite remains unresolved."
        findings.add(
            finding_type="prerequisite",
            status=status,
            subject_identifier=prerequisite.required_identifier,
            explanation=explanation,
            facts=() if fact is None else (fact,),
            prerequisites=(prerequisite,),
        )
    return unresolved


def _verification_fact(
    requirement: CapabilityVerificationRequirement,
    facts: PlanningFactSet,
) -> PlanningFact | None:
    for outcome in requirement.acceptable_outcomes:
        fact = facts.lookup(
            requirement.verifier_identifier,
            f"verification.{requirement.subject_artifact_type}.{outcome.value}",
            "boolean",
        )
        if fact is not None and fact.value is True:
            return fact
    return None


def _verification_identity(
    requirement: CapabilityVerificationRequirement,
) -> str:
    return (
        f"verification.{requirement.verifier_identifier}."
        f"{requirement.subject_artifact_type}"
    )


def _evaluate_verifications_and_approvals(
    *,
    objective: ScientificObjective,
    constraints: PlanningConstraints,
    facts: PlanningFactSet,
    envelope: CapabilityExecutionEnvelope,
    findings: _FindingCollector,
) -> tuple[set[str], tuple[CapabilityVerificationRequirement, ...], tuple[str, ...]]:
    requirements_by_identity: dict[
        tuple[str, str], CapabilityVerificationRequirement
    ] = {}
    for requirement in (
        *envelope.verification_requirements,
        *constraints.required_verifications,
    ):
        identity = (
            requirement.verifier_identifier,
            requirement.subject_artifact_type,
        )
        existing = requirements_by_identity.get(identity)
        if existing is not None and existing != requirement:
            findings.add(
                finding_type="verification",
                status=FeasibilityStatus.UNKNOWN,
                subject_identifier=objective.objective_identifier,
                explanation="Verification declarations for one verifier and subject conflict.",
            )
            continue
        requirements_by_identity[identity] = requirement

    unresolved: set[str] = set()
    for identity in sorted(requirements_by_identity):
        requirement = requirements_by_identity[identity]
        requirement_identifier = _verification_identity(requirement)
        fact = _verification_fact(requirement, facts)
        if fact is not None:
            status = FeasibilityStatus.FEASIBLE
            explanation = "Explicit evidence records an acceptable verifier outcome."
        else:
            unresolved.add(requirement_identifier)
            if requirement.required_before_execution and requirement.blocking:
                status = FeasibilityStatus.UNKNOWN
                explanation = "Blocking pre-execution verification remains unresolved."
            else:
                status = FeasibilityStatus.CONDITIONAL
                explanation = "Required verification remains a post-planning condition."
        findings.add(
            finding_type="verification",
            status=status,
            subject_identifier=requirement.verifier_identifier,
            explanation=explanation,
            facts=() if fact is None else (fact,),
        )

    approvals = tuple(
        sorted(
            set(envelope.approval_requirements).union(
                constraints.approval_requirements
            )
        )
    )
    for approval in approvals:
        unresolved.add(approval)
        findings.add(
            finding_type="approval",
            status=FeasibilityStatus.CONDITIONAL,
            subject_identifier=approval,
            explanation="Required approval remains unresolved and is not granted by planning.",
        )
    return unresolved, tuple(requirements_by_identity[key] for key in sorted(requirements_by_identity)), approvals


def evaluate_capability_feasibility(
    *,
    objective: ScientificObjective,
    constraints: PlanningConstraints,
    facts: PlanningFactSet,
    resources: ResourceAvailabilitySnapshot,
    envelope: CapabilityExecutionEnvelope,
    execution_target: str | None = None,
) -> CapabilityFeasibilityResult:
    """Evaluate one immutable capability declaration without probing or execution."""
    findings = _FindingCollector(envelope)
    if (
        envelope.supported_objective_types
        and objective.objective_type not in envelope.supported_objective_types
    ):
        findings.add(
            finding_type="objective_support",
            status=FeasibilityStatus.INFEASIBLE,
            subject_identifier=objective.objective_identifier,
            explanation="The capability does not declare support for the objective type.",
        )
    else:
        findings.add(
            finding_type="objective_support",
            status=FeasibilityStatus.FEASIBLE,
            subject_identifier=objective.objective_identifier,
            explanation="The capability objective declaration permits this objective type.",
        )

    selected_target = _select_execution_target(
        requested=execution_target,
        objective=objective,
        constraints=constraints,
        resources=resources,
        envelope=envelope,
        findings=findings,
    )
    _evaluate_limits(
        objective=objective,
        facts=facts,
        envelope=envelope,
        findings=findings,
    )
    _evaluate_resources(
        objective=objective,
        resources=resources,
        envelope=envelope,
        findings=findings,
    )
    for ceiling in (*constraints.resource_ceilings, *constraints.cost_ceilings):
        _evaluate_ceiling(
            objective=objective,
            ceiling=ceiling,
            envelope=envelope,
            findings=findings,
        )
    _evaluate_wall_time(
        objective=objective,
        constraints=constraints,
        envelope=envelope,
        findings=findings,
    )
    fidelity = _evaluate_fidelity(
        objective=objective,
        constraints=constraints,
        envelope=envelope,
        findings=findings,
    )
    unresolved = _evaluate_prerequisites(
        objective=objective,
        facts=facts,
        envelope=envelope,
        findings=findings,
    )
    verification_unresolved, verifications, approvals = (
        _evaluate_verifications_and_approvals(
            objective=objective,
            constraints=constraints,
            facts=facts,
            envelope=envelope,
            findings=findings,
        )
    )
    unresolved.update(verification_unresolved)
    finding_values = findings.values()
    status = max(
        (item.status for item in finding_values),
        key=lambda item: item.precedence,
        default=FeasibilityStatus.FEASIBLE,
    )
    identity = {
        "objective": objective.fingerprint,
        "constraints": constraints.fingerprint,
        "facts": facts.fingerprint,
        "resources": resources.fingerprint,
        "envelope": envelope.fingerprint,
        "execution_target": selected_target,
    }
    evidence = _ordered_artifacts(
        pointer
        for source in (
            *(fact.evidence_artifacts for fact in facts.facts),
            *(estimate.evidence_artifacts for estimate in envelope.estimates),
            *(
                declaration.evidence_artifacts
                for declaration in envelope.fidelity_declarations
            ),
        )
        for pointer in source
    )
    return CapabilityFeasibilityResult(
        evaluation_identifier=_stable_identifier("evaluation", identity),
        objective_identifier=objective.objective_identifier,
        capability_name=envelope.descriptor.capability_name,
        capability_version=envelope.descriptor.version,
        status=status,
        execution_target=selected_target,
        findings=finding_values,
        unresolved_requirement_identifiers=tuple(sorted(unresolved)),
        estimates=envelope.estimates,
        fidelity=fidelity,
        required_verifications=verifications,
        required_approvals=approvals,
        evidence_artifacts=evidence,
    )


def _estimate_upper(
    result: CapabilityFeasibilityResult, estimate_type: CapabilityEstimateType
) -> tuple[str, float] | None:
    matches = tuple(
        item for item in result.estimates if item.estimate_type == estimate_type
    )
    if len(matches) != 1 or matches[0].upper_bound is None:
        return None
    return matches[0].unit, matches[0].upper_bound


def _ranked_result(
    candidates: tuple[CapabilityFeasibilityResult, ...]
) -> CapabilityFeasibilityResult:
    ranked = list(candidates)
    if all(
        item.fidelity is not None
        and item.fidelity.fidelity_level in _FIDELITY_ORDER
        for item in ranked
    ):
        ranked.sort(
            key=lambda item: -_FIDELITY_ORDER[item.fidelity.fidelity_level]  # type: ignore[union-attr]
        )
        best = _FIDELITY_ORDER[ranked[0].fidelity.fidelity_level]  # type: ignore[union-attr]
        ranked = [
            item
            for item in ranked
            if _FIDELITY_ORDER[item.fidelity.fidelity_level] == best  # type: ignore[union-attr]
        ]

    for estimate_type in (
        CapabilityEstimateType.MONETARY_COST,
        CapabilityEstimateType.RUNTIME,
    ):
        bounds = [_estimate_upper(item, estimate_type) for item in ranked]
        if bounds and all(bound is not None for bound in bounds):
            units = {bound[0] for bound in bounds if bound is not None}
            if len(units) == 1:
                minimum = min(bound[1] for bound in bounds if bound is not None)
                ranked = [
                    item
                    for item, bound in zip(ranked, bounds, strict=True)
                    if bound is not None and bound[1] == minimum
                ]

    return min(
        ranked,
        key=lambda item: (
            item.capability_name,
            *_version_key(item.capability_version),
            item.execution_target or "",
        ),
    )


def _aggregate_estimates(
    assignments: tuple[CandidatePlanAssignment, ...]
) -> tuple[CapabilityEstimate, ...]:
    grouped: dict[
        tuple[CapabilityEstimateType, str],
        dict[str, list[CapabilityEstimate]],
    ] = {}
    for assignment in assignments:
        for estimate in assignment.feasibility.estimates:
            grouped.setdefault((estimate.estimate_type, estimate.unit), {}).setdefault(
                assignment.assignment_identifier, []
            ).append(estimate)
    aggregated: list[CapabilityEstimate] = []
    for identity in sorted(grouped, key=lambda item: (item[0].value, item[1])):
        estimates_by_assignment = grouped[identity]
        if any(len(values) != 1 for values in estimates_by_assignment.values()):
            continue
        estimates = [
            estimates_by_assignment[identifier][0]
            for identifier in sorted(estimates_by_assignment)
        ]

        def total(field: str) -> float | None:
            values = [getattr(item, field) for item in estimates]
            return sum(values) if all(value is not None for value in values) else None

        confidences = [item.confidence for item in estimates]
        confidence = (
            min(value for value in confidences if value is not None)
            if all(value is not None for value in confidences)
            else None
        )
        lower_bound = total("lower_bound")
        expected_value = total("expected_value")
        upper_bound = total("upper_bound")
        if lower_bound is None and expected_value is None and upper_bound is None:
            continue
        aggregated.append(
            CapabilityEstimate(
                estimate_type=identity[0],
                lower_bound=lower_bound,
                expected_value=expected_value,
                upper_bound=upper_bound,
                unit=identity[1],
                confidence=confidence,
                estimation_basis=(
                    "Deterministic arithmetic aggregate of declared estimates for "
                    "selected sequential assignments; values are not measured guarantees."
                ),
                evidence_artifacts=_ordered_artifacts(
                    pointer
                    for estimate in estimates
                    for pointer in estimate.evidence_artifacts
                ),
            )
        )
    return tuple(aggregated)


def construct_candidate_research_plan(
    *,
    objective: ScientificObjective,
    constraints: PlanningConstraints,
    facts: PlanningFactSet,
    resources: ResourceAvailabilitySnapshot,
    catalogue: ScientificCapabilityCatalog,
    provenance: CreationProvenance,
) -> CandidateResearchPlan:
    """Evaluate every declared alternative and select at most one objective capability."""
    evaluated = tuple(
        evaluate_capability_feasibility(
            objective=objective,
            constraints=constraints,
            facts=facts,
            resources=resources,
            envelope=envelope,
        )
        for envelope in catalogue.envelopes()
    )
    feasible = tuple(
        item for item in evaluated if item.status == FeasibilityStatus.FEASIBLE
    )
    conditional = tuple(
        item for item in evaluated if item.status == FeasibilityStatus.CONDITIONAL
    )
    selected_result = (
        _ranked_result(feasible)
        if feasible
        else _ranked_result(conditional)
        if conditional
        else None
    )

    envelopes = {
        (
            envelope.descriptor.capability_name,
            *_version_key(envelope.descriptor.version),
        ): envelope
        for envelope in catalogue.envelopes()
    }
    all_fact_evidence = _ordered_artifacts(
        pointer for fact in facts.facts for pointer in fact.evidence_artifacts
    )
    assignments: tuple[CandidatePlanAssignment, ...] = ()
    if selected_result is not None:
        envelope = envelopes[
            (
                selected_result.capability_name,
                *_version_key(selected_result.capability_version),
            )
        ]
        assignment_identity = {
            "objective": objective.fingerprint,
            "evaluation": selected_result.fingerprint,
        }
        assignments = (
            CandidatePlanAssignment(
                assignment_identifier=_stable_identifier(
                    "assignment", assignment_identity
                ),
                objective_identifier=objective.objective_identifier,
                capability_name=selected_result.capability_name,
                capability_version=selected_result.capability_version,
                execution_target=selected_result.execution_target,
                feasibility=selected_result,
                input_artifacts=all_fact_evidence,
                input_artifact_types=envelope.descriptor.accepted_artifact_types,
                produced_artifact_types=envelope.descriptor.produced_artifact_types,
            ),
        )

    rejected: list[RejectedCapabilityAlternative] = []
    for result in evaluated:
        if selected_result is not None and result.evaluation_identifier == selected_result.evaluation_identifier:
            continue
        reasons = tuple(
            item.explanation
            for item in result.findings
            if item.status != FeasibilityStatus.FEASIBLE
        ) or ("A deterministic declared-metadata ranking selected another candidate.",)
        rejected.append(
            RejectedCapabilityAlternative(
                alternative_identifier=_stable_identifier(
                    "alternative",
                    {
                        "objective": objective.fingerprint,
                        "evaluation": result.fingerprint,
                    },
                ),
                objective_identifier=objective.objective_identifier,
                capability_name=result.capability_name,
                capability_version=result.capability_version,
                execution_target=result.execution_target,
                feasibility=result,
                rejection_reasons=reasons,
            )
        )

    aggregate_estimates = _aggregate_estimates(assignments)
    unresolved = tuple(
        sorted(
            {
                identifier
                for assignment in assignments
                for identifier in assignment.feasibility.unresolved_requirement_identifiers
            }
            or {
                identifier
                for result in evaluated
                for identifier in result.unresolved_requirement_identifiers
            }
        )
    )
    verifications_by_identity = {
        (item.verifier_identifier, item.subject_artifact_type): item
        for assignment in assignments
        for item in assignment.feasibility.required_verifications
    }
    approvals = tuple(
        sorted(
            {
                item
                for assignment in assignments
                for item in assignment.feasibility.required_approvals
            }
        )
    )
    plan_identity = {
        "objective": objective.fingerprint,
        "constraints": constraints.fingerprint,
        "facts": facts.fingerprint,
        "resources": resources.fingerprint,
        "evaluations": tuple(item.fingerprint for item in evaluated),
        "assignments": tuple(item.fingerprint for item in assignments),
    }
    return CandidateResearchPlan(
        plan_identifier=_stable_identifier("plan", plan_identity),
        schema_version=objective.schema_version,
        objective_identifier=objective.objective_identifier,
        objective_fingerprint=objective.fingerprint,
        project_identifier=objective.project_identifier,
        molecular_system_identifiers=objective.molecular_system_identifiers,
        planning_constraints_fingerprint=constraints.fingerprint,
        resource_snapshot_identifier=resources.snapshot_identifier,
        resource_snapshot_fingerprint=resources.fingerprint,
        planning_facts=facts,
        selected_assignments=assignments,
        rejected_alternatives=tuple(rejected),
        unresolved_requirement_identifiers=unresolved,
        unresolved_goal_identifiers=(
            () if assignments else (objective.objective_identifier,)
        ),
        aggregate_estimates=aggregate_estimates,
        verification_requirements=tuple(
            verifications_by_identity[key] for key in sorted(verifications_by_identity)
        ),
        approval_requirements=approvals,
        evidence_artifacts=_ordered_artifacts(
            (
                *all_fact_evidence,
                *(
                    pointer
                    for result in evaluated
                    for pointer in result.evidence_artifacts
                ),
            )
        ),
        provenance=provenance,
    )
