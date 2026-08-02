"""Scientific capability-envelope and catalogue regressions."""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

import cgr.science.capabilities as capabilities_module
from cgr.kernel.contracts import CapabilityVersion
from cgr.science.artifacts import ArtifactPointer
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
    DuplicateScientificCapabilityError,
    ScientificCapabilityCatalog,
    ScientificCapabilityCatalogError,
    ScientificCapabilityNotFoundError,
)
from cgr.science.contracts import CapabilityDescriptor, DeterminismClassification
from cgr.science.resources import ResourceQuantity
from cgr.science.verification import ScientificVerificationOutcome

VERSION = CapabilityVersion(major=1, minor=0, patch=0)
POINTER_A = ArtifactPointer(artifact_identifier="artifact.a", content_sha256="a" * 64)
POINTER_B = ArtifactPointer(artifact_identifier="artifact.b", content_sha256="b" * 64)


def _descriptor(
    name: str = "science.example",
    version: CapabilityVersion = VERSION,
    accepted: tuple[str, ...] = ("structure",),
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_name=name,
        version=version,
        accepted_artifact_types=accepted,
        produced_artifact_types=("scientific_result",),
        determinism=DeterminismClassification.DETERMINISTIC,
    )


def _verification(
    verifier: str = "verifier.structure",
    subject: str = "structure",
) -> CapabilityVerificationRequirement:
    return CapabilityVerificationRequirement(
        verifier_identifier=verifier,
        subject_artifact_type=subject,
        acceptable_outcomes=(
            ScientificVerificationOutcome.PASSED,
            ScientificVerificationOutcome.INCONCLUSIVE,
        ),
        required_before_execution=True,
        blocking=True,
    )


def _envelope(
    name: str = "science.example",
    version: CapabilityVersion = VERSION,
    *,
    objectives: tuple[str, ...] = ("property_estimation",),
    accepted: tuple[str, ...] = ("structure",),
    targets: tuple[str, ...] = ("target.local",),
) -> CapabilityExecutionEnvelope:
    return CapabilityExecutionEnvelope(
        descriptor=_descriptor(name, version, accepted),
        supported_objective_types=objectives,
        prerequisites=(
            CapabilityPrerequisite(
                prerequisite_identifier="prerequisite.structure",
                prerequisite_type=CapabilityPrerequisiteType.ARTIFACT_TYPE,
                required_identifier="structure",
                blocking=True,
                explanation="A declared structure is required.",
            ),
        ),
        execution_targets=targets,
        resource_requirements=(
            ResourceQuantity(dimension="memory_mib", unit="mib", value=1024),
        ),
        applicability_limits=(
            CapabilityLimit(
                dimension="atom_count",
                operator=CapabilityLimitOperator.LESS_THAN_OR_EQUAL,
                threshold=1000,
                unit="count",
                explanation="Declared applicability bound.",
            ),
        ),
        estimates=(
            CapabilityEstimate(
                estimate_type=CapabilityEstimateType.RUNTIME,
                expected_value=10.0,
                unit="seconds",
                confidence=0.5,
                estimation_basis="Historical declared estimate.",
            ),
        ),
        fidelity_declarations=(
            CapabilityFidelityDeclaration(
                objective_type="property_estimation",
                fidelity_level=CapabilityFidelityLevel.UNKNOWN,
                limitations=("No universal accuracy claim.",),
            ),
        ),
        known_limitations=("Requires explicit inputs.",),
        verification_requirements=(_verification(),),
        approval_requirements=("approval.scientist",),
        metadata={"declaration": "fixture"},
    )


def test_v1_enums_have_exact_serialized_values() -> None:
    assert tuple(item.value for item in CapabilityPrerequisiteType) == (
        "artifact_type",
        "capability",
        "runtime",
        "tool",
        "approval",
        "verification",
        "region",
        "parameter",
    )
    assert tuple(item.value for item in CapabilityLimitOperator) == (
        "equal",
        "not_equal",
        "less_than",
        "less_than_or_equal",
        "greater_than",
        "greater_than_or_equal",
    )
    assert tuple(item.value for item in CapabilityEstimateType) == (
        "runtime",
        "monetary_cost",
        "quantum_usage",
        "memory",
        "storage",
        "scientific_error",
    )
    assert tuple(item.value for item in CapabilityFidelityLevel) == (
        "exploratory",
        "screening",
        "quantitative",
        "reference",
        "unknown",
    )


