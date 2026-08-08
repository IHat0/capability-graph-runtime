"""Phase 4 PySCF capability and electronic-artifact regressions."""

from __future__ import annotations

import hashlib
import sys

import pytest
from pydantic import ValidationError

from cgr.electronic_structure import (
    ACTIVE_SPACE_CONSTRUCT,
    ACTIVE_SPACE_SELECT,
    CONFIGURATION_DEFINE,
    FREQUENCY_ANALYZE,
    GRADIENT_CALCULATE,
    HARTREE_FOCK,
    IMPLICIT_SOLVENT_HARTREE_FOCK,
    MOLECULE_CONSTRUCT,
    QM_REGION_PREPARE,
    ORBITALS_GENERATE,
    QMMM_EMBEDDING_PREPARE,
    QMMM_HARTREE_FOCK,
    REFERENCE_CALCULATE,
    TRANSITION_STATE_SEARCH,
    REACTION_PATH_CONFIRM,
    ElectronicActiveSpace,
    ElectronicActiveSpaceSelection,
    ElectronicAtom,
    ElectronicHartreeFockResult,
    ElectronicFrequencyAnalysis,
    ElectronicFrequencyMode,
    ElectronicImplicitSolventResult,
    ElectronicMolecule,
    ElectronicQMMMHartreeFockResult,
    ElectronicQMRegionPreparation,
    ElectronicOrbitalSelectionScore,
    ElectronicOrbitalSet,
    ElectronicReferenceCalculation,
    ElectronicStructureConfiguration,
    ElectronicTensor,
    PySCFElectronicStructureAdapter,
    QMMMBoundaryChargeAdjustment,
    QMMMEmbeddingFoundation,
    QMMMEmbeddingSite,
    pyscf_capability_envelopes,
)
from cgr.kernel.contracts import (
    CapabilityVersion,
    ExecutionContext,
    ExecutionStatus,
)
from cgr.molecular import (
    MolecularConformer,
    MolecularConformerSet,
    MolecularCoordinate,
    MolecularEnvironment,
    MolecularGraph,
    MolecularGraphAtom,
    MolecularGraphBond,
    MolecularPeriodicBox,
    MolecularSimulationAtom,
    MolecularSimulationBond,
    MolecularSimulationSystem,
    MolecularSystemConstructionSettings,
    MolecularVector3,
)
from cgr.science import (
    ArtifactPointer,
    ArtifactReference,
    CapabilityInvocation,
    CreationProvenance,
)

VERSION = CapabilityVersion(major=1, minor=0, patch=0)
EXPERIMENT = ArtifactPointer(
    artifact_identifier="experiment.phase4-electronic",
    content_sha256="e" * 64,
)


class MemoryPayloadStore:
    """Exact content-addressed payload store used by adapter regressions."""

    def __init__(self) -> None:
        self.payloads: dict[tuple[str, str], bytes] = {}

    def read(self, reference: ArtifactReference) -> bytes:
        return self.payloads[(reference.artifact_identifier, reference.content_sha256)]

    def write(self, reference: ArtifactReference, payload: bytes) -> None:
        assert hashlib.sha256(payload).hexdigest() == reference.content_sha256
        assert reference.byte_size == len(payload)
        key = (reference.artifact_identifier, reference.content_sha256)
        existing = self.payloads.get(key)
        assert existing is None or existing == payload
        self.payloads[key] = payload


def _source_reference(
    identifier: str,
    artifact_type: str,
    payload: bytes,
) -> ArtifactReference:
    return ArtifactReference(
        artifact_identifier=identifier,
        schema_version=VERSION,
        artifact_type=artifact_type,
        media_type="application/json",
        content_sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        provenance=CreationProvenance(
            producer="test.fixture",
            producer_version=VERSION,
        ),
    )


def _invocation(
    adapter: PySCFElectronicStructureAdapter,
    capability_name: str,
    *,
    inputs: tuple[ArtifactReference, ...] = (),
    parameters: dict[str, object] | None = None,
    execution_identifier: str = "execution.phase4-electronic",
) -> CapabilityInvocation:
    envelope = next(
        envelope
        for envelope in adapter.declaration.capabilities
        if envelope.descriptor.capability_name == capability_name
    )
    return CapabilityInvocation(
        capability=envelope.descriptor,
        input_artifacts=inputs,
        experiment=EXPERIMENT,
        context=ExecutionContext(execution_id=execution_identifier),
        parameters=parameters or {},
    )


def _h2_conformer_set() -> MolecularConformerSet:
    graph = MolecularGraph(
        schema_version=VERSION,
        graph_identifier="graph.h2",
        representation_source="prepared_graph",
        canonical_smiles="[H][H]",
        mapped_smiles="[H:1][H:2]",
        atoms=(
            MolecularGraphAtom(
                atom_index=0,
                atomic_number=1,
                element_symbol="H",
                formal_charge=0,
                chiral_tag="unspecified",
                atom_map_number=1,
                source_atom_index=0,
            ),
            MolecularGraphAtom(
                atom_index=1,
                atomic_number=1,
                element_symbol="H",
                formal_charge=0,
                chiral_tag="unspecified",
                atom_map_number=2,
                source_atom_index=1,
            ),
        ),
        bonds=(
            MolecularGraphBond(
                bond_index=0,
                atom_index_a=0,
                atom_index_b=1,
                bond_order=1.0,
                bond_type="single",
                stereo="none",
            ),
        ),
        sanitized=True,
        explicit_hydrogens=True,
        formal_charge=0,
        component_count=1,
    )
    return MolecularConformerSet(
        schema_version=VERSION,
        conformer_set_identifier="conformers.h2",
        source_graph=graph,
        generation_method="test_fixture",
        force_field="none",
        random_seed=0,
        requested_conformer_count=1,
        conformers=(
            MolecularConformer(
                conformer_identifier="conformer.h2",
                conformer_index=0,
                coordinates=(
                    MolecularCoordinate(atom_index=0, x=0.0, y=0.0, z=-0.37),
                    MolecularCoordinate(atom_index=1, x=0.0, y=0.0, z=0.37),
                ),
                optimization_status="not_requested",
            ),
        ),
    )


