"""Built-in objective-specific verifier families for Phase 6."""

from __future__ import annotations

import math

from cgr.science import BoundedMetadata

from .contracts import (
    CandidateRankingRequest,
    ConformerComparisonRequest,
    EnergyComparisonRequest,
    InteractionEnergyRequest,
    MetalCentreRequest,
    MolecularDynamicsRequest,
    ObjectiveVerifierFamily,
    PotentialEnergyCurveRequest,
    ScientificVerificationFinding,
    ScientificVerificationRequestType,
    TransitionStateRequest,
    VerificationDimension,
)
from .framework import (
    BaseObjectiveScientificVerifier,
    ScientificVerifierRegistry,
    calculation_map,
    finding,
)


def _energy_missing(
    calculation_identifier: str,
) -> ScientificVerificationFinding:
    return finding(
        "verification.numerical.energy_missing",
        VerificationDimension.NUMERICAL_INTEGRITY,
        "Objective verification requires a total energy for this calculation.",
        subject_identifier=calculation_identifier,
        expected="finite_total_energy_hartree",
        observed="missing",
        evidence_identifiers=(calculation_identifier,),
    )


class EnergyComparisonVerifier(BaseObjectiveScientificVerifier):
    """Verify comparable energies and whether their ordering is scientifically resolved."""

    objective_family = "energy_comparison"
    verifier_identifier = "verifier.energy_comparison"

    def objective_findings(
        self, request: ScientificVerificationRequestType
    ) -> tuple[ScientificVerificationFinding, ...]:
        if not isinstance(request, EnergyComparisonRequest):
            raise TypeError(
                "Energy-comparison verifier requires EnergyComparisonRequest."
            )
        findings: list[ScientificVerificationFinding] = []
        energies = {
            item.calculation_identifier: item.total_energy_hartree
            for item in request.calculations
        }
        for identifier, energy in energies.items():
            if energy is None:
                findings.append(_energy_missing(identifier))
        available = {
            identifier: energy
            for identifier, energy in energies.items()
            if energy is not None
        }
        if len(available) < 2:
            return tuple(findings)

        ordered = sorted(available.items(), key=lambda item: (item[1], item[0]))
        lowest_identifier, lowest_energy = ordered[0]
        second_identifier, second_energy = ordered[1]
        gap = second_energy - lowest_energy
        if gap < request.minimum_resolvable_difference_hartree:
            findings.append(
                finding(
                    "verification.uncertainty.energy_difference_unresolved",
                    VerificationDimension.UNCERTAINTY,
                    "The lowest two energies are closer than the declared resolvable difference.",
                    subject_identifier=request.subject_identifier,
                    expected=request.minimum_resolvable_difference_hartree,
                    observed=gap,
                    evidence_identifiers=(lowest_identifier, second_identifier),
                )
            )

        evidence = calculation_map(request.calculations)
        lowest_uncertainty = evidence[lowest_identifier].uncertainty_hartree
        second_uncertainty = evidence[second_identifier].uncertainty_hartree
        if lowest_uncertainty is not None and second_uncertainty is not None:
            combined = lowest_uncertainty + second_uncertainty
            if gap <= combined:
                findings.append(
                    finding(
                        "verification.uncertainty.energy_intervals_overlap",
                        VerificationDimension.UNCERTAINTY,
                        "The leading energy ordering is not resolved beyond combined uncertainty.",
                        subject_identifier=request.subject_identifier,
                        expected=f"gap>{combined}",
                        observed=gap,
                        evidence_identifiers=(lowest_identifier, second_identifier),
                    )
                )

        if (
            request.expected_lowest_calculation_identifier is not None
            and lowest_identifier != request.expected_lowest_calculation_identifier
        ):
            findings.append(
                finding(
                    "verification.plausibility.energy_order_mismatch",
                    VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
                    "Observed lowest-energy calculation differs from the declared scientific expectation.",
                    subject_identifier=request.subject_identifier,
                    expected=request.expected_lowest_calculation_identifier,
                    observed=lowest_identifier,
                    evidence_identifiers=tuple(available),
                )
            )
        return tuple(findings)

    def dimension_metrics(
        self, request: ScientificVerificationRequestType
    ) -> dict[VerificationDimension, BoundedMetadata]:
        if not isinstance(request, EnergyComparisonRequest):
            return {}
        energies = [
            item.total_energy_hartree
            for item in request.calculations
            if item.total_energy_hartree is not None
        ]
        if len(energies) < 2:
            return {}
        ordered = sorted(energies)
        return {
            VerificationDimension.SCIENTIFIC_PLAUSIBILITY: {
                "energy_spread_hartree": max(energies) - min(energies),
            },
            VerificationDimension.UNCERTAINTY: {
                "leading_energy_gap_hartree": ordered[1] - ordered[0],
            },
        }