def test_prerequisite_validates_identifiers_and_explanation() -> None:
    with pytest.raises(ValidationError, match="stable identifier"):
        CapabilityPrerequisite(
            prerequisite_identifier=" ",
            prerequisite_type=CapabilityPrerequisiteType.TOOL,
            required_identifier="tool.example",
            blocking=True,
            explanation="Required.",
        )
    with pytest.raises(ValidationError, match="cannot be blank"):
        CapabilityPrerequisite(
            prerequisite_identifier="prerequisite.tool",
            prerequisite_type=CapabilityPrerequisiteType.TOOL,
            required_identifier="tool.example",
            blocking=True,
            explanation=" ",
        )


@pytest.mark.parametrize("threshold", (float("nan"), float("inf"), float("-inf"), "1"))
def test_limit_rejects_invalid_thresholds(threshold: object) -> None:
    with pytest.raises(ValidationError):
        CapabilityLimit(
            dimension="atom_count",
            operator=CapabilityLimitOperator.LESS_THAN,
            threshold=threshold,
            unit="count",
            explanation="A finite threshold.",
        )


def test_limit_preserves_boolean_thresholds_and_validates_explanation() -> None:
    limit = CapabilityLimit(
        dimension="network_access",
        operator=CapabilityLimitOperator.EQUAL,
        threshold=False,
        unit="boolean",
        explanation="No network is declared.",
    )
    assert limit.threshold is False
    with pytest.raises(ValidationError, match="cannot be blank"):
        CapabilityLimit(
            dimension="network_access",
            operator=CapabilityLimitOperator.EQUAL,
            threshold=False,
            unit="boolean",
            explanation=" ",
        )