def _store_model(
    store: MemoryPayloadStore,
    identifier: str,
    artifact_type: str,
    model: object,
) -> ArtifactReference:
    payload = model.to_canonical_json().encode("utf-8")
    reference = _source_reference(identifier, artifact_type, payload)
    store.write(reference, payload)
    return reference


def _construct_h2(
    adapter: PySCFElectronicStructureAdapter,
    store: MemoryPayloadStore,
) -> ArtifactReference:
    source = _store_model(
        store,
        "fixture.h2-conformers",
        "molecular_conformer_set",
        _h2_conformer_set(),
    )
    result = adapter.invoke(
        _invocation(
            adapter,
            MOLECULE_CONSTRUCT,
            inputs=(source,),
            parameters={
                "molecular_charge": 0,
                "spin": 0,
                "conformer_index": 0,
            },
        )
    )
    assert result.status is ExecutionStatus.SUCCESS
    return result.output_artifacts[0]


def _configure_h2(
    adapter: PySCFElectronicStructureAdapter,
    molecule_reference: ArtifactReference,
) -> ArtifactReference:
    result = adapter.invoke(
        _invocation(
            adapter,
            CONFIGURATION_DEFINE,
            inputs=(molecule_reference,),
            parameters={
                "basis_set": "sto-3g",
                "reference_method": "rhf",
                "convergence_tolerance": 1e-10,
                "maximum_iterations": 100,
                "direct_scf": True,
                "density_fitting": False,
                "symmetry": False,
                "initial_guess": "minao",
            },
            execution_identifier="execution.phase4-configuration",
        )
    )
    assert result.status is ExecutionStatus.SUCCESS
    return result.output_artifacts[0]


def test_declaration_exposes_phase4_capabilities_without_importing_pyscf() -> None:
    before = set(sys.modules)
    envelopes = pyscf_capability_envelopes()
    after = set(sys.modules)

    assert len(envelopes) == 15
    assert {envelope.descriptor.capability_name for envelope in envelopes} == {
        QM_REGION_PREPARE,
        MOLECULE_CONSTRUCT,
        CONFIGURATION_DEFINE,
        FREQUENCY_ANALYZE,
        GRADIENT_CALCULATE,
        HARTREE_FOCK,
        IMPLICIT_SOLVENT_HARTREE_FOCK,
        ORBITALS_GENERATE,
        REFERENCE_CALCULATE,
        TRANSITION_STATE_SEARCH,
        REACTION_PATH_CONFIRM,
        ACTIVE_SPACE_SELECT,
        ACTIVE_SPACE_CONSTRUCT,
        QMMM_EMBEDDING_PREPARE,
        QMMM_HARTREE_FOCK,
    }
    assert not any(
        name == "pyscf" or name.startswith("pyscf.") for name in after - before
    )
    assert all(
        envelope.metadata["scientific_domain"] == "electronic_structure"
        for envelope in envelopes
    )


def test_tensor_and_active_space_contracts_reject_inconsistent_shapes() -> None:
    with pytest.raises(ValidationError, match="shape"):
        ElectronicTensor(
            shape=(2, 2),
            values=(1.0, 2.0),
            unit="hartree",
            index_convention="pq",
        )

    tensor_2 = ElectronicTensor(
        shape=(2, 2),
        values=(0.0, 0.0, 0.0, 0.0),
        unit="hartree",
        index_convention="active_pq",
    )
    tensor_4 = ElectronicTensor(
        shape=(2, 2, 2, 2),
        values=(0.0,) * 16,
        unit="hartree",
        index_convention="chemist_active_pqrs",
    )
    with pytest.raises(ValidationError, match="indices"):
        ElectronicActiveSpace(
            schema_version=VERSION,
            active_space_identifier="active.invalid",
            molecule_identifier="molecule.invalid",
            hartree_fock_result_identifier="hf.invalid",
            active_electron_count=2,
            alpha_electron_count=1,
            beta_electron_count=1,
            active_spatial_orbital_count=2,
            active_spin_orbital_count=4,
            active_orbital_indices=(0, 0),
            constant_energy_hartree=0.0,
            one_body_integrals=tensor_2,
            two_body_integrals=tensor_4,
        )


def test_reaction_active_space_selection_preserves_semantic_evidence() -> None:
    selection = ElectronicActiveSpaceSelection(
        schema_version=VERSION,
        selection_identifier="selection.reaction-centre",
        molecule_identifier="molecule.reaction-centre",
        hartree_fock_result_identifier="hf.reaction-centre",
        selection_method="reaction_center_projection",
        reference_method="rohf",
        orbital_basis="canonical_rohf",
        active_electron_count=3,
        active_spatial_orbital_count=3,
        active_orbital_indices=(2, 3, 4),
        target_atom_indices=(0, 1),
        target_bond_atom_pairs=((0, 1),),
        projection_threshold=0.1,
        orbital_scores=(
            ElectronicOrbitalSelectionScore(
                orbital_index=2,
                occupation=2.0,
                alpha_occupation=1.0,
                beta_occupation=1.0,
                target_projection_score=0.72,
                selection_reasons=("occupied_pair", "target_projection"),
            ),
            ElectronicOrbitalSelectionScore(
                orbital_index=3,
                occupation=1.0,
                alpha_occupation=1.0,
                beta_occupation=0.0,
                target_projection_score=0.81,
                selection_reasons=("open_shell_mandatory", "target_projection"),
            ),
            ElectronicOrbitalSelectionScore(
                orbital_index=4,
                occupation=0.0,
                alpha_occupation=0.0,
                beta_occupation=0.0,
                target_projection_score=0.64,
                selection_reasons=("virtual_partner", "target_projection"),
            ),
        ),
    )

    assert selection.target_bond_atom_pairs == ((0, 1),)
    assert selection.reference_method == "rohf"
    assert selection.orbital_scores[-1].selection_reasons == (
        "virtual_partner",
        "target_projection",
    )


