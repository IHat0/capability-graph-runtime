"""Product Phase 5.3 OpenMM capability and artifact regressions."""

from __future__ import annotations

import hashlib
import math
import sys

import pytest

from cgr.kernel.contracts import (
    CapabilityVersion,
    ExecutionContext,
    ExecutionStatus,
    HealthStatus,
)
from cgr.molecular.openmm_adapter import (
    DYNAMICS_RUN,
    ENERGY_MINIMIZE,
    ENSEMBLE_SUMMARIZE,
    ENVIRONMENT_PREPARE,
    FORCE_FIELD_SELECT,
    SNAPSHOT_EXTRACT,
    SYSTEM_CONSTRUCT,
    TRAJECTORY_GENERATE,
    OpenMMClassicalSimulationAdapter,
)
from cgr.molecular.rdkit_adapter import (
    CONFORMER_GENERATION,
    PREPARE,
    SMILES_PARSE,
    RDKitCheminformaticsAdapter,
)
from cgr.molecular.simulation import (
    MolecularDynamicsRun,
    MolecularDynamicsSettings,
    MolecularEnsembleSummary,
    MolecularEnvironment,
    MolecularForceFieldSelection,
    MolecularSimulationSystem,
    MolecularSnapshot,
    MolecularSystemConstructionSettings,
    MolecularTrajectory,
)
from cgr.science import (
    ArtifactPointer,
    ArtifactReference,
    CapabilityInvocation,
)

VERSION = CapabilityVersion(major=1, minor=0, patch=0)
EXPERIMENT = ArtifactPointer(
    artifact_identifier="experiment.phase5-3",
    content_sha256="e" * 64,
)


class MemoryPayloadStore:
    """Exact content-addressed public payload store for adapter tests."""

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


class MemoryPrivateStateStore:
    """OpenMM-native state kept separate from public Pulsate artifacts."""

    def __init__(self) -> None:
        self.payloads: dict[tuple[str, str], bytes] = {}

    def read(self, reference: ArtifactReference) -> bytes:
        return self.payloads[(reference.artifact_identifier, reference.content_sha256)]

    def write(self, reference: ArtifactReference, payload: bytes) -> None:
        assert isinstance(payload, bytes)
        assert b"<System" in payload
        self.payloads[(reference.artifact_identifier, reference.content_sha256)] = (
            payload
        )