@pytest.mark.parametrize(
    "values",
    (
        {},
        {"lower_bound": 2.0, "expected_value": 1.0},
        {"expected_value": 2.0, "upper_bound": 1.0},
        {"lower_bound": 2.0, "upper_bound": 1.0},
    ),
)
def test_estimate_requires_consistent_bounds(values: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        CapabilityEstimate(
            estimate_type=CapabilityEstimateType.RUNTIME,
            unit="seconds",
            estimation_basis="Declared estimate.",
            **values,
        )


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_estimate_rejects_nonfinite_values(value: float) -> None:
    with pytest.raises(ValidationError):
        CapabilityEstimate(
            estimate_type=CapabilityEstimateType.SCIENTIFIC_ERROR,
            expected_value=value,
            unit="error_units",
            estimation_basis="Declared estimate.",
        )


@pytest.mark.parametrize(
    "estimate_type",
    (
        CapabilityEstimateType.RUNTIME,
        CapabilityEstimateType.MONETARY_COST,
        CapabilityEstimateType.QUANTUM_USAGE,
        CapabilityEstimateType.MEMORY,
        CapabilityEstimateType.STORAGE,
    ),
)
def test_inherently_nonnegative_estimates_reject_negative_expected_values(
    estimate_type: CapabilityEstimateType,
) -> None:
    with pytest.raises(ValidationError, match="cannot contain negative values"):
        CapabilityEstimate(
            estimate_type=estimate_type,
            expected_value=-1,
            unit="declared_units",
            estimation_basis="Declared estimate.",
        )


@pytest.mark.parametrize("field", ("lower_bound", "expected_value", "upper_bound"))
def test_runtime_estimate_rejects_negative_values_in_every_position(field: str) -> None:
    values = {field: -0.5}
    with pytest.raises(ValidationError, match="cannot contain negative values"):
        CapabilityEstimate(
            estimate_type=CapabilityEstimateType.RUNTIME,
            unit="seconds",
            estimation_basis="Declared estimate.",
            **values,
        )


@pytest.mark.parametrize(
    "estimate_type",
    (
        CapabilityEstimateType.RUNTIME,
        CapabilityEstimateType.MONETARY_COST,
        CapabilityEstimateType.QUANTUM_USAGE,
        CapabilityEstimateType.MEMORY,
        CapabilityEstimateType.STORAGE,
    ),
)
def test_inherently_nonnegative_estimates_accept_zero(
    estimate_type: CapabilityEstimateType,
) -> None:
    estimate = CapabilityEstimate(
        estimate_type=estimate_type,
        lower_bound=0,
        expected_value=0,
        upper_bound=0,
        unit="declared_units",
        estimation_basis="Declared zero estimate.",
    )
    assert estimate.lower_bound == estimate.expected_value == estimate.upper_bound == 0


def test_estimate_permits_negative_scientific_values_and_orders_evidence() -> None:
    estimate = CapabilityEstimate(
        estimate_type=CapabilityEstimateType.SCIENTIFIC_ERROR,
        lower_bound=-2.0,
        expected_value=-1.0,
        upper_bound=0.0,
        unit="error_units",
        estimation_basis="Signed metric.",
        evidence_artifacts=(POINTER_B, POINTER_A),
    )
    assert estimate.lower_bound == -2.0
    assert estimate.evidence_artifacts == (POINTER_A, POINTER_B)
    with pytest.raises(ValidationError, match="must be unique"):
        estimate.model_validate({**estimate.model_dump(), "evidence_artifacts": (POINTER_A, POINTER_A)})


def test_fidelity_requires_scientific_error_estimate() -> None:
    runtime = CapabilityEstimate(
        estimate_type=CapabilityEstimateType.RUNTIME,
        expected_value=1,
        unit="seconds",
        estimation_basis="Declared estimate.",
    )
    with pytest.raises(ValidationError, match="scientific-error"):
        CapabilityFidelityDeclaration(
            objective_type="property_estimation",
            fidelity_level=CapabilityFidelityLevel.UNKNOWN,
            expected_error=runtime,
        )


def test_fidelity_preserves_unknown_and_orders_declarations() -> None:
    declaration = CapabilityFidelityDeclaration(
        objective_type="property_estimation",
        fidelity_level=CapabilityFidelityLevel.UNKNOWN,
        conditions=("  Inputs validated. ", "Evidence available."),
        limitations=("Unknown transferability.",),
        evidence_artifacts=(POINTER_B, POINTER_A),
    )
    assert declaration.fidelity_level is CapabilityFidelityLevel.UNKNOWN
    assert declaration.conditions == ("Evidence available.", "Inputs validated.")
    assert declaration.evidence_artifacts == (POINTER_A, POINTER_B)


def test_verification_requirement_validates_outcomes_and_timing() -> None:
    requirement = _verification()
    assert requirement.acceptable_outcomes == (
        ScientificVerificationOutcome.INCONCLUSIVE,
        ScientificVerificationOutcome.PASSED,
    )
    with pytest.raises(ValidationError, match="At least one"):
        _verification().model_validate(
            {**requirement.model_dump(), "acceptable_outcomes": ()}
        )
    with pytest.raises(ValidationError, match="declare when"):
        _verification().model_validate(
            {
                **requirement.model_dump(),
                "required_before_execution": False,
                "required_after_execution": False,
            }
        )


def test_envelope_orders_every_unordered_collection_deterministically() -> None:
    first = CapabilityPrerequisite(
        prerequisite_identifier="prerequisite.z",
        prerequisite_type=CapabilityPrerequisiteType.PARAMETER,
        required_identifier="parameter.z",
        blocking=False,
        explanation="Declared parameter.",
    )
    second = CapabilityPrerequisite(
        prerequisite_identifier="prerequisite.a",
        prerequisite_type=CapabilityPrerequisiteType.APPROVAL,
        required_identifier="approval.a",
        blocking=True,
        explanation="Declared approval.",
    )
    envelope = CapabilityExecutionEnvelope(
        descriptor=_descriptor(),
        supported_objective_types=("objective.z", "objective.a"),
        prerequisites=(first, second),
        execution_targets=("target.z", "target.a"),
        resource_requirements=(
            ResourceQuantity(dimension="memory_mib", unit="mib", value=4),
            ResourceQuantity(dimension="cpu_cores", unit="count", value=1),
        ),
        known_limitations=("  Limitation Z. ", "Limitation A."),
        verification_requirements=(
            _verification("verifier.z"),
            _verification("verifier.a"),
        ),
        approval_requirements=("approval.z", "approval.a"),
    )
    assert envelope.supported_objective_types == ("objective.a", "objective.z")
    assert [item.prerequisite_identifier for item in envelope.prerequisites] == [
        "prerequisite.a",
        "prerequisite.z",
    ]
    assert envelope.execution_targets == ("target.a", "target.z")
    assert envelope.known_limitations == ("Limitation A.", "Limitation Z.")
    assert envelope.approval_requirements == ("approval.a", "approval.z")
    assert envelope.capability_identity == ("science.example", VERSION)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("supported_objective_types", ("objective.same", "objective.same")),
        ("prerequisites", (_envelope().prerequisites[0], _envelope().prerequisites[0])),
        (
            "resource_requirements",
            (
                ResourceQuantity(dimension="cpu_cores", unit="count", value=1),
                ResourceQuantity(dimension="cpu_cores", unit="count", value=2),
            ),
        ),
        ("applicability_limits", (_envelope().applicability_limits[0],) * 2),
        ("estimates", (_envelope().estimates[0],) * 2),
        ("fidelity_declarations", (_envelope().fidelity_declarations[0],) * 2),
        ("verification_requirements", (_verification(), _verification())),
        ("known_limitations", ("Same.", " Same. ")),
        ("approval_requirements", ("approval.same", "approval.same")),
    ),
)
def test_envelope_rejects_duplicate_semantic_collections(
    field: str, value: tuple[object, ...]
) -> None:
    base = _envelope().model_dump()
    base[field] = value
    with pytest.raises(ValidationError, match="unique"):
        CapabilityExecutionEnvelope.model_validate(base)


