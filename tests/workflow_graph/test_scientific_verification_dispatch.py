"""Workflow-facing Phase 6 scientific-verification dispatch regressions."""

from cgr.scientific_verification import (
    CalculationEvidence,
    EnergyComparisonRequest,
    VerificationOutcome,
    default_scientific_verifier_registry,
)


def _calculation(identifier: str, energy: float, artifact: str, geometry: str):
    return CalculationEvidence(
        calculation_identifier=identifier,
        artifact_sha256=artifact * 64,
        molecule_identifier="molecule.workflow",
        geometry_sha256=geometry * 64,
        method_identifier="method.workflow",
        method_sha256="c" * 64,
        execution_completed=True,
        execution_successful=True,
        identity_valid=True,
        numerically_converged=True,
        values_finite=True,
        authorized=True,
        total_energy_hartree=energy,
        uncertainty_hartree=1e-4,
        sample_count=2048,
    )


def test_objective_dispatch_is_stable_under_input_calculation_order() -> None:
    first = _calculation("calc.a", -1.1, "1", "a")
    second = _calculation("calc.b", -1.0, "2", "b")

    left = EnergyComparisonRequest(
        request_identifier="verification.workflow.energy",
        subject_identifier="subject.workflow",
        calculations=(first, second),
        minimum_resolvable_difference_hartree=1e-3,
    )
    right = EnergyComparisonRequest(
        request_identifier="verification.workflow.energy",
        subject_identifier="subject.workflow",
        calculations=(second, first),
        minimum_resolvable_difference_hartree=1e-3,
    )

    assert left.fingerprint == right.fingerprint

    registry = default_scientific_verifier_registry()
    left_report = registry.verify(left)
    right_report = registry.verify(right)

    assert left_report.fingerprint == right_report.fingerprint
    assert left_report.overall_outcome is VerificationOutcome.PASSED


def test_workflow_facing_report_contains_only_engine_neutral_evidence() -> None:
    report = default_scientific_verifier_registry().verify(
        EnergyComparisonRequest(
            request_identifier="verification.workflow.neutral",
            subject_identifier="subject.workflow",
            calculations=(
                _calculation("calc.a", -1.1, "1", "a"),
                _calculation("calc.b", -1.0, "2", "b"),
            ),
            minimum_resolvable_difference_hartree=1e-3,
        )
    )

    serialized = report.to_canonical_json().lower()

    for engine in (
        "openmm",
        "pyscf",
        "qiskit",
        "qiskit_nature",
        "qiskit_aer",
        "rdkit",
    ):
        assert engine not in serialized