def _invocation(
    adapter: object,
    capability_name: str,
    *,
    inputs: tuple[ArtifactReference, ...] = (),
    parameters: dict[str, object] | None = None,
    execution_identifier: str = "execution.phase5-3",
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


def _artifact_by_type(result: object, artifact_type: str) -> ArtifactReference:
    return next(
        artifact
        for artifact in result.output_artifacts
        if artifact.artifact_type == artifact_type
    )


def _water_conformer(store: MemoryPayloadStore) -> ArtifactReference:
    rdkit = RDKitCheminformaticsAdapter(store)
    graph_result = rdkit.invoke(
        _invocation(
            rdkit,
            SMILES_PARSE,
            parameters={"smiles": "O"},
            execution_identifier="execution.phase5-3.water-graph",
        )
    )
    assert graph_result.status is ExecutionStatus.SUCCESS
    graph = graph_result.output_artifacts[0]
    prepared_result = rdkit.invoke(
        _invocation(
            rdkit,
            PREPARE,
            inputs=(graph,),
            execution_identifier="execution.phase5-3.water-prepared",
        )
    )
    assert prepared_result.status is ExecutionStatus.SUCCESS
    prepared = prepared_result.output_artifacts[0]
    conformer_result = rdkit.invoke(
        _invocation(
            rdkit,
            CONFORMER_GENERATION,
            inputs=(prepared,),
            parameters={
                "conformer_count": 1,
                "random_seed": 37,
                "maximum_iterations": 200,
            },
            execution_identifier="execution.phase5-3.water-conformer",
        )
    )
    assert conformer_result.status is ExecutionStatus.SUCCESS
    return conformer_result.output_artifacts[0]


def _force_field(
    adapter: OpenMMClassicalSimulationAdapter,
) -> ArtifactReference:
    result = adapter.invoke(
        _invocation(
            adapter,
            FORCE_FIELD_SELECT,
            parameters={
                "force_field_files": "amber14/tip3p.xml",
                "water_model": "tip3p",
            },
            execution_identifier="execution.phase5-3.force-field",
        )
    )
    assert result.status is ExecutionStatus.SUCCESS
    return result.output_artifacts[0]


def _vacuum_environment(
    adapter: OpenMMClassicalSimulationAdapter,
    conformer: ArtifactReference,
    force_field: ArtifactReference,
) -> ArtifactReference:
    result = adapter.invoke(
        _invocation(
            adapter,
            ENVIRONMENT_PREPARE,
            inputs=(conformer, force_field),
            parameters={
                "environment_type": "vacuum",
                "conformer_index": 0,
                "solute_residue_name": "HOH",
                "solute_chain_identifier": "A",
                "solute_atom_names": "O;H1;H2",
            },
            execution_identifier="execution.phase5-3.environment",
        )
    )
    assert result.status is ExecutionStatus.SUCCESS
    return result.output_artifacts[0]


def _system(
    adapter: OpenMMClassicalSimulationAdapter,
    environment: ArtifactReference,
    force_field: ArtifactReference,
) -> ArtifactReference:
    result = adapter.invoke(
        _invocation(
            adapter,
            SYSTEM_CONSTRUCT,
            inputs=(environment, force_field),
            parameters={
                "nonbonded_method": "no_cutoff",
                "constraints": "hydrogen_bonds",
                "rigid_water": True,
                "remove_center_of_mass_motion": True,
            },
            execution_identifier="execution.phase5-3.system",
        )
    )
    assert result.status is ExecutionStatus.SUCCESS
    return result.output_artifacts[0]


def _minimized_snapshot(
    adapter: OpenMMClassicalSimulationAdapter,
    environment: ArtifactReference,
    system: ArtifactReference,
) -> ArtifactReference:
    result = adapter.invoke(
        _invocation(
            adapter,
            ENERGY_MINIMIZE,
            inputs=(environment, system),
            parameters={
                "platform": "Reference",
                "integration_timestep_fs": 1.0,
                "tolerance_kj_per_mol_nm": 10.0,
                "maximum_iterations": 100,
            },
            execution_identifier="execution.phase5-3.minimize",
        )
    )
    assert result.status is ExecutionStatus.SUCCESS
    return _artifact_by_type(result, "molecular_snapshot")


def _nvt_parameters(*, steps: int) -> dict[str, object]:
    return {
        "ensemble": "nvt",
        "integrator": "langevin_middle",
        "timestep_fs": 1.0,
        "steps": steps,
        "initial_velocity_source": "maxwell_boltzmann",
        "initial_temperature_kelvin": 300.0,
        "target_temperature_kelvin": 300.0,
        "friction_per_ps": 1.0,
        "random_seed": 41,
        "platform": "Reference",
    }


def test_declaration_exposes_all_phase5_3_capabilities_without_importing_openmm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "openmm", None)
    adapter = OpenMMClassicalSimulationAdapter(
        MemoryPayloadStore(),
        MemoryPrivateStateStore(),
    )

    assert tuple(
        envelope.descriptor.capability_name
        for envelope in adapter.declaration.capabilities
    ) == (
        DYNAMICS_RUN,
        ENERGY_MINIMIZE,
        ENSEMBLE_SUMMARIZE,
        ENVIRONMENT_PREPARE,
        FORCE_FIELD_SELECT,
        SNAPSHOT_EXTRACT,
        SYSTEM_CONSTRUCT,
        TRAJECTORY_GENERATE,
    )
    assert adapter.declaration.engine.engine_identifier == "engine.openmm"
    assert adapter.health().status is HealthStatus.UNAVAILABLE


def test_system_settings_reject_hidden_or_inconsistent_nonbonded_controls() -> None:
    with pytest.raises(ValueError, match="requires an explicit cutoff"):
        MolecularSystemConstructionSettings(
            nonbonded_method="pme",
            nonbonded_cutoff_nm=None,
            switch_distance_nm=None,
            constraints="hydrogen_bonds",
            rigid_water=True,
            remove_center_of_mass_motion=True,
            hydrogen_mass_amu=None,
            ewald_error_tolerance=0.0005,
        )

    with pytest.raises(ValueError, match="unused Ewald error tolerance"):
        MolecularSystemConstructionSettings(
            nonbonded_method="no_cutoff",
            nonbonded_cutoff_nm=None,
            switch_distance_nm=None,
            constraints="hydrogen_bonds",
            rigid_water=True,
            remove_center_of_mass_motion=True,
            hydrogen_mass_amu=None,
            ewald_error_tolerance=0.0005,
        )


