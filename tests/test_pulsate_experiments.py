from __future__ import annotations

import json
import importlib
import math
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cgr.pulsate_api.app import _load_preset, create_app
from cgr.pulsate_api.experiments import (
    ActiveSpacePolicy,
    ExperimentStore,
    PlannerInputError,
    compile_manifest,
    plan_scientific_question,
)
from cgr.pulsate_api.runs import RunCoordinator, assert_public_response_safe
from test_pulsate_runs import ControlledExecutor


@pytest.mark.parametrize(
    ("question", "atoms", "charge", "distance", "unit", "active_electrons", "indices"),
    [
        ("Compute the ground-state energy of H2 at 0.735 angstrom", ("H", "H"), 0, 0.735, "angstrom", 2, (0, 1)),
        ("Compute the ground-state energy of LiH at bond length 1.8 angstrom singlet", ("Li", "H"), 0, 1.8, "angstrom", 2, (1, 2)),
        ("Compute the ground-state energy of HeH+ at 2.1 bohr", ("He", "H"), 1, 2.1, "bohr", 2, (0, 1)),
        ("Compute the ground-state energy of HeH+ at 1.1 angstrom with charge +1 and singlet multiplicity", ("He", "H"), 1, 1.1, "angstrom", 2, (0, 1)),
        ("Compute ground-state energy for HLi at bond distance 1.2 angstrom charge 0 singlet", ("H", "Li"), 0, 1.2, "angstrom", 2, (1, 2)),
    ],
)
def test_planner_builds_molecule_neutral_validated_specifications(
    question: str,
    atoms: tuple[str, str],
    charge: int,
    distance: float,
    unit: str,
    active_electrons: int,
    indices: tuple[int, int],
) -> None:
    planned = plan_scientific_question(question)

    assert planned.ready_for_execution is True
    assert planned.specification is not None
    specification = planned.specification
    assert specification.atoms == atoms
    assert specification.molecular_charge == charge
    assert specification.coordinate_units == unit
    assert math.isclose(specification.coordinates[1][2], distance)
    assert specification.active_space_policy.active_electron_count == active_electrons
    assert specification.active_space_policy.active_orbital_indices == indices


def test_unitless_distance_is_an_explicit_assumption() -> None:
    planned = plan_scientific_question("Compute the ground-state energy of H2 at 0.74")

    assert planned.ready_for_execution is True
    assert "coordinate_units=angstrom (system default)" in planned.assumptions


@pytest.mark.parametrize(
    ("alias", "atoms", "charge"),
    [
        ("hydrogen molecule", ("H", "H"), 0),
        ("molecular hydrogen", ("H", "H"), 0),
        ("lithium hydride", ("Li", "H"), 0),
        ("helium hydride cation", ("He", "H"), 1),
        ("helium hydride ion", ("He", "H"), 1),
    ],
)
def test_supported_molecule_aliases_are_normalized(
    alias: str, atoms: tuple[str, str], charge: int
) -> None:
    planned = plan_scientific_question(
        f"Calculate the ground-state energy of {alias} at 1.8 angstrom."
    )
    assert planned.ready_for_execution is True
    assert planned.specification is not None
    assert planned.specification.atoms == atoms
    assert planned.specification.molecular_charge == charge


@pytest.mark.parametrize("symbol", ["Å", "å"])
def test_bare_angstrom_symbol_is_supported(symbol: str) -> None:
    planned = plan_scientific_question(
        f"Calculate the ground-state energy of LiH at 1.8 {symbol}."
    )
    assert planned.ready_for_execution is True
    assert planned.specification is not None
    assert planned.specification.coordinate_units == "angstrom"
    assert planned.specification.coordinates[1][2] == pytest.approx(1.8)


def test_frontend_placeholder_produces_a_ready_lih_plan() -> None:
    component = (
        Path(__file__).parents[1] / "frontend" / "src" / "components" / "EmptyWorkspace.tsx"
    ).read_text(encoding="utf-8")
    match = re.search(r'placeholder="([^"]+)"', component)
    assert match is not None

    planned = plan_scientific_question(match.group(1))
    assert planned.ready_for_execution is True
    assert planned.specification is not None
    assert planned.specification.atoms == ("Li", "H")