def test_implicit_solvent_contract_keeps_electronic_energy_semantics() -> None:
    result = ElectronicImplicitSolventResult(
        schema_version=VERSION,
        result_identifier="implicit-solvent.water",
        molecule_identifier="molecule.water-solute",
        configuration_identifier="configuration.water-solute",
        reference_method="rhf",
        solvent_name="water",
        solvent_model="IEF-PCM",
        dielectric_constant=78.3553,
        equilibrium_solvation=True,
        converged=True,
        vacuum_total_energy_hartree=-100.0,
        solvated_total_energy_hartree=-100.025,
        electronic_solvation_contribution_hartree=-0.025,
    )

    assert result.energy_semantics == "solvated_electronic_energy"
    assert not result.thermal_correction_included
    assert not result.gibbs_free_energy

    with pytest.raises(ValidationError, match="difference"):
        ElectronicImplicitSolventResult.model_validate(
            {
                **result.model_dump(),
                "electronic_solvation_contribution_hartree": -0.5,
            }
        )


def test_frequency_analysis_requires_exact_mode_evidence() -> None:
    mode_tensor = ElectronicTensor(
        shape=(2, 3),
        values=(1.0, 0.0, 0.0, -1.0, 0.0, 0.0),
        unit="dimensionless",
        index_convention="atom_by_cartesian",
    )
    hessian = ElectronicTensor(
        shape=(2, 2, 3, 3),
        values=(0.0,) * 36,
        unit="hartree_per_bohr2",
        index_convention="atom_atom_cartesian_cartesian",
    )
    analysis = ElectronicFrequencyAnalysis(
        schema_version=VERSION,
        analysis_identifier="frequency.ts",
        molecule_identifier="molecule.ts",
        configuration_identifier="configuration.ts",
        gradient_result_identifier="gradient.ts",
        hessian_hartree_per_bohr2=hessian,
        modes=(
            ElectronicFrequencyMode(
                mode_index=0,
                wavenumber_cm_inverse=-412.0,
                reduced_mass_amu=6.0,
                imaginary=True,
                significant_imaginary=True,
                normalized_displacements=mode_tensor,
                reaction_coordinate_participation=0.83,
            ),
        ),
        significant_imaginary_threshold_cm_inverse=20.0,
        significant_imaginary_mode_count=1,
        exactly_one_significant_imaginary_mode=True,
    )

    assert analysis.exactly_one_significant_imaginary_mode
    assert analysis.modes[0].reaction_coordinate_participation == pytest.approx(0.83)


