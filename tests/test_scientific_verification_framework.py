"""Phase 6 scientific-verification framework and objective-family regressions."""

from __future__ import annotations

import sys

import pytest

from cgr.scientific_verification import (
    VERIFICATION_DIMENSION_ORDER,
    BaseObjectiveScientificVerifier,
    CalculationEvidence,
    CandidateRankingRequest,
    ConformerComparisonRequest,
    DuplicateScientificVerifierError,
    EnergyComparisonRequest,
    InteractionEnergyRequest,
    MetalCentreRequest,
    MolecularDynamicsRequest,
    ObjectiveScientificVerifier,
    ObjectiveVerifierFamily,
    PotentialEnergyCurveRequest,
    PotentialEnergyPoint,
    RankedCandidate,
    ScientificVerificationRequest,
    ScientificVerifierRegistry,
    TransitionStateRequest,
    VerificationDimension,
    VerificationOutcome,
    default_scientific_verifier_registry,
    finding,
)


def _sha(character: str) -> str:
    return character * 64


def _calculation(
    identifier: str,
    *,
    energy: float | None = -1.0,
    molecule: str = "molecule.fixture",
    geometry_character: str = "b",
    method_character: str = "c",
    uncertainty: float | None = 1e-4,
    completed: bool = True,
    successful: bool = True,
    identity_valid: bool = True,
    converged: bool = True,
    values_finite: bool = True,
    authorized: bool = True,
) -> CalculationEvidence:
    artifact_character = {
        "calc.a": "1",
        "calc.b": "2",
        "calc.c": "3",
        "calc.complex": "4",
        "calc.fragment-a": "5",
        "calc.fragment-b": "6",
        "calc.ts": "7",
        "calc.metal": "8",
        "calc.md": "9",
    }.get(identifier, "a")
    return CalculationEvidence(
        calculation_identifier=identifier,
        artifact_sha256=_sha(artifact_character),
        molecule_identifier=molecule,
        geometry_sha256=_sha(geometry_character),
        method_identifier="method.fixture",
        method_sha256=_sha(method_character),
        execution_completed=completed,
        execution_successful=successful,
        identity_valid=identity_valid,
        numerically_converged=converged,
        values_finite=values_finite,
        authorized=authorized,
        total_energy_hartree=energy,
        uncertainty_hartree=uncertainty,
        sample_count=4096,
    )


def _result(report, dimension: VerificationDimension):
    return next(
        item for item in report.dimension_results if item.dimension is dimension
    )


def test_default_registry_contains_all_eight_objective_families() -> None:
    registry = default_scientific_verifier_registry()

    assert registry.families() == tuple(
        sorted(family.value for family in ObjectiveVerifierFamily)
    )
    assert len(registry.verifiers()) == 8
    assert all(
        isinstance(verifier, ObjectiveScientificVerifier)
        for verifier in registry.verifiers()
    )


def test_common_report_always_contains_all_seven_dimensions_in_order() -> None:
    report = default_scientific_verifier_registry().verify(
        EnergyComparisonRequest(
            request_identifier="verification.energy.pass",
            subject_identifier="subject.energy",
            calculations=(
                _calculation("calc.a", energy=-1.1),
                _calculation(
                    "calc.b",
                    energy=-1.0,
                    geometry_character="d",
                ),
            ),
            minimum_resolvable_difference_hartree=1e-3,
        )
    )

    assert tuple(item.dimension for item in report.dimension_results) == (
        VERIFICATION_DIMENSION_ORDER
    )
    assert report.overall_outcome is VerificationOutcome.PASSED
    assert report.execution_integrity_passed is True
    assert report.scientific_quality_passed is True
    assert report.authorization_passed is True


def test_energy_agreement_passes_when_difference_is_within_tolerance() -> None:
    report = default_scientific_verifier_registry().verify(
        EnergyComparisonRequest(
            request_identifier="verification.energy.agreement-pass",
            subject_identifier="subject.energy",
            calculations=(
                _calculation("calc.a", energy=-1.0, uncertainty=None),
                _calculation("calc.b", energy=-0.9999999995, uncertainty=None),
            ),
            comparison_goal="verify_agreement",
            maximum_agreement_difference_hartree=1e-5,
        )
    )

    assert report.overall_outcome is VerificationOutcome.PASSED