class PotentialEnergyCurveVerifier(BaseObjectiveScientificVerifier):
    """Verify reaction-coordinate coverage, numerical continuity and optional minima."""

    objective_family = "potential_energy_curve"
    verifier_identifier = "verifier.potential_energy_curve"

    def objective_findings(
        self, request: ScientificVerificationRequestType
    ) -> tuple[ScientificVerificationFinding, ...]:
        if not isinstance(request, PotentialEnergyCurveRequest):
            raise TypeError(
                "Potential-energy-curve verifier requires PotentialEnergyCurveRequest."
            )
        findings: list[ScientificVerificationFinding] = []
        calculations = calculation_map(request.calculations)
        coordinates = [point.reaction_coordinate for point in request.points]
        if len(coordinates) != len(set(coordinates)):
            findings.append(
                finding(
                    "verification.comparability.duplicate_reaction_coordinate",
                    VerificationDimension.CROSS_CALCULATION_COMPARABILITY,
                    "Potential-energy curve contains duplicate reaction-coordinate values.",
                    subject_identifier=request.subject_identifier,
                )
            )

        ordered_points = sorted(
            request.points,
            key=lambda item: (item.reaction_coordinate, item.calculation_identifier),
        )
        ordered_energies: list[tuple[str, float]] = []
        for point in ordered_points:
            energy = calculations[point.calculation_identifier].total_energy_hartree
            if energy is None:
                findings.append(_energy_missing(point.calculation_identifier))
            else:
                ordered_energies.append((point.calculation_identifier, energy))

        if request.maximum_adjacent_energy_jump_hartree is not None and len(
            ordered_energies
        ) == len(ordered_points):
            for (left_id, left_energy), (right_id, right_energy) in zip(
                ordered_energies, ordered_energies[1:]
            ):
                jump = abs(right_energy - left_energy)
                if jump > request.maximum_adjacent_energy_jump_hartree:
                    findings.append(
                        finding(
                            "verification.numerical.curve_discontinuity",
                            VerificationDimension.NUMERICAL_INTEGRITY,
                            "Adjacent potential-energy points exceed the declared continuity bound.",
                            expected=request.maximum_adjacent_energy_jump_hartree,
                            observed=jump,
                            evidence_identifiers=(left_id, right_id),
                        )
                    )

        if request.require_interior_minimum and len(ordered_energies) == len(
            ordered_points
        ):
            minimum_index = min(
                range(len(ordered_energies)),
                key=lambda index: (
                    ordered_energies[index][1],
                    ordered_energies[index][0],
                ),
            )
            if minimum_index in {0, len(ordered_energies) - 1}:
                findings.append(
                    finding(
                        "verification.plausibility.curve_minimum_at_boundary",
                        VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
                        "The required potential-energy minimum lies at the sampled boundary.",
                        subject_identifier=request.subject_identifier,
                        expected="interior_minimum",
                        observed="boundary_minimum",
                    )
                )
        return tuple(findings)

    def dimension_metrics(
        self, request: ScientificVerificationRequestType
    ) -> dict[VerificationDimension, BoundedMetadata]:
        if not isinstance(request, PotentialEnergyCurveRequest):
            return {}
        coordinates = [item.reaction_coordinate for item in request.points]
        return {
            VerificationDimension.CROSS_CALCULATION_COMPARABILITY: {
                "curve_point_count": len(request.points),
                "reaction_coordinate_span": max(coordinates) - min(coordinates),
            }
        }