def test_uhf_common_spatial_basis_uses_unrestricted_natural_orbitals() -> None:
    numpy = pytest.importorskip("numpy")
    identity = ElectronicTensor(
        shape=(3, 3),
        values=tuple(float(value) for value in numpy.eye(3).reshape(-1)),
        unit="dimensionless",
        index_convention="ao_by_ao",
    )
    zero = ElectronicTensor(
        shape=(3, 3),
        values=(0.0,) * 9,
        unit="hartree",
        index_convention="ao_by_ao",
    )
    density_alpha = ElectronicTensor(
        shape=(3, 3),
        values=(1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        unit="electron",
        index_convention="ao_by_ao",
    )
    density_beta = ElectronicTensor(
        shape=(3, 3),
        values=(0.8, 0.0, 0.0, 0.0, 0.2, 0.0, 0.0, 0.0, 0.0),
        unit="electron",
        index_convention="ao_by_ao",
    )
    result = ElectronicHartreeFockResult(
        schema_version=VERSION,
        result_identifier="hf.uhf-uno",
        molecule_identifier="molecule.uhf-uno",
        configuration_identifier="configuration.uhf-uno",
        reference_method="uhf",
        converged=True,
        iterations=4,
        electron_count=2,
        alpha_electron_count=1,
        beta_electron_count=1,
        atomic_orbital_count=3,
        spatial_orbital_count=3,
        electronic_energy_hartree=-1.5,
        nuclear_repulsion_energy_hartree=0.5,
        total_energy_hartree=-1.0,
        orbital_energies_alpha_hartree=(-0.5, 0.1, 0.4),
        orbital_energies_beta_hartree=(-0.4, 0.2, 0.5),
        orbital_occupations_alpha=(1.0, 0.0, 0.0),
        orbital_occupations_beta=(1.0, 0.0, 0.0),
        mo_coefficients_alpha=identity,
        mo_coefficients_beta=identity,
        overlap_matrix_ao=identity,
        core_hamiltonian_ao=zero,
        density_matrix_alpha_ao=density_alpha,
        density_matrix_beta_ao=density_beta,
    )

    coefficients, occupations, alpha, beta, basis = (
        PySCFElectronicStructureAdapter._common_spatial_orbitals(numpy, result)
    )

    assert basis == "unrestricted_natural_orbital"
    assert occupations == pytest.approx((1.8, 0.2, 0.0))
    assert float(sum(alpha)) == pytest.approx(1.0)
    assert float(sum(beta)) == pytest.approx(1.0)
    assert coefficients.T @ coefficients == pytest.approx(numpy.eye(3))

def test_molecule_and_configuration_are_generic_and_explicit() -> None:
    store = MemoryPayloadStore()
    adapter = PySCFElectronicStructureAdapter(store)
    molecule_reference = _construct_h2(adapter, store)
    molecule = ElectronicMolecule.model_validate_json(store.read(molecule_reference))

    assert molecule.electron_count == 2
    assert molecule.alpha_electron_count == 1
    assert molecule.beta_electron_count == 1
    assert molecule.coordinate_unit == "angstrom"
    assert "pyscf" not in molecule.to_canonical_json().lower()

    configuration_reference = _configure_h2(adapter, molecule_reference)
    configuration = ElectronicStructureConfiguration.model_validate_json(
        store.read(configuration_reference)
    )
    assert configuration.basis_set == "sto-3g"
    assert configuration.reference_method == "rhf"
    assert configuration.convergence_tolerance == 1e-10


def test_missing_scientific_parameters_fail_closed() -> None:
    store = MemoryPayloadStore()
    adapter = PySCFElectronicStructureAdapter(store)
    source = _store_model(
        store,
        "fixture.h2-conformers",
        "molecular_conformer_set",
        _h2_conformer_set(),
    )
    result = adapter.invoke(
        _invocation(
            adapter,
            MOLECULE_CONSTRUCT,
            inputs=(source,),
            parameters={"molecular_charge": 0},
        )
    )
    assert result.status is ExecutionStatus.FAILED
    assert result.failure is not None
    assert result.failure.code == "electronic_input_invalid"


def test_configuration_rejects_rhf_for_open_shell_molecule() -> None:
    store = MemoryPayloadStore()
    adapter = PySCFElectronicStructureAdapter(store)
    source = _store_model(
        store,
        "fixture.h2-conformers",
        "molecular_conformer_set",
        _h2_conformer_set(),
    )
    molecule_result = adapter.invoke(
        _invocation(
            adapter,
            MOLECULE_CONSTRUCT,
            inputs=(source,),
            parameters={
                "molecular_charge": 1,
                "spin": 1,
                "conformer_index": 0,
            },
        )
    )
    assert molecule_result.status is ExecutionStatus.SUCCESS
    configuration_result = adapter.invoke(
        _invocation(
            adapter,
            CONFIGURATION_DEFINE,
            inputs=(molecule_result.output_artifacts[0],),
            parameters={
                "basis_set": "sto-3g",
                "reference_method": "rhf",
                "convergence_tolerance": 1e-10,
                "maximum_iterations": 100,
                "direct_scf": True,
                "density_fitting": False,
                "symmetry": False,
                "initial_guess": "minao",
            },
        )
    )
    assert configuration_result.status is ExecutionStatus.FAILED
    assert configuration_result.failure is not None
    assert configuration_result.failure.code == "electronic_input_invalid"


def _qmmm_system_fixture(
    environment: MolecularEnvironment,
    charges: tuple[float, ...],
) -> MolecularSimulationSystem:
    assert len(charges) == len(environment.atoms)

    settings = MolecularSystemConstructionSettings(
        nonbonded_method="no_cutoff",
        nonbonded_cutoff_nm=None,
        switch_distance_nm=None,
        constraints="none",
        rigid_water=False,
        remove_center_of_mass_motion=False,
        hydrogen_mass_amu=None,
        ewald_error_tolerance=None,
    )

    return MolecularSimulationSystem(
        schema_version=VERSION,
        system_identifier="system.qmmm-fixture",
        environment_identifier=environment.environment_identifier,
        force_field_selection_identifier=(
            environment.force_field_selection_identifier
        ),
        particle_count=len(environment.atoms),
        massive_particle_count=len(environment.atoms),
        constraint_count=0,
        degrees_of_freedom=3 * len(environment.atoms),
        force_kinds=("nonbonded_force",),
        total_mass_amu=float(len(environment.atoms)),
        periodic=False,
        settings=settings,
        engine_identifier="engine.openmm",
        engine_distribution_version="8.5.2",
        engine_build_version="8.5.2",
        private_state_sha256="a" * 64,
        partial_charge_source="openmm_nonbonded_force",
        particle_partial_charges_e=charges,
        total_partial_charge_e=sum(charges),
    )


def test_qmmm_foundation_uses_real_openmm_partial_charges() -> None:
    store = MemoryPayloadStore()
    adapter = PySCFElectronicStructureAdapter(store)

    environment = MolecularEnvironment(
        schema_version=VERSION,
        environment_identifier="environment.h2-mm-charge",
        environment_type="vacuum",
        source_conformer_set_identifier="conformers.h2",
        source_conformer_index=0,
        force_field_selection_identifier="force-field.test",
        atoms=(
            MolecularSimulationAtom(
                particle_index=0,
                particle_kind="atom",
                atomic_number=1,
                element_symbol="H",
                atom_name="H1",
                residue_index=0,
                residue_name="H2",
                residue_identifier="residue.h2",
                chain_index=0,
                chain_identifier="chain.a",
                source_atom_index=0,
                formal_charge=0,
            ),
            MolecularSimulationAtom(
                particle_index=1,
                particle_kind="atom",
                atomic_number=1,
                element_symbol="H",
                atom_name="H2",
                residue_index=0,
                residue_name="H2",
                residue_identifier="residue.h2",
                chain_index=0,
                chain_identifier="chain.a",
                source_atom_index=1,
                formal_charge=0,
            ),
            MolecularSimulationAtom(
                particle_index=2,
                particle_kind="atom",
                atomic_number=8,
                element_symbol="O",
                atom_name="OMM",
                residue_index=1,
                residue_name="MM",
                residue_identifier="residue.mm",
                chain_index=1,
                chain_identifier="chain.m",
                source_atom_index=None,
                formal_charge=None,
            ),
        ),
        bonds=(
            MolecularSimulationBond(
                atom_index_a=0,
                atom_index_b=1,
                order=1,
            ),
        ),
        positions=(
            MolecularVector3(x=0.0, y=0.0, z=-0.037),
            MolecularVector3(x=0.0, y=0.0, z=0.037),
            MolecularVector3(x=0.5, y=0.0, z=0.0),
        ),
        source_solute_atom_count=2,
    )

    environment_reference = _store_model(
        store,
        "fixture.qmmm-real-charge-environment",
        "molecular_environment",
        environment,
    )

    qm_result = adapter.invoke(
        _invocation(
            adapter,
            QM_REGION_PREPARE,
            inputs=(environment_reference,),
            parameters={
                "seed_particle_indices": "0,1",
                "expansion_bond_depth": 0,
                "include_seed_residues": False,
                "maximum_transition_metal_spin": 6,
            },
            execution_identifier="execution.qmmm-real-qm-region",
        )
    )

    assert qm_result.status is ExecutionStatus.SUCCESS

    qm_artifacts = {
        artifact.artifact_type: artifact
        for artifact in qm_result.output_artifacts
    }

    preparation_reference = qm_artifacts[
        "electronic_qm_region_preparation"
    ]
    molecule_reference = qm_artifacts["electronic_molecule"]

    preparation = ElectronicQMRegionPreparation.model_validate_json(
        store.read(preparation_reference)
    )

    assert preparation.boundary_links == ()

    system = _qmmm_system_fixture(
        environment,
        (0.1, -0.1, -0.834),
    )

    system_reference = _store_model(
        store,
        "fixture.qmmm-real-charge-system",
        "molecular_simulation_system",
        system,
    )

    result = adapter.invoke(
        _invocation(
            adapter,
            QMMM_EMBEDDING_PREPARE,
            inputs=(
                preparation_reference,
                molecule_reference,
                environment_reference,
                system_reference,
            ),
            execution_identifier="execution.qmmm-real-foundation",
        )
    )

    assert result.status is ExecutionStatus.SUCCESS

    foundation = QMMMEmbeddingFoundation.model_validate_json(
        store.read(result.output_artifacts[0])
    )

    assert foundation.qm_region_preparation_identifier == (
        preparation.preparation_identifier
    )
    assert foundation.simulation_system_identifier == system.system_identifier
    assert foundation.qm_source_particle_count == 2
    assert foundation.charge_model == "openmm_nonbonded_force"
    assert foundation.boundary_policy == "no_covalent_boundary"
    assert foundation.electrostatic_embedding_ready
    assert len(foundation.embedding_sites) == 1

    site = foundation.embedding_sites[0]

    assert site.source_particle_index == 2
    assert site.charge_source == "openmm_nonbonded_force"
    assert site.charge_e == pytest.approx(-0.834)
    assert site.x_angstrom == pytest.approx(5.0)
    assert foundation.embedding_total_charge_e == pytest.approx(-0.834)


def test_qmmm_foundation_charge_shifts_supported_covalent_boundary() -> None:
    store = MemoryPayloadStore()
    adapter = PySCFElectronicStructureAdapter(store)

    environment = _qm_region_environment()

    environment_reference = _store_model(
        store,
        "fixture.qmmm-boundary-environment",
        "molecular_environment",
        environment,
    )

    qm_result = adapter.invoke(
        _invocation(
            adapter,
            QM_REGION_PREPARE,
            inputs=(environment_reference,),
            parameters={
                "seed_particle_indices": "0",
                "expansion_bond_depth": 0,
                "include_seed_residues": True,
                "maximum_transition_metal_spin": 6,
            },
            execution_identifier="execution.qmmm-boundary-qm-region",
        )
    )

    assert qm_result.status is ExecutionStatus.SUCCESS

    qm_artifacts = {
        artifact.artifact_type: artifact
        for artifact in qm_result.output_artifacts
    }

    preparation_reference = qm_artifacts[
        "electronic_qm_region_preparation"
    ]
    molecule_reference = qm_artifacts["electronic_molecule"]

    preparation = ElectronicQMRegionPreparation.model_validate_json(
        store.read(preparation_reference)
    )

    assert preparation.boundary_links

    system = _qmmm_system_fixture(
        environment,
        (-0.1, 0.1, 0.0, 0.0),
    )

    system_reference = _store_model(
        store,
        "fixture.qmmm-boundary-system",
        "molecular_simulation_system",
        system,
    )

    result = adapter.invoke(
        _invocation(
            adapter,
            QMMM_EMBEDDING_PREPARE,
            inputs=(
                preparation_reference,
                molecule_reference,
                environment_reference,
                system_reference,
            ),
            execution_identifier="execution.qmmm-boundary-foundation",
        )
    )

    assert result.status is ExecutionStatus.SUCCESS
    foundation = QMMMEmbeddingFoundation.model_validate_json(
        store.read(result.output_artifacts[0])
    )
    assert foundation.boundary_policy == "charge_shift"
    assert foundation.embedding_total_charge_e == pytest.approx(0.1)
    assert foundation.unmodified_embedding_total_charge_e == pytest.approx(0.1)
    assert foundation.boundary_charge_adjustments == (
        QMMMBoundaryChargeAdjustment(
            source_particle_index=1,
            original_charge_e=0.1,
            adjusted_charge_e=0.0,
            charge_delta_e=-0.1,
            boundary_mm_particle_indices=(1,),
            role="boundary_charge_removed",
        ),
        QMMMBoundaryChargeAdjustment(
            source_particle_index=3,
            original_charge_e=0.0,
            adjusted_charge_e=0.1,
            charge_delta_e=0.1,
            boundary_mm_particle_indices=(1,),
            role="neighbor_charge_recipient",
        ),
    )
    sites = {
        site.source_particle_index: site for site in foundation.embedding_sites
    }
    assert sites[1].charge_e == pytest.approx(0.0)
    assert sites[3].charge_e == pytest.approx(0.1)
    assert sites[1].charge_source == "openmm_boundary_charge_shift"

def test_real_pyscf_h2_pipeline_produces_generic_artifacts() -> None:
    pytest.importorskip("pyscf")

    store = MemoryPayloadStore()
    adapter = PySCFElectronicStructureAdapter(store)
    molecule_reference = _construct_h2(adapter, store)
    configuration_reference = _configure_h2(adapter, molecule_reference)

    hf_result = adapter.invoke(
        _invocation(
            adapter,
            HARTREE_FOCK,
            inputs=(molecule_reference, configuration_reference),
            execution_identifier="execution.phase4-hf",
        )
    )
    assert hf_result.status is ExecutionStatus.SUCCESS
    hf_reference = hf_result.output_artifacts[0]
    hartree_fock = ElectronicHartreeFockResult.model_validate_json(
        store.read(hf_reference)
    )
    assert hartree_fock.converged
    assert hartree_fock.reference_method == "rhf"
    assert hartree_fock.spatial_orbital_count == 2
    assert hartree_fock.total_energy_hartree < -1.0

    orbital_result = adapter.invoke(
        _invocation(
            adapter,
            ORBITALS_GENERATE,
            inputs=(hf_reference, configuration_reference),
            execution_identifier="execution.phase4-orbitals",
        )
    )
    assert orbital_result.status is ExecutionStatus.SUCCESS
    orbitals = ElectronicOrbitalSet.model_validate_json(
        store.read(orbital_result.output_artifacts[0])
    )
    assert len(orbitals.alpha_orbitals) == 2
    assert len(orbitals.beta_orbitals) == 2

    reference_result = adapter.invoke(
        _invocation(
            adapter,
            REFERENCE_CALCULATE,
            inputs=(molecule_reference, configuration_reference, hf_reference),
            parameters={"method": "mp2"},
            execution_identifier="execution.phase4-mp2",
        )
    )
    assert reference_result.status is ExecutionStatus.SUCCESS
    reference = ElectronicReferenceCalculation.model_validate_json(
        store.read(reference_result.output_artifacts[0])
    )
    assert reference.method == "mp2"
    assert reference.correlation_energy_hartree < 0
    assert reference.total_energy_hartree < hartree_fock.total_energy_hartree

    selection_result = adapter.invoke(
        _invocation(
            adapter,
            ACTIVE_SPACE_SELECT,
            inputs=(
                molecule_reference,
                configuration_reference,
                hf_reference,
            ),
            parameters={
                "active_electron_count": 2,
                "active_spatial_orbital_count": 2,
                "selection_method": "frontier",
            },
            execution_identifier="execution.phase8-active-selection",
        )
    )
    assert selection_result.status is ExecutionStatus.SUCCESS

    selection = ElectronicActiveSpaceSelection.model_validate_json(
        store.read(selection_result.output_artifacts[0])
    )
    assert selection.active_electron_count == 2
    assert selection.active_spatial_orbital_count == 2
    assert selection.active_orbital_indices == (0, 1)
    assert selection.selection_method == "frontier"

    active_result = adapter.invoke(
        _invocation(
            adapter,
            ACTIVE_SPACE_CONSTRUCT,
            inputs=(
                molecule_reference,
                configuration_reference,
                hf_reference,
                selection_result.output_artifacts[0],
            ),
            parameters={},
            execution_identifier="execution.phase4-active",
        )
    )
    assert active_result.status is ExecutionStatus.SUCCESS
    active = ElectronicActiveSpace.model_validate_json(
        store.read(active_result.output_artifacts[0])
    )
    assert active.active_electron_count == 2
    assert active.active_spatial_orbital_count == 2
    assert active.one_body_integrals.shape == (2, 2)
    assert active.two_body_integrals.shape == (2, 2, 2, 2)
    assert "pyscf" not in active.to_canonical_json().lower()

def test_target_ao_projection_selects_without_manual_orbital_indices() -> None:
    pytest.importorskip("pyscf")

    store = MemoryPayloadStore()
    adapter = PySCFElectronicStructureAdapter(store)

    molecule_reference = _construct_h2(adapter, store)
    configuration_reference = _configure_h2(
        adapter,
        molecule_reference,
    )

    hf_result = adapter.invoke(
        _invocation(
            adapter,
            HARTREE_FOCK,
            inputs=(
                molecule_reference,
                configuration_reference,
            ),
            execution_identifier="execution.phase8-target-hf",
        )
    )
    assert hf_result.status is ExecutionStatus.SUCCESS

    result = adapter.invoke(
        _invocation(
            adapter,
            ACTIVE_SPACE_SELECT,
            inputs=(
                molecule_reference,
                configuration_reference,
                hf_result.output_artifacts[0],
            ),
            parameters={
                "active_electron_count": 2,
                "active_spatial_orbital_count": 2,
                "selection_method": "target_ao_projection",
                "target_atom_indices": "0",
            },
            execution_identifier="execution.phase8-target-select",
        )
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert result.failure is None

    selection = ElectronicActiveSpaceSelection.model_validate_json(
        store.read(result.output_artifacts[0])
    )

    assert selection.selection_method == "target_ao_projection"
    assert selection.target_atom_indices == (0,)
    assert selection.active_orbital_indices == (0, 1)
    assert len(selection.orbital_scores) == 2
    assert all(
        score.target_projection_score >= 0
        for score in selection.orbital_scores
    )


def test_automatic_selection_rejects_impossible_electron_count() -> None:
    pytest.importorskip("pyscf")

    store = MemoryPayloadStore()
    adapter = PySCFElectronicStructureAdapter(store)

    molecule_reference = _construct_h2(adapter, store)
    configuration_reference = _configure_h2(
        adapter,
        molecule_reference,
    )

    hf_result = adapter.invoke(
        _invocation(
            adapter,
            HARTREE_FOCK,
            inputs=(
                molecule_reference,
                configuration_reference,
            ),
        )
    )
    assert hf_result.status is ExecutionStatus.SUCCESS

    result = adapter.invoke(
        _invocation(
            adapter,
            ACTIVE_SPACE_SELECT,
            inputs=(
                molecule_reference,
                configuration_reference,
                hf_result.output_artifacts[0],
            ),
            parameters={
                "active_electron_count": 4,
                "active_spatial_orbital_count": 2,
                "selection_method": "frontier",
            },
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.failure is not None
    assert result.failure.code == "electronic_input_invalid"

def _qm_region_environment(
    *,
    metal_boundary: bool = False,
    missing_charge: bool = False,
) -> MolecularEnvironment:
    first_atomic_number = 30 if metal_boundary else 6
    first_symbol = "Zn" if metal_boundary else "C"

    return MolecularEnvironment(
        schema_version=VERSION,
        environment_identifier="environment.qm-region-test",
        environment_type="vacuum",
        source_conformer_set_identifier="conformers.qm-region-test",
        source_conformer_index=0,
        force_field_selection_identifier="force-field.test",
        atoms=(
            MolecularSimulationAtom(
                particle_index=0,
                particle_kind="atom",
                atomic_number=first_atomic_number,
                element_symbol=first_symbol,
                atom_name=first_symbol,
                residue_index=0,
                residue_name="QMA",
                residue_identifier="residue.qma",
                chain_index=0,
                chain_identifier="chain.a",
                source_atom_index=0,
                formal_charge=None if missing_charge else 0,
            ),
            MolecularSimulationAtom(
                particle_index=1,
                particle_kind="atom",
                atomic_number=6 if not metal_boundary else 7,
                element_symbol="C" if not metal_boundary else "N",
                atom_name="C2" if not metal_boundary else "N1",
                residue_index=1,
                residue_name="MMB",
                residue_identifier="residue.mmb",
                chain_index=0,
                chain_identifier="chain.a",
                source_atom_index=1,
                formal_charge=0,
            ),
            MolecularSimulationAtom(
                particle_index=2,
                particle_kind="atom",
                atomic_number=1,
                element_symbol="H",
                atom_name="H1",
                residue_index=0,
                residue_name="QMA",
                residue_identifier="residue.qma",
                chain_index=0,
                chain_identifier="chain.a",
                source_atom_index=2,
                formal_charge=0,
            ),
            MolecularSimulationAtom(
                particle_index=3,
                particle_kind="atom",
                atomic_number=1,
                element_symbol="H",
                atom_name="H2",
                residue_index=1,
                residue_name="MMB",
                residue_identifier="residue.mmb",
                chain_index=0,
                chain_identifier="chain.a",
                source_atom_index=3,
                formal_charge=0,
            ),
        ),
        bonds=(
            MolecularSimulationBond(
                atom_index_a=0,
                atom_index_b=1,
                order=None if metal_boundary else 1,
            ),
            MolecularSimulationBond(
                atom_index_a=0,
                atom_index_b=2,
                order=1,
            ),
            MolecularSimulationBond(
                atom_index_a=1,
                atom_index_b=3,
                order=1,
            ),
        ),
        positions=(
            MolecularVector3(x=0.0, y=0.0, z=0.0),
            MolecularVector3(x=0.154, y=0.0, z=0.0),
            MolecularVector3(x=-0.100, y=0.0, z=0.0),
            MolecularVector3(x=0.254, y=0.0, z=0.0),
        ),
        source_solute_atom_count=4,
    )


def test_qm_region_prepare_caps_boundary_and_determines_charge_spin() -> None:
    store = MemoryPayloadStore()
    adapter = PySCFElectronicStructureAdapter(store)

    environment_reference = _store_model(
        store,
        "fixture.qm-region-environment",
        "molecular_environment",
        _qm_region_environment(),
    )

    result = adapter.invoke(
        _invocation(
            adapter,
            QM_REGION_PREPARE,
            inputs=(environment_reference,),
            parameters={
                "seed_particle_indices": "0",
                "expansion_bond_depth": 0,
                "include_seed_residues": True,
                "maximum_transition_metal_spin": 6,
            },
            execution_identifier="execution.phase8-qm-region",
        )
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert len(result.output_artifacts) == 2

    artifacts_by_type = {
        reference.artifact_type: reference
        for reference in result.output_artifacts
    }

    assert set(artifacts_by_type) == {
        "electronic_qm_region_preparation",
        "electronic_molecule",
    }

    preparation = ElectronicQMRegionPreparation.model_validate_json(
        store.read(
            artifacts_by_type[
                "electronic_qm_region_preparation"
            ]
        )
    )
    molecule = ElectronicMolecule.model_validate_json(
        store.read(
            artifacts_by_type["electronic_molecule"]
        )
    )

    assert preparation.selected_particle_indices == (0, 2)
    assert preparation.selected_source_atom_indices == (0, 2)
    assert preparation.included_residue_identifiers == ("residue.qma",)
    assert len(preparation.boundary_links) == 1

    link = preparation.boundary_links[0]
    assert link.qm_particle_index == 0
    assert link.mm_particle_index == 1
    assert link.link_atom_index == 2
    assert link.link_distance_angstrom == pytest.approx(1.09)
    assert link.x_angstrom == pytest.approx(1.09)

    assessment = preparation.charge_spin_assessment
    assert assessment.molecular_charge == 0
    assert assessment.electron_count == 8
    assert assessment.spin_candidates == (0,)
    assert not assessment.contains_transition_metal

    assert molecule.molecular_charge == 0
    assert molecule.spin == 0
    assert molecule.electron_count == 8
    assert len(molecule.atoms) == 3
    assert molecule.atoms[-1].element_symbol == "H"
    assert molecule.atoms[-1].source_atom_index is None


def test_qm_region_prepare_rejects_transition_metal_boundary_cut() -> None:
    store = MemoryPayloadStore()
    adapter = PySCFElectronicStructureAdapter(store)

    environment_reference = _store_model(
        store,
        "fixture.qm-region-metal-boundary",
        "molecular_environment",
        _qm_region_environment(metal_boundary=True),
    )

    result = adapter.invoke(
        _invocation(
            adapter,
            QM_REGION_PREPARE,
            inputs=(environment_reference,),
            parameters={
                "seed_particle_indices": "0",
                "expansion_bond_depth": 0,
                "include_seed_residues": False,
                "maximum_transition_metal_spin": 6,
            },
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.failure is not None
    assert result.failure.code == "electronic_input_invalid"


def test_qm_region_prepare_fails_without_charge_evidence() -> None:
    store = MemoryPayloadStore()
    adapter = PySCFElectronicStructureAdapter(store)

    environment_reference = _store_model(
        store,
        "fixture.qm-region-missing-charge",
        "molecular_environment",
        _qm_region_environment(missing_charge=True),
    )

    result = adapter.invoke(
        _invocation(
            adapter,
            QM_REGION_PREPARE,
            inputs=(environment_reference,),
            parameters={
                "seed_particle_indices": "0",
                "expansion_bond_depth": 0,
                "include_seed_residues": True,
                "maximum_transition_metal_spin": 6,
            },
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.failure is not None
    assert result.failure.code == "electronic_input_invalid"

def _qmmm_embedding_for_molecule(
    molecule_identifier: str,
    *,
    charge_e: float = -0.834,
) -> QMMMEmbeddingFoundation:
    return QMMMEmbeddingFoundation(
        schema_version=VERSION,
        embedding_identifier=(
            "embedding.native-qmmm-hf"
        ),
        molecule_identifier=molecule_identifier,
        qm_region_preparation_identifier=(
            "qm-region.native-qmmm-hf"
        ),
        environment_identifier=(
            "environment.native-qmmm-hf"
        ),
        simulation_system_identifier=(
            "system.native-qmmm-hf"
        ),
        qm_source_particle_count=1,
        embedding_sites=(
            QMMMEmbeddingSite(
                site_index=0,
                source_particle_index=100,
                element_symbol="O",
                x_angstrom=5.0,
                y_angstrom=0.0,
                z_angstrom=0.0,
                charge_e=charge_e,
                charge_source="openmm_nonbonded_force",
            ),
        ),
        embedding_total_charge_e=charge_e,
        electrostatic_embedding_ready=True,
        boundary_policy="no_covalent_boundary",
        charge_model="openmm_nonbonded_force",
    )


def test_real_pyscf_qmmm_hartree_fock_changes_hamiltonian_and_energy() -> None:
    pytest.importorskip("pyscf")

    store = MemoryPayloadStore()
    adapter = PySCFElectronicStructureAdapter(store)

    molecule_reference = _construct_h2(
        adapter,
        store,
    )
    configuration_reference = _configure_h2(
        adapter,
        molecule_reference,
    )

    molecule = ElectronicMolecule.model_validate_json(
        store.read(molecule_reference)
    )

    embedding = _qmmm_embedding_for_molecule(
        molecule.molecule_identifier
    )
    embedding_reference = _store_model(
        store,
        "fixture.native-qmmm-hf-embedding",
        "qmmm_embedding_foundation",
        embedding,
    )

    bare_invocation = adapter.invoke(
        _invocation(
            adapter,
            HARTREE_FOCK,
            inputs=(
                molecule_reference,
                configuration_reference,
            ),
            execution_identifier=(
                "execution.native-qmmm-hf-bare"
            ),
        )
    )

    assert bare_invocation.status is ExecutionStatus.SUCCESS

    bare = ElectronicHartreeFockResult.model_validate_json(
        store.read(
            bare_invocation.output_artifacts[0]
        )
    )

    embedded_invocation = adapter.invoke(
        _invocation(
            adapter,
            QMMM_HARTREE_FOCK,
            inputs=(
                molecule_reference,
                configuration_reference,
                embedding_reference,
            ),
            execution_identifier=(
                "execution.native-qmmm-hf-embedded"
            ),
        )
    )

    assert (
        embedded_invocation.status
        is ExecutionStatus.SUCCESS
    )

    embedded = ElectronicQMMMHartreeFockResult.model_validate_json(
        store.read(
            embedded_invocation.output_artifacts[0]
        )
    )

    assert embedded.converged
    assert embedded.reference_method == "rhf"
    assert embedded.embedding_identifier == (
        embedding.embedding_identifier
    )
    assert embedded.embedding_site_count == 1
    assert embedded.embedding_total_charge_e == pytest.approx(
        -0.834
    )
    assert embedded.energy_model == (
        "pyscf_point_charge_electrostatic_embedding"
    )

    assert (
        abs(
            embedded.embedded_total_energy_hartree
            - bare.total_energy_hartree
        )
        > 1e-6
    )

    hcore_delta = max(
        abs(embedded_value - bare_value)
        for embedded_value, bare_value in zip(
            embedded.embedded_core_hamiltonian_ao.values,
            bare.core_hamiltonian_ao.values,
            strict=True,
        )
    )

    assert hcore_delta > 1e-8

    assert (
        embedded.embedded_total_energy_hartree
        == pytest.approx(
            embedded.embedded_electronic_energy_hartree
            + embedded.qm_nuclear_repulsion_energy_hartree
            + embedded.qm_mm_nuclear_interaction_energy_hartree,
            abs=1e-9,
        )
    )

    assert not embedded.mm_internal_energy_included
    assert not embedded.mm_mm_electrostatics_included
    assert not embedded.qm_mm_vdw_included


@pytest.mark.parametrize(
    "reference_method",
    ("uhf", "rohf"),
)
def test_real_pyscf_qmmm_hartree_fock_supports_open_shell(
    reference_method: str,
) -> None:
    pytest.importorskip("pyscf")

    store = MemoryPayloadStore()
    adapter = PySCFElectronicStructureAdapter(store)

    molecule = ElectronicMolecule(
        schema_version=VERSION,
        molecule_identifier=(
            f"molecule.native-qmmm-{reference_method}"
        ),
        source_artifact_identifier=(
            f"source.native-qmmm-{reference_method}"
        ),
        source_geometry_identifier=(
            f"geometry.native-qmmm-{reference_method}"
        ),
        atoms=(
            ElectronicAtom(
                atom_index=0,
                atomic_number=1,
                element_symbol="H",
                x_angstrom=0.0,
                y_angstrom=0.0,
                z_angstrom=0.0,
                source_atom_index=0,
            ),
        ),
        molecular_charge=0,
        source_formal_charge=0,
        spin=1,
        electron_count=1,
        alpha_electron_count=1,
        beta_electron_count=0,
    )

    molecule_reference = _store_model(
        store,
        f"fixture.native-qmmm-{reference_method}-molecule",
        "electronic_molecule",
        molecule,
    )

    configuration = ElectronicStructureConfiguration(
        schema_version=VERSION,
        configuration_identifier=(
            f"configuration.native-qmmm-{reference_method}"
        ),
        molecule_identifier=molecule.molecule_identifier,
        basis_set="sto-3g",
        reference_method=reference_method,
        convergence_tolerance=1e-10,
        maximum_iterations=100,
        direct_scf=True,
        density_fitting=False,
        symmetry=False,
        initial_guess="minao",
    )

    configuration_reference = _store_model(
        store,
        f"fixture.native-qmmm-{reference_method}-configuration",
        "electronic_structure_configuration",
        configuration,
    )

    embedding = _qmmm_embedding_for_molecule(
        molecule.molecule_identifier
    )

    embedding_reference = _store_model(
        store,
        f"fixture.native-qmmm-{reference_method}-embedding",
        "qmmm_embedding_foundation",
        embedding,
    )

    result = adapter.invoke(
        _invocation(
            adapter,
            QMMM_HARTREE_FOCK,
            inputs=(
                molecule_reference,
                configuration_reference,
                embedding_reference,
            ),
            execution_identifier=(
                f"execution.native-qmmm-{reference_method}"
            ),
        )
    )

    assert result.status is ExecutionStatus.SUCCESS

    evidence = ElectronicQMMMHartreeFockResult.model_validate_json(
        store.read(result.output_artifacts[0])
    )

    assert evidence.converged
    assert evidence.reference_method == reference_method
    assert evidence.electron_count == 1
    assert evidence.alpha_electron_count == 1
    assert evidence.beta_electron_count == 0
    assert evidence.embedded_total_energy_hartree < 0.0
    assert (
        abs(
            evidence.qm_mm_nuclear_interaction_energy_hartree
        )
        > 1e-8
    )