def test_energy_agreement_fails_when_difference_exceeds_tolerance() -> None:
    report = default_scientific_verifier_registry().verify(
        EnergyComparisonRequest(
            request_identifier="verification.energy.agreement-fail",
            subject_identifier="subject.energy",
            calculations=(
                _calculation("calc.a", energy=-1.0, uncertainty=None),
                _calculation("calc.b", energy=-0.998, uncertainty=None),
            ),
            comparison_goal="verify_agreement",
            maximum_agreement_difference_hartree=1e-5,
        )
    )

    assert report.overall_outcome is VerificationOutcome.FAILED
    assert (
        _result(report, VerificationDimension.NUMERICAL_INTEGRITY).outcome
        is VerificationOutcome.FAILED
    )


def test_variational_upper_bound_accepts_a_converged_approximation() -> None:
    report = default_scientific_verifier_registry().verify(
        EnergyComparisonRequest(
            request_identifier="verification.energy.variational-pass",
            subject_identifier="subject.energy",
            calculations=(
                _calculation("calc.a", energy=-1.0, uncertainty=None),
                _calculation("calc.b", energy=-0.99995, uncertainty=None),
            ),
            comparison_goal="verify_variational_upper_bound",
            variational_reference_calculation_identifier="calc.a",
            variational_candidate_calculation_identifier="calc.b",
            variational_tolerance_hartree=1e-9,
        )
    )

    assert report.overall_outcome is VerificationOutcome.PASSED


def test_variational_upper_bound_rejects_energy_below_exact_reference() -> None:
    report = default_scientific_verifier_registry().verify(
        EnergyComparisonRequest(
            request_identifier="verification.energy.variational-fail",
            subject_identifier="subject.energy",
            calculations=(
                _calculation("calc.a", energy=-1.0, uncertainty=None),
                _calculation("calc.b", energy=-1.001, uncertainty=None),
            ),
            comparison_goal="verify_variational_upper_bound",
            variational_reference_calculation_identifier="calc.a",
            variational_candidate_calculation_identifier="calc.b",
            variational_tolerance_hartree=1e-9,
        )
    )

    assert report.overall_outcome is VerificationOutcome.FAILED
    assert (
        _result(report, VerificationDimension.SCIENTIFIC_PLAUSIBILITY).outcome
        is VerificationOutcome.FAILED
    )


def test_parameterized_curve_allows_geometry_specific_molecule_fingerprints() -> None:
    report = default_scientific_verifier_registry().verify(
        PotentialEnergyCurveRequest(
            request_identifier="verification.curve.parameterized-system",
            subject_identifier="subject.parameterized-system",
            calculations=(
                _calculation(
                    "calc.a", energy=-1.0, molecule="molecule.point-a"
                ),
                _calculation(
                    "calc.b",
                    energy=-1.1,
                    molecule="molecule.point-b",
                    geometry_character="d",
                ),
                _calculation(
                    "calc.c",
                    energy=-0.9,
                    molecule="molecule.point-c",
                    geometry_character="e",
                ),
            ),
            points=(
                PotentialEnergyPoint(
                    calculation_identifier="calc.a",
                    reaction_coordinate=0.5,
                    reaction_coordinate_unit="angstrom",
                ),
                PotentialEnergyPoint(
                    calculation_identifier="calc.b",
                    reaction_coordinate=1.0,
                    reaction_coordinate_unit="angstrom",
                ),
                PotentialEnergyPoint(
                    calculation_identifier="calc.c",
                    reaction_coordinate=1.5,
                    reaction_coordinate_unit="angstrom",
                ),
            ),
            require_same_molecule=False,
        )
    )

    assert report.overall_outcome is VerificationOutcome.PASSED