class InteractionEnergyVerifier(BaseObjectiveScientificVerifier):
    """Verify complex-minus-fragment energy arithmetic on a common method identity."""

    objective_family = "interaction_energy"
    verifier_identifier = "verifier.interaction_energy"

    def objective_findings(
        self, request: ScientificVerificationRequestType
    ) -> tuple[ScientificVerificationFinding, ...]:
        if not isinstance(request, InteractionEnergyRequest):
            raise TypeError(
                "Interaction-energy verifier requires InteractionEnergyRequest."
            )
        findings: list[ScientificVerificationFinding] = []
        calculations = calculation_map(request.calculations)
        complex_energy = calculations[
            request.complex_calculation_identifier
        ].total_energy_hartree
        fragment_energies = [
            calculations[identifier].total_energy_hartree
            for identifier in request.fragment_calculation_identifiers
        ]
        if complex_energy is None:
            findings.append(_energy_missing(request.complex_calculation_identifier))
        for identifier, energy in zip(
            request.fragment_calculation_identifiers, fragment_energies
        ):
            if energy is None:
                findings.append(_energy_missing(identifier))
        if complex_energy is None or any(value is None for value in fragment_energies):
            return tuple(findings)

        computed = complex_energy - math.fsum(
            float(value) for value in fragment_energies if value is not None
        )
        difference = abs(computed - request.reported_interaction_energy_hartree)
        if difference > request.consistency_tolerance_hartree:
            findings.append(
                finding(
                    "verification.plausibility.interaction_energy_inconsistent",
                    VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
                    "Reported interaction energy is inconsistent with complex-minus-fragment energies.",
                    subject_identifier=request.subject_identifier,
                    expected=computed,
                    observed=request.reported_interaction_energy_hartree,
                    evidence_identifiers=(
                        request.complex_calculation_identifier,
                        *request.fragment_calculation_identifiers,
                    ),
                )
            )
        return tuple(findings)

    def dimension_metrics(
        self, request: ScientificVerificationRequestType
    ) -> dict[VerificationDimension, BoundedMetadata]:
        if not isinstance(request, InteractionEnergyRequest):
            return {}
        calculations = calculation_map(request.calculations)
        complex_energy = calculations[
            request.complex_calculation_identifier
        ].total_energy_hartree
        fragments = [
            calculations[item].total_energy_hartree
            for item in request.fragment_calculation_identifiers
        ]
        if complex_energy is None or any(value is None for value in fragments):
            return {}
        computed = complex_energy - math.fsum(
            float(value) for value in fragments if value is not None
        )
        return {
            VerificationDimension.SCIENTIFIC_PLAUSIBILITY: {
                "computed_interaction_energy_hartree": computed,
                "interaction_energy_residual_hartree": abs(
                    computed - request.reported_interaction_energy_hartree
                ),
            }
        }


class TransitionStateVerifier(BaseObjectiveScientificVerifier):
    """Verify first-order saddle evidence and path connectivity."""

    objective_family = "transition_state"
    verifier_identifier = "verifier.transition_state"

    def objective_findings(
        self, request: ScientificVerificationRequestType
    ) -> tuple[ScientificVerificationFinding, ...]:
        if not isinstance(request, TransitionStateRequest):
            raise TypeError(
                "Transition-state verifier requires TransitionStateRequest."
            )
        findings: list[ScientificVerificationFinding] = []
        negative = tuple(
            value for value in request.imaginary_frequencies_cm_inverse if value < 0
        )
        if len(negative) != 1:
            findings.append(
                finding(
                    "verification.plausibility.transition_state_order",
                    VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
                    "A first-order transition state requires exactly one imaginary frequency.",
                    subject_identifier=request.subject_identifier,
                    expected=1,
                    observed=len(negative),
                )
            )
        elif (
            abs(negative[0]) < request.minimum_imaginary_frequency_magnitude_cm_inverse
        ):
            findings.append(
                finding(
                    "verification.plausibility.imaginary_frequency_too_small",
                    VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
                    "Imaginary frequency magnitude is below the declared transition-state threshold.",
                    expected=request.minimum_imaginary_frequency_magnitude_cm_inverse,
                    observed=abs(negative[0]),
                )
            )
        if (
            request.gradient_norm_hartree_per_bohr
            > request.maximum_gradient_norm_hartree_per_bohr
        ):
            findings.append(
                finding(
                    "verification.numerical.transition_state_gradient",
                    VerificationDimension.NUMERICAL_INTEGRITY,
                    "Transition-state gradient norm exceeds the convergence threshold.",
                    expected=request.maximum_gradient_norm_hartree_per_bohr,
                    observed=request.gradient_norm_hartree_per_bohr,
                )
            )
        if not request.connects_reactant_and_product:
            findings.append(
                finding(
                    "verification.plausibility.transition_path_unconfirmed",
                    VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
                    "Transition-state evidence does not connect the intended reactant and product.",
                    expected=True,
                    observed=False,
                )
            )
        return tuple(findings)

    def dimension_metrics(
        self, request: ScientificVerificationRequestType
    ) -> dict[VerificationDimension, BoundedMetadata]:
        if not isinstance(request, TransitionStateRequest):
            return {}
        negative = [
            value for value in request.imaginary_frequencies_cm_inverse if value < 0
        ]
        return {
            VerificationDimension.SCIENTIFIC_PLAUSIBILITY: {
                "imaginary_frequency_count": len(negative),
            },
            VerificationDimension.NUMERICAL_INTEGRITY: {
                "gradient_norm_hartree_per_bohr": request.gradient_norm_hartree_per_bohr,
            },
        }