@pytest.mark.parametrize(
    "target_phrase",
    ["on IBM Quantum", "on IBM hardware", "on quantum hardware", "execute on IBM"],
)
def test_explicit_ibm_intent_never_falls_back_to_local(target_phrase: str) -> None:
    planned = plan_scientific_question(
        f"Calculate the ground-state energy of H2 at 0.9 angstrom {target_phrase}."
    )
    assert planned.ready_for_execution is False
    assert planned.requested_execution_target == "ibm_quantum"
    assert "ibm_quantum_execution_unavailable" in planned.missing_fields
    assert not any("execution_target=local_simulator" in item for item in planned.assumptions)


def test_plan_api_preserves_ibm_target_and_local_run_endpoint_rejects_it(
    tmp_path: Path,
) -> None:
    coordinator = RunCoordinator(
        run_root=tmp_path / "runs",
        manifest_resolver=_load_preset,
        executor=ControlledExecutor(),
        enabled=True,
    )
    with TestClient(create_app(coordinator=coordinator)) as client:
        response = client.post(
            "/api/v1/experiments/plan",
            json={
                "question": "Calculate the ground-state energy of H2 at 0.9 angstrom on IBM Quantum."
            },
        )
        blocked_run = client.post(
            "/api/v1/runs",
            json={
                "experiment_identifier": "experiment-" + "e" * 32,
                "execution_target": "ibm_quantum",
            },
        )

    assert response.status_code == 201
    plan = response.json()
    assert plan["ready_for_execution"] is False
    assert plan["requested_execution_target"] == "ibm_quantum"
    assert "ibm_quantum_execution_unavailable" in plan["missing_fields"]
    assert blocked_run.status_code == 422


def test_explicit_unsupported_basis_is_not_silently_replaced() -> None:
    planned = plan_scientific_question(
        "Calculate the ground-state energy of H2 at 0.9 angstrom using a 6-31G basis."
    )
    assert planned.ready_for_execution is False
    assert planned.specification is None
    assert "unsupported_basis_set" in planned.missing_fields


@pytest.mark.parametrize(
    ("setting_phrase", "missing_code"),
    [
        ("using UHF", "unsupported_reference_method"),
        ("with parity mapper", "unsupported_mapper"),
        ("with hardware-efficient ansatz", "unsupported_ansatz"),
        ("with COBYLA optimizer", "unsupported_optimizer"),
    ],
)
def test_other_explicit_unsupported_settings_are_not_silently_replaced(
    setting_phrase: str, missing_code: str
) -> None:
    planned = plan_scientific_question(
        f"Calculate the ground-state energy of H2 at 0.9 angstrom {setting_phrase}."
    )
    assert planned.ready_for_execution is False
    assert planned.specification is None
    assert missing_code in planned.missing_fields


def test_excessive_negative_charge_fails_basis_capacity_before_execution() -> None:
    with pytest.raises(PlannerInputError, match="STO-3G electron capacity"):
        plan_scientific_question(
            "Calculate the ground-state energy of H2 at 0.9 angstrom with charge -3."
        )


def test_excessive_active_electron_count_fails_before_execution() -> None:
    with pytest.raises(PlannerInputError, match="active-space capacity"):
        plan_scientific_question(
            "Calculate the ground-state energy of LiH at 1.8 angstrom with charge -3."
        )


def test_active_space_contract_enforces_electron_capacity() -> None:
    with pytest.raises(ValueError, match="active-space capacity"):
        ActiveSpacePolicy(
            active_electron_count=5,
            active_spatial_orbital_count=2,
            active_orbital_indices=(0, 1),
        )


@pytest.mark.parametrize(
    "question",
    [
        "Compute the ground-state energy of XeH at 1.0 angstrom",
        "Compute the ground-state energy of H2 at nope angstrom",
        "Compute the ground-state energy of H3 at 1.0 angstrom",
        "Compute the ground-state energy of HeH+ at 1.0 angstrom charge 0",
    ],
)
def test_invalid_scientific_declarations_fail_closed(question: str) -> None:
    with pytest.raises(PlannerInputError):
        plan_scientific_question(question)