def test_envelope_fingerprint_is_stable_across_input_order() -> None:
    first = _envelope()
    second = CapabilityExecutionEnvelope.model_validate(first.model_dump())
    assert first.to_canonical_json() == second.to_canonical_json()
    assert first.fingerprint == second.fingerprint


def test_catalogue_rejects_exact_duplicates_and_allows_multiple_versions() -> None:
    first = _envelope(version=CapabilityVersion(major=1, minor=0, patch=0))
    second = _envelope(version=CapabilityVersion(major=2, minor=0, patch=0))
    catalogue = ScientificCapabilityCatalog((first, second))

    assert catalogue.capability_names() == ("science.example",)
    assert catalogue.envelopes() == (first, second)
    with pytest.raises(DuplicateScientificCapabilityError):
        catalogue.register(first)


def test_catalogue_register_many_is_transactional() -> None:
    existing = _envelope("science.existing")
    valid = _envelope("science.valid")
    catalogue = ScientificCapabilityCatalog((existing,))

    with pytest.raises(DuplicateScientificCapabilityError):
        catalogue.register_many((valid, existing))
    assert catalogue.envelopes() == (existing,)

    with pytest.raises(ScientificCapabilityCatalogError):
        catalogue.register_many((valid, object()))  # type: ignore[arg-type]
    assert catalogue.envelopes() == (existing,)


def test_catalogue_retrieval_order_get_and_not_found() -> None:
    zeta = _envelope("science.zeta")
    alpha_v2 = _envelope("science.alpha", CapabilityVersion(major=2, minor=0, patch=0))
    alpha_v1 = _envelope("science.alpha", CapabilityVersion(major=1, minor=0, patch=0))
    catalogue = ScientificCapabilityCatalog((zeta, alpha_v2, alpha_v1))

    assert catalogue.envelopes() == (alpha_v1, alpha_v2, zeta)
    assert catalogue.capability_names() == ("science.alpha", "science.zeta")
    assert catalogue.get("science.alpha", alpha_v2.descriptor.version) is alpha_v2
    with pytest.raises(ScientificCapabilityNotFoundError):
        catalogue.get("science.missing", VERSION)


def test_catalogue_filters_objective_artifacts_target_and_combines_with_and() -> None:
    matching = _envelope(
        "science.matching",
        objectives=("property_estimation",),
        accepted=("parameters", "structure"),
        targets=("target.local",),
    )
    other = _envelope(
        "science.other",
        objectives=("visualization",),
        accepted=("scene",),
        targets=("target.remote",),
    )
    catalogue = ScientificCapabilityCatalog((other, matching))

    assert catalogue.find(objective_type="property_estimation") == (matching,)
    assert catalogue.find(input_artifact_types=("structure", "parameters")) == (matching,)
    assert catalogue.find(execution_target="target.local") == (matching,)
    assert catalogue.find(
        objective_type="property_estimation",
        input_artifact_types=("structure",),
        execution_target="target.local",
    ) == (matching,)
    assert catalogue.find(
        objective_type="property_estimation", execution_target="target.remote"
    ) == ()


def test_catalogue_preserves_existing_generic_descriptor_semantics() -> None:
    generic = _envelope(
        "science.generic", objectives=(), accepted=(), targets=()
    )
    catalogue = ScientificCapabilityCatalog((generic,))

    assert catalogue.find(objective_type="future_objective") == (generic,)
    assert catalogue.find(execution_target="future_target") == (generic,)
    assert catalogue.find(input_artifact_types=("structure",)) == (generic,)


def test_catalogue_validates_filters_and_does_not_mutate_envelopes() -> None:
    envelope = _envelope()
    catalogue = ScientificCapabilityCatalog((envelope,))
    before = envelope.fingerprint

    assert catalogue.find(objective_type="property_estimation") == (envelope,)
    assert envelope.fingerprint == before
    with pytest.raises(ValueError):
        catalogue.find(objective_type=" ")
    with pytest.raises(ValueError, match="must be unique"):
        catalogue.find(input_artifact_types=("structure", "structure"))
    with pytest.raises(ValidationError):
        envelope.execution_targets = ("target.changed",)  # type: ignore[misc]


def test_capability_module_has_no_runtime_engine_or_provider_imports() -> None:
    source = inspect.getsource(capabilities_module)
    prohibited_imports = (
        "cgr.kernel.runtime",
        "cgr.kernel.router",
        "cgr.kernel.graph",
        "qiskit",
        "rdkit",
        "openmm",
        "pyscf",
        "ibm",
    )
    assert all(item not in source.lower() for item in prohibited_imports)
    assert not hasattr(ScientificCapabilityCatalog, "execute")