def test_execution_can_pass_while_scientific_quality_fails() -> None:
    report = default_scientific_verifier_registry().verify(
        EnergyComparisonRequest(
            request_identifier="verification.energy.science-fail",
            subject_identifier="subject.energy",
            calculations=(
                _calculation("calc.a", energy=-1.1),
                _calculation(
                    "calc.b",
                    energy=-1.0,
                    geometry_character="d",
                ),
            ),
            minimum_resolvable_difference_hartree=1e-3,
            expected_lowest_calculation_identifier="calc.b",
        )
    )

    assert report.execution_integrity_passed is True
    assert (
        _result(report, VerificationDimension.EXECUTION_INTEGRITY).outcome
        is VerificationOutcome.PASSED
    )
    assert report.scientific_quality_passed is False
    assert (
        _result(report, VerificationDimension.SCIENTIFIC_PLAUSIBILITY).outcome
        is VerificationOutcome.FAILED
    )
    assert report.overall_outcome is VerificationOutcome.FAILED


def test_common_comparability_uncertainty_and_authorization_are_fail_closed() -> None:
    report = default_scientific_verifier_registry().verify(
        EnergyComparisonRequest(
            request_identifier="verification.energy.common-failures",
            subject_identifier="subject.energy",
            calculations=(
                _calculation(
                    "calc.a",
                    energy=-1.0000,
                    uncertainty=0.02,
                    authorized=False,
                ),
                _calculation(
                    "calc.b",
                    energy=-0.9999,
                    geometry_character="d",
                    method_character="e",
                    uncertainty=0.02,
                ),
            ),
            require_uncertainty=True,
            maximum_uncertainty_hartree=0.01,
            minimum_resolvable_difference_hartree=0.001,
        )
    )

    assert (
        _result(
            report,
            VerificationDimension.CROSS_CALCULATION_COMPARABILITY,
        ).outcome
        is VerificationOutcome.FAILED
    )
    assert (
        _result(report, VerificationDimension.UNCERTAINTY).outcome
        is VerificationOutcome.FAILED
    )
    assert (
        _result(report, VerificationDimension.AUTHORIZATION).outcome
        is VerificationOutcome.FAILED
    )
    assert report.execution_integrity_passed is True
    assert report.authorization_passed is False