def test_dynamics_settings_reject_unused_velocity_initialization_controls() -> None:
    with pytest.raises(ValueError, match="unused initialization temperature"):
        MolecularDynamicsSettings(
            ensemble="nve",
            integrator="verlet",
            timestep_fs=1.0,
            steps=10,
            initial_velocity_source="input_snapshot",
            initial_temperature_kelvin=300.0,
            random_seed=None,
            platform="Reference",
        )

    with pytest.raises(ValueError, match="requires temperature and random seed"):
        MolecularDynamicsSettings(
            ensemble="nvt",
            integrator="langevin_middle",
            timestep_fs=1.0,
            steps=10,
            initial_velocity_source="maxwell_boltzmann",
            initial_temperature_kelvin=300.0,
            target_temperature_kelvin=300.0,
            friction_per_ps=1.0,
            random_seed=None,
            platform="Reference",
        )


def test_force_field_selection_is_engine_neutral_and_versioned() -> None:
    pytest.importorskip("openmm")
    store = MemoryPayloadStore()
    adapter = OpenMMClassicalSimulationAdapter(store, MemoryPrivateStateStore())

    artifact = _force_field(adapter)
    selection = MolecularForceFieldSelection.model_validate_json(store.read(artifact))

    assert artifact.artifact_type == "molecular_force_field_selection"
    assert selection.force_field_files == ("amber14/tip3p.xml",)
    assert selection.water_model == "tip3p"
    assert selection.engine_distribution_version == "8.5.2"
    assert b"<ForceField" not in store.read(artifact)


def test_solvent_and_ion_environment_is_periodic_and_explicit() -> None:
    pytest.importorskip("openmm")
    pytest.importorskip("rdkit")
    store = MemoryPayloadStore()
    adapter = OpenMMClassicalSimulationAdapter(store, MemoryPrivateStateStore())
    conformer = _water_conformer(store)
    force_field = _force_field(adapter)

    result = adapter.invoke(
        _invocation(
            adapter,
            ENVIRONMENT_PREPARE,
            inputs=(conformer, force_field),
            parameters={
                "environment_type": "solvent",
                "conformer_index": 0,
                "solute_residue_name": "HOH",
                "solute_chain_identifier": "A",
                "solute_atom_names": "O;H1;H2",
                "water_model": "tip3p",
                "ionic_strength_molar": 0.0,
                "positive_ion": "Na+",
                "negative_ion": "Cl-",
                "neutralize": False,
                "padding_nm": 0.4,
                "box_shape": "cube",
            },
            execution_identifier="execution.phase5-3.solvent",
        )
    )

    assert result.status is ExecutionStatus.SUCCESS
    environment = MolecularEnvironment.model_validate_json(
        store.read(result.output_artifacts[0])
    )
    assert environment.environment_type == "solvent"
    assert environment.periodic_box is not None
    assert environment.water_model == "tip3p"
    assert environment.ionic_strength_molar == 0.0
    assert environment.positive_ion == "Na+"
    assert environment.negative_ion == "Cl-"
    assert environment.neutralized is False
    assert len(environment.atoms) >= 3


def test_environment_and_system_keep_native_openmm_state_private() -> None:
    pytest.importorskip("openmm")
    pytest.importorskip("rdkit")
    store = MemoryPayloadStore()
    private = MemoryPrivateStateStore()
    adapter = OpenMMClassicalSimulationAdapter(store, private)
    conformer = _water_conformer(store)
    force_field = _force_field(adapter)
    environment_reference = _vacuum_environment(adapter, conformer, force_field)
    system_reference = _system(adapter, environment_reference, force_field)

    environment = MolecularEnvironment.model_validate_json(
        store.read(environment_reference)
    )
    system = MolecularSimulationSystem.model_validate_json(store.read(system_reference))
    private_payload = private.read(system_reference)

    assert environment.environment_type == "vacuum"
    assert len(environment.atoms) == 3
    assert environment.periodic_box is None
    assert [atom.residue_name for atom in environment.atoms] == ["HOH"] * 3
    assert system.particle_count == 3
    assert system.massive_particle_count == 3
    assert system.degrees_of_freedom == 3
    assert system.periodic is False
    assert hashlib.sha256(private_payload).hexdigest() == system.private_state_sha256
    assert b"<System" in private_payload
    assert b"<System" not in store.read(system_reference)
    assert b"openmm_system_xml" in store.read(system_reference)