class ConformerComparisonVerifier(BaseObjectiveScientificVerifier):
    """Verify distinct conformers and resolve their relative-energy ordering."""

    objective_family = "conformer_comparison"
    verifier_identifier = "verifier.conformer_comparison"

    def objective_findings(
        self, request: ScientificVerificationRequestType
    ) -> tuple[ScientificVerificationFinding, ...]:
        if not isinstance(request, ConformerComparisonRequest):
            raise TypeError(
                "Conformer-comparison verifier requires ConformerComparisonRequest."
            )
        findings: list[ScientificVerificationFinding] = []
        geometries = {item.geometry_sha256 for item in request.calculations}
        if len(geometries) < request.minimum_distinct_geometry_count:
            findings.append(
                finding(
                    "verification.plausibility.conformers_not_distinct",
                    VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
                    "Conformer comparison does not contain enough distinct geometries.",
                    expected=request.minimum_distinct_geometry_count,
                    observed=len(geometries),
                )
            )
        for calculation in request.calculations:
            if calculation.total_energy_hartree is None:
                findings.append(_energy_missing(calculation.calculation_identifier))

        available = [
            item
            for item in request.calculations
            if item.total_energy_hartree is not None
        ]
        if len(available) >= 2:
            ordered = sorted(
                available,
                key=lambda item: (
                    float(item.total_energy_hartree),
                    item.calculation_identifier,
                ),
            )
            first, second = ordered[0], ordered[1]
            if (
                first.uncertainty_hartree is not None
                and second.uncertainty_hartree is not None
            ):
                gap = float(second.total_energy_hartree) - float(
                    first.total_energy_hartree
                )
                combined = first.uncertainty_hartree + second.uncertainty_hartree
                if gap <= combined:
                    findings.append(
                        finding(
                            "verification.uncertainty.conformer_order_unresolved",
                            VerificationDimension.UNCERTAINTY,
                            "Lowest conformer cannot be distinguished beyond combined uncertainty.",
                            expected=f"gap>{combined}",
                            observed=gap,
                            evidence_identifiers=(
                                first.calculation_identifier,
                                second.calculation_identifier,
                            ),
                        )
                    )
        return tuple(findings)

    def dimension_metrics(
        self, request: ScientificVerificationRequestType
    ) -> dict[VerificationDimension, BoundedMetadata]:
        if not isinstance(request, ConformerComparisonRequest):
            return {}
        return {
            VerificationDimension.SCIENTIFIC_PLAUSIBILITY: {
                "distinct_geometry_count": len(
                    {item.geometry_sha256 for item in request.calculations}
                ),
                "conformer_count": len(request.calculations),
            }
        }


class MetalCentreVerifier(BaseObjectiveScientificVerifier):
    """Verify declared coordination, distances, spin and metal-active-space evidence."""

    objective_family = "metal_centre"
    verifier_identifier = "verifier.metal_centre"

    def objective_findings(
        self, request: ScientificVerificationRequestType
    ) -> tuple[ScientificVerificationFinding, ...]:
        if not isinstance(request, MetalCentreRequest):
            raise TypeError("Metal-centre verifier requires MetalCentreRequest.")
        findings: list[ScientificVerificationFinding] = []
        if request.coordination_number not in request.allowed_coordination_numbers:
            findings.append(
                finding(
                    "verification.plausibility.metal_coordination_number",
                    VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
                    "Metal coordination number is outside the declared chemically acceptable set.",
                    expected=",".join(
                        str(item) for item in request.allowed_coordination_numbers
                    ),
                    observed=request.coordination_number,
                )
            )
        for distance in request.metal_ligand_distances_angstrom:
            if not (
                request.minimum_metal_ligand_distance_angstrom
                <= distance
                <= request.maximum_metal_ligand_distance_angstrom
            ):
                findings.append(
                    finding(
                        "verification.plausibility.metal_ligand_distance",
                        VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
                        "Metal-ligand distance lies outside declared chemical bounds.",
                        expected=(
                            f"{request.minimum_metal_ligand_distance_angstrom}:"
                            f"{request.maximum_metal_ligand_distance_angstrom}"
                        ),
                        observed=distance,
                    )
                )
        if not request.active_space_contains_metal_orbitals:
            findings.append(
                finding(
                    "verification.identity.metal_active_space_missing",
                    VerificationDimension.IDENTITY_INTEGRITY,
                    "Electronic active space does not include the required metal-centred orbitals.",
                    expected=True,
                    observed=False,
                )
            )
        if not request.spin_state_consistent:
            findings.append(
                finding(
                    "verification.plausibility.metal_spin_state",
                    VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
                    "Metal-centre spin-state evidence is inconsistent.",
                    expected=True,
                    observed=False,
                )
            )
        return tuple(findings)

    def dimension_metrics(
        self, request: ScientificVerificationRequestType
    ) -> dict[VerificationDimension, BoundedMetadata]:
        if not isinstance(request, MetalCentreRequest):
            return {}
        return {
            VerificationDimension.SCIENTIFIC_PLAUSIBILITY: {
                "coordination_number": request.coordination_number,
                "minimum_observed_distance_angstrom": min(
                    request.metal_ligand_distances_angstrom
                ),
                "maximum_observed_distance_angstrom": max(
                    request.metal_ligand_distances_angstrom
                ),
            }
        }


