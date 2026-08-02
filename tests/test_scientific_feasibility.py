"""Deterministic generic feasibility and candidate-plan regressions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cgr.kernel.contracts import CapabilityVersion
from cgr.science.artifacts import ArtifactPointer, CreationProvenance
from cgr.science.capabilities import (
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
from cgr.science.contracts import CapabilityDescriptor, DeterminismClassification
from cgr.science.feasibility import (
    construct_candidate_research_plan,
    evaluate_capability_feasibility,
)
from cgr.science.planning import (
    FeasibilityStatus,
    PlanningConstraints,
    PlanningFact,
    PlanningFactSet,
    ScientificObjective,
)
from cgr.science.resources import ResourceAvailabilitySnapshot, ResourceQuantity
from cgr.science.verification import ScientificVerificationOutcome

VERSION = CapabilityVersion(major=1, minor=0, patch=0)
PROVENANCE = CreationProvenance(producer="planning-tests", producer_version=VERSION)
POINTER = ArtifactPointer(artifact_identifier="artifact.structure", content_sha256="a" * 64)


def _objective(**updates: object) -> ScientificObjective:
    values: dict[str, object] = {
        "objective_identifier": "objective.one",
        "schema_version": VERSION,
        "objective_type": "property_estimation",
        "original_description": "Estimate a property.",
        "normalized_description": "Estimate a property.",
        "project_identifier": "project.one",
        "molecular_system_identifiers": ("system.one",),
        "structure_identifiers": ("structure.one",),
        "provenance": PROVENANCE,
    }
    values.update(updates)
    return ScientificObjective.model_validate(values)


def _resources(**updates: object) -> ResourceAvailabilitySnapshot:
    values: dict[str, object] = {
        "snapshot_identifier": "resources.one",
        "schema_version": VERSION,
        "observed_at": datetime(2026, 1, 1, tzinfo=UTC),
        "execution_environment": "test",
        "available_execution_targets": ("target.local", "target.remote"),
        "provenance": PROVENANCE,
    }
    values.update(updates)
    return ResourceAvailabilitySnapshot.model_validate(values)


def _descriptor(name: str = "capability.one") -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_name=name,
        version=VERSION,
        accepted_artifact_types=("structure",),
        produced_artifact_types=("result",),
        determinism=DeterminismClassification.DETERMINISTIC,
    )


def _envelope(name: str = "capability.one", **updates: object) -> CapabilityExecutionEnvelope:
    values: dict[str, object] = {
        "descriptor": _descriptor(name),
        "supported_objective_types": ("property_estimation",),
        "execution_targets": ("target.local", "target.remote"),
    }
    values.update(updates)
    return CapabilityExecutionEnvelope.model_validate(values)


def _fact(
    dimension: str,
    value: bool | int | float,
    *,
    subject: str = "structure.one",
    unit: str = "count",
) -> PlanningFact:
    identity = f"{subject}-{dimension}-{unit}".replace(".", "-")
    return PlanningFact(
        fact_identifier=f"fact-{identity}",
        schema_version=VERSION,
        subject_identifier=subject,
        dimension=dimension,
        unit=unit,
        value=value,
        evidence_artifacts=(POINTER,),
        derivation_identifier="test-facts-v1",
    )


def _evaluate(
    envelope: CapabilityExecutionEnvelope | None = None,
    *,
    objective: ScientificObjective | None = None,
    constraints: PlanningConstraints | None = None,
    facts: tuple[PlanningFact, ...] = (),
    resources: ResourceAvailabilitySnapshot | None = None,
    target: str | None = None,
):
    return evaluate_capability_feasibility(
        objective=objective or _objective(),
        constraints=constraints or PlanningConstraints(),
        facts=PlanningFactSet(facts=facts),
        resources=resources or _resources(),
        envelope=envelope or _envelope(),
        execution_target=target,
    )


def test_supported_and_unsupported_objective_types() -> None:
    assert _evaluate().status == FeasibilityStatus.FEASIBLE
    unsupported = _evaluate(_envelope(supported_objective_types=("design",)))
    assert unsupported.status == FeasibilityStatus.INFEASIBLE
    assert any(item.finding_type == "objective_support" for item in unsupported.findings)
    assert _evaluate(_envelope(supported_objective_types=())).status == FeasibilityStatus.FEASIBLE


def test_target_selection_is_deterministic_and_policy_is_fail_closed() -> None:
    assert _evaluate().execution_target == "target.local"
    assert _evaluate(target="target.remote").execution_target == "target.remote"
    forbidden = _evaluate(
        constraints=PlanningConstraints(forbidden_execution_targets=("target.local",)),
        target="target.local",
    )
    assert forbidden.status == FeasibilityStatus.INFEASIBLE
    outside = _evaluate(
        constraints=PlanningConstraints(permitted_execution_targets=("target.local",)),
        target="target.remote",
    )
    assert outside.status == FeasibilityStatus.INFEASIBLE
    unavailable = _evaluate(
        resources=_resources(
            available_execution_targets=("target.remote",),
            unavailable_reasons={"target.local": "offline"},
        ),
        target="target.local",
    )
    assert unavailable.status == FeasibilityStatus.INFEASIBLE
    absent = _evaluate(resources=_resources(available_execution_targets=()))
    assert absent.status == FeasibilityStatus.UNKNOWN


@pytest.mark.parametrize(
    ("operator", "value", "threshold", "expected"),
    (
        (CapabilityLimitOperator.EQUAL, 4, 4, FeasibilityStatus.FEASIBLE),
        (CapabilityLimitOperator.NOT_EQUAL, 4, 5, FeasibilityStatus.FEASIBLE),
        (CapabilityLimitOperator.LESS_THAN, 4, 5, FeasibilityStatus.FEASIBLE),
        (CapabilityLimitOperator.LESS_THAN_OR_EQUAL, 4, 4, FeasibilityStatus.FEASIBLE),
        (CapabilityLimitOperator.GREATER_THAN, 5, 4, FeasibilityStatus.FEASIBLE),
        (CapabilityLimitOperator.GREATER_THAN_OR_EQUAL, 4, 4, FeasibilityStatus.FEASIBLE),
        (CapabilityLimitOperator.LESS_THAN, 5, 4, FeasibilityStatus.INFEASIBLE),
    ),
)
def test_all_numeric_limit_operators(operator, value, threshold, expected) -> None:
    limit = CapabilityLimit(
        dimension="structure.atom_count",
        operator=operator,
        threshold=threshold,
        unit="count",
        explanation="A declared limit.",
    )
    assert _evaluate(
        _envelope(applicability_limits=(limit,)),
        facts=(_fact("structure.atom_count", value),),
    ).status == expected


@pytest.mark.parametrize(
    ("operator", "fact_value", "threshold", "expected"),
    (
        (CapabilityLimitOperator.EQUAL, True, True, FeasibilityStatus.FEASIBLE),
        (CapabilityLimitOperator.NOT_EQUAL, True, False, FeasibilityStatus.FEASIBLE),
        (CapabilityLimitOperator.EQUAL, True, 1, FeasibilityStatus.UNKNOWN),
        (CapabilityLimitOperator.LESS_THAN, True, True, FeasibilityStatus.UNKNOWN),
    ),
)
def test_boolean_limits_are_strict(operator, fact_value, threshold, expected) -> None:
    limit = CapabilityLimit(
        dimension="structure.has_topology",
        operator=operator,
        threshold=threshold,
        unit="boolean",
        explanation="A boolean limit.",
    )
    assert _evaluate(
        _envelope(applicability_limits=(limit,)),
        facts=(_fact("structure.has_topology", fact_value, unit="boolean"),),
    ).status == expected


def test_limit_resolution_requires_one_exact_dimension_and_unit() -> None:
    limit = CapabilityLimit(
        dimension="structure.atom_count",
        operator=CapabilityLimitOperator.LESS_THAN,
        threshold=10,
        unit="count",
        explanation="Exact evidence only.",
    )
    envelope = _envelope(applicability_limits=(limit,))
    assert _evaluate(envelope).status == FeasibilityStatus.UNKNOWN
    assert _evaluate(
        envelope, facts=(_fact("structure.atom_count", 3, unit="atoms"),)
    ).status == FeasibilityStatus.UNKNOWN
    assert _evaluate(
        envelope,
        facts=(
            _fact("structure.atom_count", 3),
            _fact("structure.atom_count", 4, subject="structure.two"),
        ),
    ).status == FeasibilityStatus.UNKNOWN


@pytest.mark.parametrize(
    ("required", "available", "expected"),
    (
        (4, 8, FeasibilityStatus.FEASIBLE),
        (8, 4, FeasibilityStatus.INFEASIBLE),
        (True, True, FeasibilityStatus.FEASIBLE),
        (True, False, FeasibilityStatus.INFEASIBLE),
        (False, True, FeasibilityStatus.INFEASIBLE),
        (True, 1, FeasibilityStatus.UNKNOWN),
    ),
)
def test_resource_requirements_use_strict_exact_values(required, available, expected) -> None:
    unit = "boolean" if isinstance(required, bool) else "count"
    envelope = _envelope(
        resource_requirements=(ResourceQuantity(dimension="workers", unit=unit, value=required),)
    )
    resources = _resources(
        available_resources=(ResourceQuantity(dimension="workers", unit=unit, value=available),)
    )
    assert _evaluate(envelope, resources=resources).status == expected


def test_missing_resource_and_hard_estimate_ceilings_are_conservative() -> None:
    requirement = ResourceQuantity(dimension="memory", unit="mib", value=4)
    assert _evaluate(_envelope(resource_requirements=(requirement,))).status == FeasibilityStatus.UNKNOWN
    within = CapabilityEstimate(
        estimate_type=CapabilityEstimateType.MONETARY_COST,
        lower_bound=1,
        upper_bound=3,
        unit="usd",
        estimation_basis="Declared range.",
    )
    unknown = within.model_copy(update={"upper_bound": None, "expected_value": 2})
    over = within.model_copy(update={"lower_bound": 6, "upper_bound": 8})
    ceiling = PlanningConstraints(
        cost_ceilings=(ResourceQuantity(dimension="monetary_cost", unit="usd", value=5),)
    )
    assert _evaluate(_envelope(estimates=(within,)), constraints=ceiling).status == FeasibilityStatus.FEASIBLE
    assert _evaluate(_envelope(estimates=(unknown,)), constraints=ceiling).status == FeasibilityStatus.UNKNOWN
    assert _evaluate(_envelope(estimates=(over,)), constraints=ceiling).status == FeasibilityStatus.INFEASIBLE


def test_minimum_resource_requirement_cannot_prove_a_hard_ceiling() -> None:
    requirement = ResourceQuantity(dimension="memory", unit="mib", value=4)
    resources = _resources(
        available_resources=(
            ResourceQuantity(dimension="memory", unit="mib", value=64),
        )
    )
    constraints = PlanningConstraints(
        resource_ceilings=(
            ResourceQuantity(dimension="memory", unit="mib", value=8),
        )
    )
    without_bound = _evaluate(
        _envelope(resource_requirements=(requirement,)),
        resources=resources,
        constraints=constraints,
    )
    assert without_bound.status == FeasibilityStatus.UNKNOWN

    bounded = CapabilityEstimate(
        estimate_type=CapabilityEstimateType.MEMORY,
        lower_bound=4,
        upper_bound=7,
        unit="mib",
        estimation_basis="Declared memory-use bound.",
    )
    with_bound = _evaluate(
        _envelope(resource_requirements=(requirement,), estimates=(bounded,)),
        resources=resources,
        constraints=constraints,
    )
    assert with_bound.status == FeasibilityStatus.FEASIBLE

    excessive_requirement = requirement.model_copy(update={"value": 9})
    excessive = _evaluate(
        _envelope(resource_requirements=(excessive_requirement,)),
        resources=resources,
        constraints=constraints,
    )
    assert excessive.status == FeasibilityStatus.INFEASIBLE


def test_wall_time_uses_only_seconds_upper_bound() -> None:
    estimate = CapabilityEstimate(
        estimate_type=CapabilityEstimateType.RUNTIME,
        upper_bound=9,
        unit="seconds",
        estimation_basis="Declared bound.",
    )
    constraints = PlanningConstraints(maximum_wall_time_seconds=10)
    assert _evaluate(_envelope(estimates=(estimate,)), constraints=constraints).status == FeasibilityStatus.FEASIBLE
    minutes = estimate.model_copy(update={"unit": "minutes"})
    assert _evaluate(_envelope(estimates=(minutes,)), constraints=constraints).status == FeasibilityStatus.UNKNOWN


def test_fidelity_and_confidence_require_declared_objective_evidence() -> None:
    screening = CapabilityFidelityDeclaration(
        objective_type="property_estimation",
        fidelity_level=CapabilityFidelityLevel.SCREENING,
    )
    assert _evaluate(
        _envelope(fidelity_declarations=(screening,)),
        constraints=PlanningConstraints(minimum_fidelity=CapabilityFidelityLevel.SCREENING),
    ).status == FeasibilityStatus.FEASIBLE
    assert _evaluate(
        _envelope(fidelity_declarations=(screening,)),
        constraints=PlanningConstraints(minimum_fidelity=CapabilityFidelityLevel.QUANTITATIVE),
    ).status == FeasibilityStatus.INFEASIBLE
    assert _evaluate(
        constraints=PlanningConstraints(minimum_fidelity=CapabilityFidelityLevel.SCREENING)
    ).status == FeasibilityStatus.UNKNOWN
    unknown = CapabilityFidelityDeclaration(
        objective_type="property_estimation",
        fidelity_level=CapabilityFidelityLevel.UNKNOWN,
    )
    assert _evaluate(
        _envelope(fidelity_declarations=(unknown,)),
        constraints=PlanningConstraints(
            minimum_fidelity=CapabilityFidelityLevel.EXPLORATORY
        ),
    ).status == FeasibilityStatus.UNKNOWN
    assert _evaluate(objective=_objective(required_confidence=0.9)).status == FeasibilityStatus.UNKNOWN


def test_prerequisites_verifications_and_approvals_remain_unresolved() -> None:
    prerequisite = CapabilityPrerequisite(
        prerequisite_identifier="prerequisite.region",
        prerequisite_type=CapabilityPrerequisiteType.REGION,
        required_identifier="region.one",
        blocking=True,
        explanation="A region is needed.",
    )
    verification = CapabilityVerificationRequirement(
        verifier_identifier="verifier.one",
        subject_artifact_type="structure",
        acceptable_outcomes=(ScientificVerificationOutcome.PASSED,),
        required_before_execution=True,
        blocking=True,
    )
    result = _evaluate(
        _envelope(
            prerequisites=(prerequisite,),
            verification_requirements=(verification,),
            approval_requirements=("approval.one",),
        )
    )
    assert result.status == FeasibilityStatus.UNKNOWN
    assert set(result.unresolved_requirement_identifiers) == {
        "prerequisite.region",
        "verification.verifier.one.structure",
        "approval.one",
    }
    satisfied = _evaluate(
        _envelope(prerequisites=(prerequisite,)),
        objective=_objective(structural_region_identifiers=("region.one",)),
    )
    assert satisfied.status == FeasibilityStatus.FEASIBLE


def test_nonblocking_prerequisite_and_post_execution_verification_are_conditional() -> None:
    prerequisite = CapabilityPrerequisite(
        prerequisite_identifier="prerequisite.tool",
        prerequisite_type=CapabilityPrerequisiteType.TOOL,
        required_identifier="tool.one",
        blocking=False,
        explanation="A later tool is needed.",
    )
    verification = CapabilityVerificationRequirement(
        verifier_identifier="verifier.after",
        subject_artifact_type="result",
        acceptable_outcomes=(ScientificVerificationOutcome.PASSED,),
        required_after_execution=True,
        blocking=True,
    )
    result = _evaluate(
        _envelope(
            prerequisites=(prerequisite,),
            verification_requirements=(verification,),
            approval_requirements=("approval.one",),
        )
    )
    assert result.status == FeasibilityStatus.CONDITIONAL
    assert set(result.unresolved_requirement_identifiers) == {
        "prerequisite.tool",
        "verification.verifier.after.result",
        "approval.one",
    }


def test_evaluation_findings_and_fingerprint_are_deterministic() -> None:
    first = _evaluate()
    second = _evaluate()
    assert first.to_canonical_json() == second.to_canonical_json()
    assert first.fingerprint == second.fingerprint
    assert first.findings == tuple(
        sorted(first.findings, key=lambda item: item.finding_identifier)
    )
    assert not hasattr(first, "execute")
    assert not hasattr(first, "submit")


def test_infeasible_precedence_exceeds_unknown_and_conditional() -> None:
    limit = CapabilityLimit(
        dimension="structure.atom_count",
        operator=CapabilityLimitOperator.LESS_THAN,
        threshold=1,
        unit="count",
        explanation="Too small.",
    )
    result = _evaluate(
        _envelope(applicability_limits=(limit,), approval_requirements=("approval.one",)),
        facts=(_fact("structure.atom_count", 2),),
        constraints=PlanningConstraints(minimum_fidelity=CapabilityFidelityLevel.REFERENCE),
    )
    assert result.status == FeasibilityStatus.INFEASIBLE


def test_candidate_plan_is_stable_selects_ranked_candidate_and_preserves_alternatives() -> None:
    cheap = CapabilityEstimate(
        estimate_type=CapabilityEstimateType.MONETARY_COST,
        lower_bound=1,
        expected_value=2,
        upper_bound=3,
        unit="usd",
        confidence=0.8,
        estimation_basis="Declared estimate.",
    )
    expensive = cheap.model_copy(update={"lower_bound": 4, "expected_value": 5, "upper_bound": 6})
    alpha = _envelope("capability.alpha", estimates=(expensive,))
    beta = _envelope("capability.beta", estimates=(cheap,))
    catalog = ScientificCapabilityCatalog((alpha, beta))
    first = construct_candidate_research_plan(
        objective=_objective(),
        constraints=PlanningConstraints(),
        facts=PlanningFactSet(),
        resources=_resources(),
        catalogue=catalog,
        provenance=PROVENANCE,
    )
    second = construct_candidate_research_plan(
        objective=_objective(),
        constraints=PlanningConstraints(),
        facts=PlanningFactSet(),
        resources=_resources(),
        catalogue=ScientificCapabilityCatalog((beta, alpha)),
        provenance=PROVENANCE,
    )
    assert first.fingerprint == second.fingerprint
    assert first.selected_assignments[0].capability_name == "capability.beta"
    assert first.rejected_alternatives[0].capability_name == "capability.alpha"
    assert first.aggregate_estimates[0].upper_bound == 3
    assert "guarantees" in first.aggregate_estimates[0].estimation_basis
    assert not hasattr(first, "execute")
    assert not hasattr(first, "authorize")


def test_missing_quantum_usage_metadata_does_not_claim_nonquantum_preference() -> None:
    quantum_usage = CapabilityEstimate(
        estimate_type=CapabilityEstimateType.QUANTUM_USAGE,
        upper_bound=10,
        unit="shots",
        estimation_basis="Declared usage estimate, not an execution classification.",
    )
    alpha = _envelope("capability.alpha", estimates=(quantum_usage,))
    beta = _envelope("capability.beta")
    plan = construct_candidate_research_plan(
        objective=_objective(),
        constraints=PlanningConstraints(),
        facts=PlanningFactSet(),
        resources=_resources(),
        catalogue=ScientificCapabilityCatalog((beta, alpha)),
        provenance=PROVENANCE,
    )
    assert plan.selected_assignments[0].capability_name == "capability.alpha"


def test_ambiguous_same_assignment_estimates_are_not_fabricated_into_total() -> None:
    first = CapabilityEstimate(
        estimate_type=CapabilityEstimateType.MONETARY_COST,
        lower_bound=1,
        upper_bound=2,
        unit="usd",
        estimation_basis="First independent declared estimate.",
    )
    second = CapabilityEstimate(
        estimate_type=CapabilityEstimateType.MONETARY_COST,
        lower_bound=3,
        upper_bound=4,
        unit="usd",
        estimation_basis="Second independent declared estimate.",
    )
    plan = construct_candidate_research_plan(
        objective=_objective(),
        constraints=PlanningConstraints(),
        facts=PlanningFactSet(),
        resources=_resources(),
        catalogue=ScientificCapabilityCatalog(
            (_envelope(estimates=(first, second)),)
        ),
        provenance=PROVENANCE,
    )
    assert plan.selected_assignments
    assert plan.aggregate_estimates == ()