def test_missing_fields_and_unsupported_spin_are_not_executable() -> None:
    incomplete = plan_scientific_question("Compute the ground-state energy of H2")
    triplet = plan_scientific_question(
        "Compute the ground-state energy of H2 at 0.74 angstrom triplet"
    )

    assert incomplete.specification is None
    assert incomplete.missing_fields == ("bond_length",)
    assert triplet.specification is None
    assert "closed_shell_singlet_for_v1_execution" in triplet.missing_fields


def test_impossible_charge_and_multiplicity_is_rejected() -> None:
    with pytest.raises(PlannerInputError, match="physically incompatible"):
        plan_scientific_question(
            "Compute the ground-state energy of H2 at 0.9 angstrom doublet"
        )


def test_h2_dynamic_geometry_differs_from_the_regression_preset() -> None:
    specification = plan_scientific_question(
        "Compute the ground-state energy of H2 at 0.9 angstrom"
    ).specification
    assert specification is not None
    assert specification.coordinates[1] == (0.0, 0.0, 0.9)
    assert specification.coordinates[1][2] != pytest.approx(
        _load_preset("h2-ground-state-v1").experiment.molecular_system.declared_bond_distance
    )


def test_specification_fingerprint_is_deterministic_and_geometry_sensitive() -> None:
    first = plan_scientific_question(
        "Compute the ground-state energy of H2 at 0.9 angstrom"
    ).specification
    second = plan_scientific_question(
        "Compute the ground-state energy of H2 at 0.9 angstrom"
    ).specification
    changed = plan_scientific_question(
        "Compute the ground-state energy of H2 at 1.0 angstrom"
    ).specification
    assert first is not None and second is not None and changed is not None
    assert first.fingerprint == second.fingerprint
    assert first.fingerprint != changed.fingerprint


def test_manifest_compilation_is_deterministic_and_does_not_use_a_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    specification = plan_scientific_question(
        "Compute the ground-state energy of LiH at 1.8 angstrom"
    ).specification
    assert specification is not None
    identifier = "experiment-" + "a" * 32

    monkeypatch.setattr(
        importlib.import_module("cgr.pulsate_api.app"),
        "_load_preset",
        lambda _identifier: (_ for _ in ()).throw(AssertionError("preset read")),
    )
    first = compile_manifest(specification, experiment_identifier=identifier)
    second = compile_manifest(specification, experiment_identifier=identifier)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.experiment.experiment_identifier == identifier
    assert first.expected_experiment_sha256 == first.experiment.fingerprint
    assert [atom.element for atom in first.experiment.molecular_system.atoms] == ["Li", "H"]


def test_store_persists_and_revalidates_immutable_scientific_identity(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path / "experiments")
    store.start()
    state = store.plan("Compute the ground-state energy of LiH at 1.8 angstrom")

    identifier = state["experiment_identifier"]
    directory = store.root / identifier
    assert sorted(path.name for path in directory.iterdir()) == [
        "request.json", "specification.json", "state.json"
    ]
    assert store.get(identifier) == state
    manifest, molecule = store.resolve_for_run(identifier)
    assert manifest.experiment.experiment_identifier == identifier
    assert molecule == state["molecule"]
    store.close()
    recovered = ExperimentStore(tmp_path / "experiments")
    recovered.start()
    assert recovered.get(identifier) == state

    state_path = directory / "state.json"
    tampered = json.loads(state_path.read_text(encoding="utf-8"))
    tampered["specification"]["coordinates"][1][2] = 9.0
    state_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="identity mismatch"):
        recovered.get(identifier)