class MolecularDynamicsVerifier(BaseObjectiveScientificVerifier):
    """Verify trajectory completion, stability, temperature and sampling adequacy."""

    objective_family = "molecular_dynamics"
    verifier_identifier = "verifier.molecular_dynamics"

    def objective_findings(
        self, request: ScientificVerificationRequestType
    ) -> tuple[ScientificVerificationFinding, ...]:
        if not isinstance(request, MolecularDynamicsRequest):
            raise TypeError(
                "Molecular-dynamics verifier requires MolecularDynamicsRequest."
            )
        findings: list[ScientificVerificationFinding] = []
        if request.completed_steps < request.requested_steps:
            findings.append(
                finding(
                    "verification.execution.md_trajectory_incomplete",
                    VerificationDimension.EXECUTION_INTEGRITY,
                    "Molecular-dynamics trajectory ended before the requested step count.",
                    expected=request.requested_steps,
                    observed=request.completed_steps,
                )
            )
        if not request.trajectory_values_finite:
            findings.append(
                finding(
                    "verification.numerical.md_nonfinite_trajectory",
                    VerificationDimension.NUMERICAL_INTEGRITY,
                    "Molecular-dynamics trajectory contains non-finite values.",
                    expected=True,
                    observed=False,
                )
            )
        temperature_deviation = abs(
            request.mean_temperature_kelvin - request.target_temperature_kelvin
        )
        if temperature_deviation > request.temperature_tolerance_kelvin:
            findings.append(
                finding(
                    "verification.plausibility.md_temperature_outside_tolerance",
                    VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
                    "Mean trajectory temperature lies outside the declared tolerance.",
                    expected=request.temperature_tolerance_kelvin,
                    observed=temperature_deviation,
                )
            )
        absolute_drift = abs(request.energy_drift_hartree_per_picosecond)
        if absolute_drift > (
            request.maximum_absolute_energy_drift_hartree_per_picosecond
        ):
            findings.append(
                finding(
                    "verification.numerical.md_energy_drift",
                    VerificationDimension.NUMERICAL_INTEGRITY,
                    "Molecular-dynamics energy drift exceeds the declared stability bound.",
                    expected=request.maximum_absolute_energy_drift_hartree_per_picosecond,
                    observed=absolute_drift,
                )
            )
        if request.production_steps < request.minimum_production_steps:
            findings.append(
                finding(
                    "verification.uncertainty.md_sampling_insufficient",
                    VerificationDimension.UNCERTAINTY,
                    "Production trajectory is shorter than the declared sampling requirement.",
                    expected=request.minimum_production_steps,
                    observed=request.production_steps,
                )
            )
        if not request.constraints_satisfied:
            findings.append(
                finding(
                    "verification.plausibility.md_constraints_failed",
                    VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
                    "Molecular-dynamics structural constraints were not satisfied.",
                    expected=True,
                    observed=False,
                )
            )
        return tuple(findings)

    def dimension_metrics(
        self, request: ScientificVerificationRequestType
    ) -> dict[VerificationDimension, BoundedMetadata]:
        if not isinstance(request, MolecularDynamicsRequest):
            return {}
        return {
            VerificationDimension.EXECUTION_INTEGRITY: {
                "requested_steps": request.requested_steps,
                "completed_steps": request.completed_steps,
            },
            VerificationDimension.NUMERICAL_INTEGRITY: {
                "absolute_energy_drift_hartree_per_picosecond": abs(
                    request.energy_drift_hartree_per_picosecond
                ),
            },
            VerificationDimension.SCIENTIFIC_PLAUSIBILITY: {
                "temperature_deviation_kelvin": abs(
                    request.mean_temperature_kelvin - request.target_temperature_kelvin
                ),
            },
            VerificationDimension.UNCERTAINTY: {
                "production_steps": request.production_steps,
            },
        }