def test_minimization_dynamics_trajectory_snapshot_and_ensemble_pipeline() -> None:
    pytest.importorskip("openmm")
    pytest.importorskip("rdkit")
    store = MemoryPayloadStore()
    private = MemoryPrivateStateStore()
    adapter = OpenMMClassicalSimulationAdapter(store, private)
    conformer = _water_conformer(store)
    force_field = _force_field(adapter)
    environment = _vacuum_environment(adapter, conformer, force_field)
    system = _system(adapter, environment, force_field)
    minimized = _minimized_snapshot(adapter, environment, system)

    minimized_snapshot = MolecularSnapshot.model_validate_json(store.read(minimized))
    assert minimized_snapshot.particle_count == 3
    assert math.isfinite(minimized_snapshot.potential_energy_kj_per_mol)

    dynamics_result = adapter.invoke(
        _invocation(
            adapter,
            DYNAMICS_RUN,
            inputs=(system, minimized),
            parameters=_nvt_parameters(steps=4),
            execution_identifier="execution.phase5-3.dynamics",
        )
    )
    assert dynamics_result.status is ExecutionStatus.SUCCESS
    run_reference = _artifact_by_type(dynamics_result, "molecular_dynamics_run")
    final_reference = _artifact_by_type(dynamics_result, "molecular_snapshot")
    run = MolecularDynamicsRun.model_validate_json(store.read(run_reference))
    final_snapshot = MolecularSnapshot.model_validate_json(store.read(final_reference))
    assert run.completed_steps == 4
    assert run.simulated_time_ps == pytest.approx(0.004)
    assert final_snapshot.step == 4
    assert final_snapshot.particle_count == 3

    trajectory_result = adapter.invoke(
        _invocation(
            adapter,
            TRAJECTORY_GENERATE,
            inputs=(system, minimized),
            parameters={
                **_nvt_parameters(steps=4),
                "report_interval_steps": 2,
            },
            execution_identifier="execution.phase5-3.trajectory",
        )
    )
    assert trajectory_result.status is ExecutionStatus.SUCCESS
    trajectory_reference = trajectory_result.output_artifacts[0]
    trajectory = MolecularTrajectory.model_validate_json(
        store.read(trajectory_reference)
    )
    assert [snapshot.step for snapshot in trajectory.snapshots] == [0, 2, 4]
    assert all(snapshot.particle_count == 3 for snapshot in trajectory.snapshots)

    extraction_result = adapter.invoke(
        _invocation(
            adapter,
            SNAPSHOT_EXTRACT,
            inputs=(trajectory_reference,),
            parameters={"frame_index": 1},
            execution_identifier="execution.phase5-3.snapshot-extract",
        )
    )
    extracted = MolecularSnapshot.model_validate_json(
        store.read(extraction_result.output_artifacts[0])
    )
    assert extraction_result.status is ExecutionStatus.SUCCESS
    assert extracted.step == 2
    assert extracted.frame_index == 0
    assert extracted.source_trajectory_identifier == trajectory.trajectory_identifier

    summary_result = adapter.invoke(
        _invocation(
            adapter,
            ENSEMBLE_SUMMARIZE,
            inputs=(trajectory_reference,),
            execution_identifier="execution.phase5-3.ensemble",
        )
    )
    summary = MolecularEnsembleSummary.model_validate_json(
        store.read(summary_result.output_artifacts[0])
    )
    assert summary_result.status is ExecutionStatus.SUCCESS
    assert summary.frame_count == 3
    assert summary.first_step == 0
    assert summary.last_step == 4
    assert math.isfinite(summary.mean_potential_energy_kj_per_mol)


def test_missing_required_simulation_parameters_fail_closed() -> None:
    pytest.importorskip("openmm")
    adapter = OpenMMClassicalSimulationAdapter(
        MemoryPayloadStore(),
        MemoryPrivateStateStore(),
    )

    result = adapter.invoke(
        _invocation(
            adapter,
            FORCE_FIELD_SELECT,
            parameters={"water_model": "tip3p"},
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.failure is not None
    assert result.failure.code == "simulation_input_invalid"
    assert "force_field_files" not in result.failure.message


def test_missing_private_system_state_fails_closed() -> None:
    pytest.importorskip("openmm")
    pytest.importorskip("rdkit")
    store = MemoryPayloadStore()
    private = MemoryPrivateStateStore()
    adapter = OpenMMClassicalSimulationAdapter(store, private)
    conformer = _water_conformer(store)
    force_field = _force_field(adapter)
    environment = _vacuum_environment(adapter, conformer, force_field)
    system = _system(adapter, environment, force_field)

    isolated = OpenMMClassicalSimulationAdapter(store, MemoryPrivateStateStore())
    result = isolated.invoke(
        _invocation(
            isolated,
            ENERGY_MINIMIZE,
            inputs=(environment, system),
            parameters={
                "platform": "Reference",
                "integration_timestep_fs": 1.0,
                "tolerance_kj_per_mol_nm": 10.0,
                "maximum_iterations": 10,
            },
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.failure is not None
    assert result.failure.code == "simulation_input_invalid"
