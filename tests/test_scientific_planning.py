"""Scientific objective and planning-policy contract regressions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cgr.kernel.contracts import CapabilityVersion
from cgr.science.artifacts import ArtifactPointer, CreationProvenance
from cgr.science.capabilities import (
    CapabilityFidelityLevel,
    CapabilityVerificationRequirement,
)
from cgr.science.contracts import ApprovalStatus, AssumptionSource, ScientificAssumption
from cgr.science.planning import (
    CandidateResearchPlan,
    PlanningConstraints,
    PlanningFact,
    PlanningFactSet,
    ScientificObjective,
)
from cgr.science.resources import ResourceQuantity
from cgr.science.verification import ScientificVerificationOutcome

VERSION = CapabilityVersion(major=1, minor=0, patch=0)
PROVENANCE = CreationProvenance(
    producer="science.planning.fixture",
    producer_version=VERSION,
    execution_identifier="planning.fixture-001",
)
POINTER_A = ArtifactPointer(artifact_identifier="artifact.a", content_sha256="a" * 64)
POINTER_B = ArtifactPointer(artifact_identifier="artifact.b", content_sha256="b" * 64)


def _assumption(identifier: str) -> ScientificAssumption:
    return ScientificAssumption(
        assumption_identifier=identifier,
        description=f"Declared assumption {identifier}.",
        source=AssumptionSource.USER_PROVIDED,
        approval_status=ApprovalStatus.APPROVED,
    )


def _verification(identifier: str) -> CapabilityVerificationRequirement:
    return CapabilityVerificationRequirement(
        verifier_identifier=identifier,
        subject_artifact_type="structure",
        acceptable_outcomes=(ScientificVerificationOutcome.PASSED,),
        required_before_execution=True,
        blocking=True,
    )


def _objective(**updates: object) -> ScientificObjective:
    values: dict[str, object] = {
        "objective_identifier": "objective.generic-001",
        "schema_version": VERSION,
        "objective_type": "property_estimation",
        "original_description": "  Estimate a declared scientific property.  ",
        "normalized_description": "Estimate the requested property.",
        "project_identifier": "project.example",
        "molecular_system_identifiers": ("system.two", "system.one"),
        "structure_identifiers": ("structure.two", "structure.one"),
        "structural_region_identifiers": ("region.two", "region.one"),
        "comparison_state_identifiers": ("state.two", "state.one"),
        "requested_property_identifiers": ("property.two", "property.one"),
        "scientific_constraints": ("  Preserve evidence. ", "Declare uncertainty."),
        "required_confidence": 0.9,
        "stopping_criteria": (" Verification succeeds. ", "Evidence is complete."),
        "assumptions": (_assumption("assumption.two"), _assumption("assumption.one")),
        "provenance": PROVENANCE,
    }
    values.update(updates)
    return ScientificObjective.model_validate(values)


def test_scientific_objective_constructs_and_orders_declarations() -> None:
    objective = _objective()

    assert objective.original_description == "Estimate a declared scientific property."
    assert objective.molecular_system_identifiers == ("system.one", "system.two")
    assert objective.structure_identifiers == ("structure.one", "structure.two")
    assert objective.structural_region_identifiers == ("region.one", "region.two")
    assert objective.comparison_state_identifiers == ("state.one", "state.two")
    assert objective.requested_property_identifiers == ("property.one", "property.two")
    assert objective.scientific_constraints == (
        "Declare uncertainty.",
        "Preserve evidence.",
    )
    assert [item.assumption_identifier for item in objective.assumptions] == [
        "assumption.one",
        "assumption.two",
    ]


@pytest.mark.parametrize(
    "field",
    (
        "molecular_system_identifiers",
        "structure_identifiers",
        "structural_region_identifiers",
        "comparison_state_identifiers",
        "requested_property_identifiers",
    ),
)
def test_scientific_objective_rejects_duplicate_identifiers(field: str) -> None:
    with pytest.raises(ValidationError, match="must be unique"):
        _objective(**{field: ("duplicate", "duplicate")})


def test_scientific_objective_rejects_blank_and_duplicate_text() -> None:
    with pytest.raises(ValidationError, match="cannot be blank"):
        _objective(scientific_constraints=(" ",))
    with pytest.raises(ValidationError, match="must be unique"):
        _objective(stopping_criteria=("Complete.", " Complete. "))
    with pytest.raises(ValidationError, match="cannot be blank"):
        _objective(original_description="  ")


def test_scientific_objective_rejects_duplicate_assumptions() -> None:
    assumption = _assumption("assumption.same")
    with pytest.raises(ValidationError, match="assumption identifiers must be unique"):
        _objective(assumptions=(assumption, assumption))


@pytest.mark.parametrize("confidence", (-0.01, 1.01, float("nan"), float("inf")))
def test_scientific_objective_rejects_invalid_confidence(confidence: float) -> None:
    with pytest.raises(ValidationError):
        _objective(required_confidence=confidence)


def test_planning_constraints_reject_target_conflicts() -> None:
    with pytest.raises(ValidationError, match="both permitted and forbidden"):
        PlanningConstraints(
            permitted_execution_targets=("target.shared",),
            forbidden_execution_targets=("target.shared",),
        )


@pytest.mark.parametrize("field", ("resource_ceilings", "cost_ceilings"))
def test_planning_constraints_reject_duplicate_ceilings(field: str) -> None:
    ceiling = ResourceQuantity(dimension="memory_mib", unit="mib", value=1024)
    with pytest.raises(ValidationError, match="pairs must be unique"):
        PlanningConstraints.model_validate({field: (ceiling, ceiling)})


@pytest.mark.parametrize("wall_time", (0, -1, float("nan"), float("inf")))
def test_planning_constraints_validate_wall_time(wall_time: float) -> None:
    with pytest.raises(ValidationError):
        PlanningConstraints(maximum_wall_time_seconds=wall_time)


def test_planning_constraints_order_verification_approval_and_policy() -> None:
    constraints = PlanningConstraints(
        permitted_execution_targets=("target.two", "target.one"),
        forbidden_execution_targets=("target.four", "target.three"),
        resource_ceilings=(
            ResourceQuantity(dimension="memory_mib", unit="mib", value=2048),
            ResourceQuantity(dimension="cpu_cores", unit="count", value=4),
        ),
        cost_ceilings=(
            ResourceQuantity(dimension="monetary_cost", unit="currency_units", value=5.0),
        ),
        maximum_wall_time_seconds=60,
        minimum_fidelity=CapabilityFidelityLevel.SCREENING,
        required_confidence=0.8,
        required_verifications=(_verification("verifier.z"), _verification("verifier.a")),
        approval_requirements=("approval.two", "approval.one"),
        policy_notes=("  Preserve evidence. ", "Fail closed."),
    )

    assert constraints.permitted_execution_targets == ("target.one", "target.two")
    assert constraints.approval_requirements == ("approval.one", "approval.two")
    assert [item.verifier_identifier for item in constraints.required_verifications] == [
        "verifier.a",
        "verifier.z",
    ]
    assert constraints.policy_notes == ("Fail closed.", "Preserve evidence.")


def test_planning_constraints_reject_duplicate_verifications_and_approvals() -> None:
    requirement = _verification("verifier.same")
    with pytest.raises(ValidationError, match="verification requirements must be unique"):
        PlanningConstraints(required_verifications=(requirement, requirement))
    with pytest.raises(ValidationError, match="must be unique"):
        PlanningConstraints(approval_requirements=("approval.same", "approval.same"))


def test_objective_and_constraints_have_stable_identity_without_authorization() -> None:
    first = _objective()
    second = ScientificObjective.model_validate(first.model_dump())
    constraints = PlanningConstraints(policy_notes=("No execution authority.",))

    assert first.to_canonical_json() == second.to_canonical_json()
    assert first.fingerprint == second.fingerprint
    assert "execution_allowed" not in ScientificObjective.model_fields
    assert "execution_allowed" not in PlanningConstraints.model_fields
    assert not hasattr(first, "execute")
    assert not hasattr(constraints, "authorize")


def _fact(identifier: str = "fact.atom-count", **updates: object) -> PlanningFact:
    values: dict[str, object] = {
        "fact_identifier": identifier,
        "schema_version": VERSION,
        "subject_identifier": "structure.one",
        "dimension": "structure.atom_count",
        "unit": "count",
        "value": 12,
        "evidence_artifacts": (POINTER_B, POINTER_A),
        "derivation_identifier": "molecular-planning-projection-v1",
    }
    values.update(updates)
    return PlanningFact.model_validate(values)


def test_planning_fact_is_strict_finite_and_orders_evidence() -> None:
    fact = _fact()
    assert fact.evidence_artifacts == (POINTER_A, POINTER_B)
    assert fact.semantic_address == (
        "structure.one",
        "structure.atom_count",
        "count",
    )
    for invalid in ("12", None, {}, [], float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            _fact(value=invalid)


def test_planning_fact_set_rejects_duplicate_identifiers_and_addresses() -> None:
    first = _fact()
    with pytest.raises(ValidationError, match="identifiers must be unique"):
        PlanningFactSet(facts=(first, first))
    with pytest.raises(ValidationError, match="semantic addresses must be unique"):
        PlanningFactSet(facts=(first, _fact("fact.other", value=13)))


def test_planning_fact_set_is_stable_ordered_and_exactly_lookupable() -> None:
    atom = _fact()
    bond = _fact(
        "fact.bond-count",
        dimension="structure.bond_count",
        value=11,
        evidence_artifacts=(POINTER_A,),
    )
    first = PlanningFactSet(facts=(atom, bond))
    second = PlanningFactSet(facts=(bond, atom))
    assert first.to_canonical_json() == second.to_canonical_json()
    assert first.fingerprint == second.fingerprint
    assert first.lookup("structure.one", "structure.atom_count", "count") == atom
    assert first.lookup("structure.one", "structure.atom_count", "boolean") is None


def test_candidate_plan_contract_has_no_execution_or_authorization_surface() -> None:
    assert "execution_allowed" not in CandidateResearchPlan.model_fields
    assert not hasattr(CandidateResearchPlan, "execute")
    assert not hasattr(CandidateResearchPlan, "authorize")
    assert not hasattr(CandidateResearchPlan, "submit")