def test_plan_get_and_dynamic_run_preserve_compiled_manifest_and_molecule(tmp_path: Path) -> None:
    executor = ControlledExecutor()
    coordinator = RunCoordinator(
        run_root=tmp_path / "runs",
        manifest_resolver=_load_preset,
        executor=executor,
        enabled=True,
    )
    store = ExperimentStore(tmp_path / "experiments")
    with TestClient(create_app(coordinator=coordinator, experiment_store=store)) as client:
        planned = client.post(
            "/api/v1/experiments/plan",
            json={"question": "Compute the ground-state energy of LiH at 1.8 angstrom"},
        )
        assert planned.status_code == 201
        plan = planned.json()
        identifier = plan["experiment_identifier"]
        assert plan["ready_for_execution"] is True
        assert client.get(f"/api/v1/experiments/{identifier}").json() == plan

        created = client.post(
            "/api/v1/runs",
            headers={"Idempotency-Key": "dynamic-experiment-run-0001"},
            json={
                "experiment_identifier": identifier,
                "execution_target": "local_simulator",
            },
        )
        assert created.status_code == 202
        state = created.json()
        assert state["experiment_identifier"] == identifier
        assert state["source_type"] == "dynamic_experiment"
        assert state["source_identifier"] == identifier
        assert state["preset_identifier"] is None
        assert state["molecule"] == plan["molecule"]
        assert_public_response_safe(plan)
        assert_public_response_safe(state)

        run_directory = coordinator.run_root / state["run_identifier"]
        request = json.loads((run_directory / "request.json").read_text(encoding="utf-8"))
        compiled = json.loads((run_directory / "compiled-manifest.json").read_text(encoding="utf-8"))
        assert request["experiment_identifier"] == identifier
        assert "preset_identifier" not in request
        assert compiled["experiment"]["experiment_identifier"] == identifier
        assert compiled["expected_experiment_sha256"] == plan["expected_experiment_sha256"]

        repeated = client.post(
            "/api/v1/runs",
            headers={"Idempotency-Key": "dynamic-experiment-run-0001"},
            json={
                "experiment_identifier": identifier,
                "execution_target": "local_simulator",
            },
        )
        assert repeated.status_code == 202
        assert repeated.json()["run_identifier"] == state["run_identifier"]
        conflict = client.post(
            "/api/v1/runs",
            headers={"Idempotency-Key": "dynamic-experiment-run-0001"},
            json={
                "preset_identifier": "h2-ground-state-v1",
                "execution_target": "local_simulator",
            },
        )
        assert conflict.status_code == 409

        for _ in range(100):
            terminal = client.get(f"/api/v1/runs/{state['run_identifier']}").json()
            if terminal["status"] in {"authorized", "rejected", "failed", "interrupted"}:
                break
        assert terminal["status"] == "authorized"
        assert terminal["molecule"]["specification_sha256"] == plan["specification_sha256"]
        for endpoint in ("results", "verification", "receipt"):
            evidence = client.get(
                f"/api/v1/runs/{state['run_identifier']}/{endpoint}"
            ).json()
            assert evidence["source_type"] == "dynamic_experiment"
            assert evidence["source_identifier"] == identifier
            assert evidence["preset_identifier"] is None


def test_run_request_requires_exactly_one_source(tmp_path: Path) -> None:
    coordinator = RunCoordinator(
        run_root=tmp_path / "runs",
        manifest_resolver=_load_preset,
        executor=ControlledExecutor(),
        enabled=True,
    )
    with TestClient(create_app(coordinator=coordinator)) as client:
        neither = client.post("/api/v1/runs", json={"execution_target": "local_simulator"})
        both = client.post(
            "/api/v1/runs",
            json={
                "preset_identifier": "h2-ground-state-v1",
                "experiment_identifier": "experiment-" + "b" * 32,
                "execution_target": "local_simulator",
            },
        )

    assert neither.status_code == 422
    assert both.status_code == 422


def test_caller_controlled_manifest_path_is_rejected(tmp_path: Path) -> None:
    coordinator = RunCoordinator(
        run_root=tmp_path / "runs",
        manifest_resolver=_load_preset,
        executor=ControlledExecutor(),
        enabled=True,
    )
    with TestClient(create_app(coordinator=coordinator)) as client:
        response = client.post(
            "/api/v1/runs",
            json={
                "experiment_identifier": "experiment-" + "d" * 32,
                "execution_target": "local_simulator",
                "manifest_path": "C:/caller/manifest.json",
            },
        )
    assert response.status_code == 422


def test_experiment_store_rejects_symlinked_record_directories(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path / "experiments")
    store.start()
    identifier = "experiment-" + "c" * 32
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (store.root / identifier).symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symbolic-link creation is unavailable on this platform.")

    with pytest.raises(Exception, match="not found"):
        store.get(identifier)