@pytest.mark.parametrize(
    "verification_request",
    [
        EnergyComparisonRequest(
            request_identifier="verification.energy.family",
            subject_identifier="subject.energy",
            calculations=(
                _calculation("calc.a", energy=-1.2),
                _calculation(
                    "calc.b",
                    energy=-1.0,
                    geometry_character="d",
                ),
            ),
            minimum_resolvable_difference_hartree=0.01,
        ),
        PotentialEnergyCurveRequest(
            request_identifier="verification.pec.family",
            subject_identifier="subject.pec",
            calculations=(
                _calculation("calc.a", energy=-1.0, geometry_character="b"),
                _calculation("calc.b", energy=-1.2, geometry_character="d"),
                _calculation("calc.c", energy=-1.0, geometry_character="e"),
            ),
            points=(
                PotentialEnergyPoint(
                    calculation_identifier="calc.a",
                    reaction_coordinate=-1.0,
                    reaction_coordinate_unit="angstrom",
                ),
                PotentialEnergyPoint(
                    calculation_identifier="calc.b",
                    reaction_coordinate=0.0,
                    reaction_coordinate_unit="angstrom",
                ),
                PotentialEnergyPoint(
                    calculation_identifier="calc.c",
                    reaction_coordinate=1.0,
                    reaction_coordinate_unit="angstrom",
                ),
            ),
            maximum_adjacent_energy_jump_hartree=0.5,
            require_interior_minimum=True,
        ),
        InteractionEnergyRequest(
            request_identifier="verification.interaction.family",
            subject_identifier="subject.interaction",
            calculations=(
                _calculation(
                    "calc.complex",
                    energy=-3.1,
                    molecule="molecule.complex",
                ),
                _calculation(
                    "calc.fragment-a",
                    energy=-1.0,
                    molecule="molecule.fragment-a",
                    geometry_character="d",
                ),
                _calculation(
                    "calc.fragment-b",
                    energy=-2.0,
                    molecule="molecule.fragment-b",
                    geometry_character="e",
                ),
            ),
            complex_calculation_identifier="calc.complex",
            fragment_calculation_identifiers=(
                "calc.fragment-a",
                "calc.fragment-b",
            ),
            reported_interaction_energy_hartree=-0.1,
            consistency_tolerance_hartree=1e-8,
        ),
        TransitionStateRequest(
            request_identifier="verification.ts.family",
            subject_identifier="subject.ts",
            calculations=(_calculation("calc.ts", energy=-1.0),),
            imaginary_frequencies_cm_inverse=(-500.0,),
            gradient_norm_hartree_per_bohr=1e-5,
            maximum_gradient_norm_hartree_per_bohr=1e-4,
            minimum_imaginary_frequency_magnitude_cm_inverse=100.0,
            connects_reactant_and_product=True,
        ),
        ConformerComparisonRequest(
            request_identifier="verification.conformer.family",
            subject_identifier="subject.conformer",
            calculations=(
                _calculation("calc.a", energy=-1.1, geometry_character="b"),
                _calculation("calc.b", energy=-1.0, geometry_character="d"),
            ),
        ),
        MetalCentreRequest(
            request_identifier="verification.metal.family",
            subject_identifier="subject.metal",
            calculations=(_calculation("calc.metal", energy=-10.0),),
            metal_element="Fe",
            coordination_number=4,
            allowed_coordination_numbers=(4, 6),
            metal_ligand_distances_angstrom=(2.0, 2.1, 2.0, 2.1),
            minimum_metal_ligand_distance_angstrom=1.5,
            maximum_metal_ligand_distance_angstrom=3.0,
            active_space_contains_metal_orbitals=True,
            spin_state_consistent=True,
        ),
        MolecularDynamicsRequest(
            request_identifier="verification.md.family",
            subject_identifier="subject.md",
            calculations=(_calculation("calc.md", energy=None),),
            requested_steps=1000,
            completed_steps=1000,
            timestep_femtosecond=2.0,
            target_temperature_kelvin=300.0,
            mean_temperature_kelvin=302.0,
            temperature_tolerance_kelvin=10.0,
            energy_drift_hartree_per_picosecond=0.001,
            maximum_absolute_energy_drift_hartree_per_picosecond=0.01,
            equilibration_steps=200,
            production_steps=800,
            minimum_production_steps=500,
            trajectory_values_finite=True,
            constraints_satisfied=True,
        ),
        CandidateRankingRequest(
            request_identifier="verification.ranking.family",
            subject_identifier="subject.ranking",
            calculations=(
                _calculation("calc.a", energy=-1.1),
                _calculation(
                    "calc.b",
                    energy=-1.0,
                    geometry_character="d",
                ),
            ),
            candidates=(
                RankedCandidate(
                    calculation_identifier="calc.a",
                    score=0.9,
                    score_uncertainty=0.01,
                    eligible=True,
                    scientific_verification_passed=True,
                ),
                RankedCandidate(
                    calculation_identifier="calc.b",
                    score=0.5,
                    score_uncertainty=0.01,
                    eligible=True,
                    scientific_verification_passed=True,
                ),
            ),
            higher_score_is_better=True,
            minimum_score_separation=0.1,
        ),
    ],
    ids=(
        "energy-comparison",
        "potential-energy-curve",
        "interaction-energy",
        "transition-state",
        "conformer-comparison",
        "metal-centre",
        "molecular-dynamics",
        "candidate-ranking",
    ),
)
def test_all_eight_objective_families_pass_valid_engine_neutral_evidence(
    verification_request,
) -> None:
    report = default_scientific_verifier_registry().verify(verification_request)

    assert report.objective_family == verification_request.objective_family
    assert report.overall_outcome is VerificationOutcome.PASSED
    assert report.execution_integrity_passed is True
    assert report.scientific_quality_passed is True
    assert report.authorization_passed is True
    assert len(report.dimension_results) == 7


