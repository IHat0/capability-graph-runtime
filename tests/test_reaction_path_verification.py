"""Native electronic TS evidence to universal verifier bridge."""

from __future__ import annotations

from cgr.electronic_structure import (
    ElectronicAtom,
    ElectronicFrequencyAnalysis,
    ElectronicFrequencyMode,
    ElectronicMolecule,
    ElectronicReactionPathPoint,
    ElectronicReactionPathResult,
    ElectronicTensor,
    ElectronicTransitionStateSearch,
    transition_state_verification_request,
)
from cgr.kernel.contracts import CapabilityVersion
from cgr.scientific_verification import TransitionStateVerifier, VerificationOutcome

VERSION = CapabilityVersion(major=1, minor=0, patch=0)


def _molecule(identifier: str, distance: float) -> ElectronicMolecule:
    return ElectronicMolecule(
        schema_version=VERSION,
        molecule_identifier=identifier,
        source_artifact_identifier="source-ts",
        source_geometry_identifier="geometry-ts",
        atoms=(
            ElectronicAtom(atom_index=0, atomic_number=1, element_symbol="H", x_angstrom=0, y_angstrom=0, z_angstrom=0),
            ElectronicAtom(atom_index=1, atomic_number=1, element_symbol="H", x_angstrom=distance, y_angstrom=0, z_angstrom=0),
        ),
        molecular_charge=0,
        source_formal_charge=0,
        spin=0,
        electron_count=2,
        alpha_electron_count=1,
        beta_electron_count=1,
    )


def test_characterized_saddle_and_two_sided_path_feed_existing_verifier() -> None:
    saddle = _molecule("molecule-ts", 1.0)
    search = ElectronicTransitionStateSearch(
        schema_version=VERSION,
        search_identifier="search-ts",
        initial_molecule_identifier="molecule-initial",
        configuration_identifier="configuration-ts",
        optimizer_version="1.1.1",
        converged=True,
        maximum_steps=100,
        intended_reaction_atom_pair=(0, 1),
        optimized_molecule=saddle,
        final_energy_hartree=-1.0,
        final_rms_gradient_hartree_per_bohr=1.0e-5,
        final_maximum_gradient_hartree_per_bohr=2.0e-5,
    )
    mode = ElectronicTensor(
        shape=(2, 3), values=(1, 0, 0, -1, 0, 0), unit="dimensionless",
        index_convention="atom_by_cartesian",
    )
    frequency = ElectronicFrequencyAnalysis(
        schema_version=VERSION,
        analysis_identifier="frequency-ts",
        molecule_identifier=saddle.molecule_identifier,
        configuration_identifier="configuration-final-ts",
        gradient_result_identifier="gradient-ts",
        hessian_hartree_per_bohr2=ElectronicTensor(
            shape=(2, 2, 3, 3), values=(0,) * 36, unit="hartree_per_bohr2",
            index_convention="atom_atom_cartesian_cartesian",
        ),
        modes=(ElectronicFrequencyMode(
            mode_index=0, wavenumber_cm_inverse=-400, reduced_mass_amu=1,
            imaginary=True, significant_imaginary=True,
            normalized_displacements=mode, reaction_coordinate_participation=0.8,
        ),),
        significant_imaginary_mode_count=1,
        exactly_one_significant_imaginary_mode=True,
    )
    points = tuple(
        ElectronicReactionPathPoint(
            direction=direction, step_index=index,
            molecule=_molecule(f"molecule-{direction}-{index}", distance),
            energy_hartree=-1.01 - 0.01 * index,
            rms_gradient_hartree_per_bohr=0.01,
        )
        for direction, distances in (("forward", (1.1, 1.2)), ("reverse", (0.9, 0.8)))
        for index, distance in enumerate(distances)
    )
    path = ElectronicReactionPathResult(
        schema_version=VERSION,
        path_identifier="reaction-path-ts",
        transition_state_search_identifier=search.search_identifier,
        frequency_analysis_identifier=frequency.analysis_identifier,
        method="mass_weighted_steepest_descent",
        step_size_bohr=0.05,
        points=points,
        forward_energy_decreased=True,
        reverse_energy_decreased=True,
        distinct_endpoints=True,
        path_confirmation_passed=True,
    )

    request = transition_state_verification_request(
        search=search,
        frequency=frequency,
        reaction_path=path,
        maximum_gradient_norm_hartree_per_bohr=1.0e-4,
    )
    report = TransitionStateVerifier().verify(request)

    assert request.connects_reactant_and_product
    assert report.overall_outcome is VerificationOutcome.PASSED
    assert report.scientific_quality_passed
