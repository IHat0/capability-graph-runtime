"""Phase 4 PySCF capability and electronic-artifact regressions."""

from __future__ import annotations

import hashlib
import sys

import pytest
from pydantic import ValidationError

from cgr.electronic_structure import (
    ACTIVE_SPACE_CONSTRUCT,
    CONFIGURATION_DEFINE,
    HARTREE_FOCK,
    MOLECULE_CONSTRUCT,
    ORBITALS_GENERATE,
    QMMM_EMBEDDING_PREPARE,
    REFERENCE_CALCULATE,
    ElectronicActiveSpace,
    ElectronicHartreeFockResult,
    ElectronicMolecule,
    ElectronicOrbitalSet,
    ElectronicReferenceCalculation,
    ElectronicStructureConfiguration,
    ElectronicTensor,
    PySCFElectronicStructureAdapter,
    QMMMEmbeddingFoundation,
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

    assert len(envelopes) == 7
    assert {envelope.descriptor.capability_name for envelope in envelopes} == {
        MOLECULE_CONSTRUCT,
        CONFIGURATION_DEFINE,
        HARTREE_FOCK,
        ORBITALS_GENERATE,
        REFERENCE_CALCULATE,
        ACTIVE_SPACE_CONSTRUCT,
        QMMM_EMBEDDING_PREPARE,
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


def test_qmmm_foundation_records_unassigned_environment_charges() -> None:
    store = MemoryPayloadStore()
    adapter = PySCFElectronicStructureAdapter(store)
    molecule_reference = _construct_h2(adapter, store)
    environment = MolecularEnvironment(
        schema_version=VERSION,
        environment_identifier="environment.h2-solvent",
        environment_type="solvent",
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
                atom_name="O",
                residue_index=1,
                residue_name="HOH",
                residue_identifier="residue.water",
                chain_index=1,
                chain_identifier="chain.w",
                formal_charge=None,
            ),
        ),
        bonds=(MolecularSimulationBond(atom_index_a=0, atom_index_b=1, order=1),),
        positions=(
            MolecularVector3(x=0.0, y=0.0, z=-0.037),
            MolecularVector3(x=0.0, y=0.0, z=0.037),
            MolecularVector3(x=0.5, y=0.5, z=0.5),
        ),
        source_solute_atom_count=2,
        periodic_box=MolecularPeriodicBox(
            vector_a=MolecularVector3(x=2.0, y=0.0, z=0.0),
            vector_b=MolecularVector3(x=0.0, y=2.0, z=0.0),
            vector_c=MolecularVector3(x=0.0, y=0.0, z=2.0),
        ),
        water_model="tip3p",
        ionic_strength_molar=0.0,
        positive_ion="Na+",
        negative_ion="Cl-",
        neutralized=True,
        solvent_padding_nm=1.0,
        solvent_box_shape="cube",
    )
    environment_reference = _store_model(
        store,
        "fixture.h2-environment",
        "molecular_environment",
        environment,
    )
    result = adapter.invoke(
        _invocation(
            adapter,
            QMMM_EMBEDDING_PREPARE,
            inputs=(molecule_reference, environment_reference),
            execution_identifier="execution.phase4-qmmm",
        )
    )
    assert result.status is ExecutionStatus.SUCCESS
    foundation = QMMMEmbeddingFoundation.model_validate_json(
        store.read(result.output_artifacts[0])
    )
    assert len(foundation.embedding_sites) == 1
    assert foundation.assigned_charge_count == 0
    assert foundation.unassigned_charge_count == 1
    assert not foundation.electrostatic_embedding_ready


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

    active_result = adapter.invoke(
        _invocation(
            adapter,
            ACTIVE_SPACE_CONSTRUCT,
            inputs=(molecule_reference, configuration_reference, hf_reference),
            parameters={
                "active_electron_count": 2,
                "active_orbital_indices": "0,1",
            },
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