def test_transition_state_family_rejects_execution_success_with_wrong_saddle_order() -> (
    None
):
    report = default_scientific_verifier_registry().verify(
        TransitionStateRequest(
            request_identifier="verification.ts.wrong-order",
            subject_identifier="subject.ts",
            calculations=(_calculation("calc.ts"),),
            imaginary_frequencies_cm_inverse=(-500.0, -250.0),
            gradient_norm_hartree_per_bohr=1e-5,
            maximum_gradient_norm_hartree_per_bohr=1e-4,
            minimum_imaginary_frequency_magnitude_cm_inverse=100.0,
            connects_reactant_and_product=True,
        )
    )

    assert report.execution_integrity_passed is True
    assert report.scientific_quality_passed is False
    plausibility = _result(report, VerificationDimension.SCIENTIFIC_PLAUSIBILITY)
    assert {item.finding_identifier for item in plausibility.findings} == {
        "verification.plausibility.transition_state_order"
    }


def test_md_family_separates_execution_integrity_from_sampling_quality() -> None:
    report = default_scientific_verifier_registry().verify(
        MolecularDynamicsRequest(
            request_identifier="verification.md.sampling-fail",
            subject_identifier="subject.md",
            calculations=(_calculation("calc.md", energy=None),),
            requested_steps=1000,
            completed_steps=1000,
            timestep_femtosecond=2.0,
            target_temperature_kelvin=300.0,
            mean_temperature_kelvin=300.0,
            temperature_tolerance_kelvin=10.0,
            energy_drift_hartree_per_picosecond=0.001,
            maximum_absolute_energy_drift_hartree_per_picosecond=0.01,
            equilibration_steps=900,
            production_steps=100,
            minimum_production_steps=500,
            trajectory_values_finite=True,
            constraints_satisfied=True,
        )
    )

    assert report.execution_integrity_passed is True
    assert (
        _result(report, VerificationDimension.UNCERTAINTY).outcome
        is VerificationOutcome.FAILED
    )
    assert report.scientific_quality_passed is False


class _CustomVerifier(BaseObjectiveScientificVerifier):
    objective_family = "custom_objective"
    verifier_identifier = "verifier.custom"

    def objective_findings(self, request):
        return (
            finding(
                "verification.plausibility.custom",
                VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
                "Custom verifier intentionally rejects this fixture.",
                blocking=True,
            ),
        )


def test_registry_is_extensible_beyond_built_in_objective_families() -> None:
    registry = ScientificVerifierRegistry((_CustomVerifier(),))
    request = ScientificVerificationRequest(
        request_identifier="verification.custom",
        objective_family="custom_objective",
        subject_identifier="subject.custom",
        calculations=(_calculation("calc.a"),),
    )

    report = registry.verify(request)

    assert registry.families() == ("custom_objective",)
    assert report.objective_family == "custom_objective"
    assert report.execution_integrity_passed is True
    assert report.scientific_quality_passed is False


def test_registry_rejects_duplicate_objective_family_registration() -> None:
    registry = ScientificVerifierRegistry((_CustomVerifier(),))

    with pytest.raises(DuplicateScientificVerifierError):
        registry.register(_CustomVerifier())


def test_public_import_does_not_load_scientific_engines() -> None:
    import subprocess

    program = """
import sys

import cgr.scientific_verification

prefixes = (
    "openmm",
    "pyscf",
    "qiskit",
    "qiskit_nature",
    "qiskit_aer",
    "rdkit",
)

loaded = sorted(
    name
    for name in sys.modules
    if any(
        name == prefix or name.startswith(prefix + ".")
        for prefix in prefixes
    )
)

if loaded:
    raise SystemExit(
        "scientific engines loaded by public import: "
        + ", ".join(loaded)
    )
"""

    result = subprocess.run(
        [sys.executable, "-c", program],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