class CandidateRankingVerifier(BaseObjectiveScientificVerifier):
    """Verify ranking eligibility, upstream scientific quality and score separation."""

    objective_family = "candidate_ranking"
    verifier_identifier = "verifier.candidate_ranking"

    def objective_findings(
        self, request: ScientificVerificationRequestType
    ) -> tuple[ScientificVerificationFinding, ...]:
        if not isinstance(request, CandidateRankingRequest):
            raise TypeError(
                "Candidate-ranking verifier requires CandidateRankingRequest."
            )
        findings: list[ScientificVerificationFinding] = []
        for candidate in request.candidates:
            if not candidate.eligible:
                findings.append(
                    finding(
                        "verification.plausibility.ranking_ineligible_candidate",
                        VerificationDimension.SCIENTIFIC_PLAUSIBILITY,
                        "Candidate ranking contains an ineligible candidate.",
                        subject_identifier=candidate.calculation_identifier,
                        expected=True,
                        observed=False,
                    )
                )
            if (
                request.require_all_candidates_scientifically_verified
                and not candidate.scientific_verification_passed
            ):
                findings.append(
                    finding(
                        "verification.identity.ranking_unverified_candidate",
                        VerificationDimension.IDENTITY_INTEGRITY,
                        "Candidate ranking contains a candidate that did not pass upstream scientific verification.",
                        subject_identifier=candidate.calculation_identifier,
                        expected=True,
                        observed=False,
                    )
                )

        ordered = sorted(
            request.candidates,
            key=lambda item: (
                -item.score if request.higher_score_is_better else item.score,
                item.calculation_identifier,
            ),
        )
        leader, runner_up = ordered[0], ordered[1]
        raw_separation = abs(leader.score - runner_up.score)
        required_separation = (
            request.minimum_score_separation
            + leader.score_uncertainty
            + runner_up.score_uncertainty
        )
        if raw_separation < required_separation:
            findings.append(
                finding(
                    "verification.uncertainty.ranking_unresolved",
                    VerificationDimension.UNCERTAINTY,
                    "Leading candidates are not separated beyond the declared score and uncertainty margin.",
                    subject_identifier=request.subject_identifier,
                    expected=required_separation,
                    observed=raw_separation,
                    evidence_identifiers=(
                        leader.calculation_identifier,
                        runner_up.calculation_identifier,
                    ),
                )
            )
        return tuple(findings)

    def dimension_metrics(
        self, request: ScientificVerificationRequestType
    ) -> dict[VerificationDimension, BoundedMetadata]:
        if not isinstance(request, CandidateRankingRequest):
            return {}
        ordered = sorted(
            request.candidates,
            key=lambda item: (
                -item.score if request.higher_score_is_better else item.score,
                item.calculation_identifier,
            ),
        )
        return {
            VerificationDimension.UNCERTAINTY: {
                "leading_score_separation": abs(ordered[0].score - ordered[1].score),
                "candidate_count": len(ordered),
            }
        }


def built_in_objective_verifiers() -> tuple[BaseObjectiveScientificVerifier, ...]:
    """Return all built-in Phase 6 verifier families in stable enum order."""

    by_family: dict[str, BaseObjectiveScientificVerifier] = {
        "energy_comparison": EnergyComparisonVerifier(),
        "potential_energy_curve": PotentialEnergyCurveVerifier(),
        "interaction_energy": InteractionEnergyVerifier(),
        "transition_state": TransitionStateVerifier(),
        "conformer_comparison": ConformerComparisonVerifier(),
        "metal_centre": MetalCentreVerifier(),
        "molecular_dynamics": MolecularDynamicsVerifier(),
        "candidate_ranking": CandidateRankingVerifier(),
    }
    return tuple(by_family[family.value] for family in ObjectiveVerifierFamily)


def default_scientific_verifier_registry() -> ScientificVerifierRegistry:
    """Return a registry containing all eight built-in objective families."""

    return ScientificVerifierRegistry(built_in_objective_verifiers())
