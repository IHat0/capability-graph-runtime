"""Approved natural-language experiment execution bridge regressions."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_pulsate_ibm import AuthorizedLocalExecutor, FakeIBMAdapter
from test_pulsate_natural_language import (
    H2_QUESTION,
    LIH_QUESTION,
    ControlledProvider,
    _complete_draft,
    _real_qwen_h2_identity_omitted_draft,
)
from test_pulsate_runs import wait_for_terminal

from cgr.pulsate_api.app import _load_preset, create_app
from cgr.pulsate_api.experiments import ExperimentStore
from cgr.pulsate_api.ibm import IBMQuantumConfiguration, IBMQuantumRunExecutor
from cgr.pulsate_api.natural_language import NaturalLanguageInterpretationStore
from cgr.pulsate_api.runs import RunCoordinator
from cgr.quantum_preflight.artifacts import write_json_atomic
from cgr.science import sha256_fingerprint


def _configuration() -> IBMQuantumConfiguration:
    return IBMQuantumConfiguration(
        token=None,
        instance=None,
        backend_name="ibm_fake_backend",
        target_precision=0.5,
        optimization_level=2,
        maximum_seconds=60,
        dependency_available=True,
        image_identifier="sha256:" + "b" * 64,
        backend_qubit_capacity=8,
        requires_provider_credentials=False,
    )


def _ibm_executor(
    adapter: FakeIBMAdapter,
    local_executor: Any | None = None,
) -> IBMQuantumRunExecutor:
    return IBMQuantumRunExecutor(
        local_executor=local_executor or AuthorizedLocalExecutor(),
        adapter=adapter,
        configuration=_configuration(),
    )


def _coordinator(
    root: Path,
    adapter: FakeIBMAdapter,
    local_executor: Any | None = None,
) -> RunCoordinator:
    return RunCoordinator(
        run_root=root / "runs",
        manifest_resolver=_load_preset,
        executor=AuthorizedLocalExecutor(),
        ibm_executor=_ibm_executor(adapter, local_executor),
        enabled=True,
    )


def _approve_lih(
    client: TestClient,
) -> tuple[dict[str, Any], dict[str, Any]]:
    interpreted = client.post(
        "/api/v1/experiments/interpret",
        json={"question": LIH_QUESTION},
    )
    assert interpreted.status_code == 201
    interpretation = interpreted.json()
    approved = client.post(
        f"/api/v1/experiments/{interpretation['interpretation_identifier']}/approve",
        json={
            "specification": interpretation["specification"],
            "accepted_assumptions": True,
        },
    )
    assert approved.status_code == 201
    assert approved.json()["status"] == "ready_for_ibm_submission"
    return interpretation, approved.json()


def test_approved_lih_runs_through_existing_fake_ibm_worker_and_recovers(
    tmp_path: Path,
) -> None:
    provider = ControlledProvider([json.dumps(_complete_draft())])
    natural_store = NaturalLanguageInterpretationStore(
        tmp_path / "interpretations",
        provider,
    )
    experiment_store = ExperimentStore(tmp_path / "dynamic-experiments")
    adapter = FakeIBMAdapter()
    coordinator = _coordinator(tmp_path, adapter)
    idempotency_key = "approved-lih-fake-ibm-0001"

    with TestClient(
        create_app(
            coordinator=coordinator,
            experiment_store=experiment_store,
            natural_language_store=natural_store,
        )
    ) as client:
        interpretation, approved = _approve_lih(client)
        request = {
            "approved_experiment_identifier": approved["experiment_identifier"],
            "execution_target": "ibm_quantum",
        }
        created = client.post(
            "/api/v1/runs",
            headers={"Idempotency-Key": idempotency_key},
            json=request,
        )
        assert created.status_code == 202, created.json()
        repeated = client.post(
            "/api/v1/runs",
            headers={"Idempotency-Key": idempotency_key},
            json=request,
        )
        assert repeated.status_code == 202
        assert repeated.json()["run_identifier"] == created.json()["run_identifier"]
        terminal = wait_for_terminal(client, created.json()["run_identifier"])
        assert terminal["status"] == "authorized"
        results = client.get(
            f"/api/v1/runs/{terminal['run_identifier']}/results"
        ).json()
        verification = client.get(
            f"/api/v1/runs/{terminal['run_identifier']}/verification"
        ).json()
        receipt = client.get(
            f"/api/v1/runs/{terminal['run_identifier']}/receipt"
        ).json()

        override = client.post(
            "/api/v1/runs",
            json={
                **request,
                "basis": "6-31g",
            },
        )
        assert override.status_code == 422

    assert provider.request_count == 1
    assert adapter.submissions == 1
    run_directory = tmp_path / "runs" / terminal["run_identifier"]
    manifest = json.loads(
        (run_directory / "compiled-manifest.json").read_text(encoding="utf-8")
    )
    molecule = manifest["experiment"]["molecular_system"]
    assert [atom["element"] for atom in molecule["atoms"]] == ["Li", "H"]
    assert [atom["coordinates"] for atom in molecule["atoms"]] == [
        [-0.8, 0.0, 0.0],
        [0.8, 0.0, 0.0],
    ]
    assert molecule["coordinate_unit"] == "angstrom"
    assert molecule["declared_bond_distance"] == 1.6
    assert molecule["molecular_charge"] == 0
    assert molecule["spin_multiplicity"] == 1
    assert manifest["experiment"]["electronic_structure"]["basis_set"] == "sto-3g"
    assert (
        manifest["experiment"]["electronic_structure"]["reference_method"]
        == "restricted_hartree_fock"
    )
    assert (
        manifest["experiment"]["electronic_structure"]["active_electron_count"]
        == 2
    )
    assert (
        manifest["experiment"]["electronic_structure"][
            "active_spatial_orbital_count"
        ]
        == 2
    )
    assert manifest["experiment"]["electronic_structure"][
        "active_orbital_indices"
    ] == [1, 2]
    assert manifest["experiment"]["quantum_model"]["mapper"] == "jordan_wigner"
    assert manifest["experiment"]["quantum_model"]["ansatz"] == "uccsd"
    assert manifest["experiment"]["quantum_model"]["optimizer"] == "slsqp"
    assert (
        manifest["experiment"]["quantum_model"]["convergence_threshold"]
        == approved["specification"]["tolerance"]["value"]
    )
    execution_parameters = manifest["experiment"]["parent_experiment"][
        "execution_policy"
    ]["parameters"]
    assert (
        execution_parameters["approved_specification_sha256"]
        == approved["specification_sha256"]
    )
    assert (
        execution_parameters["approved_scientific_objective"]
        == approved["specification"]["scientific_objective"]["value"]
    )
    assert (
        execution_parameters["approved_requested_quantity"]
        == approved["specification"]["requested_quantity"]["value"]
    )
    assert (
        execution_parameters["compiled_objective"]
        == "molecular_ground_state_energy"
    )
    assert (
        execution_parameters["requested_precision"]
        == approved["specification"]["precision"]["value"]
    )

    state_provenance = terminal["approved_experiment"]
    assert terminal["source_type"] == "approved_experiment"
    assert terminal["preset_identifier"] is None
    assert (
        state_provenance["interpretation_identifier"]
        == interpretation["interpretation_identifier"]
    )
    assert (
        state_provenance["approved_specification_sha256"]
        == approved["specification_sha256"]
    )
    assert (
        state_provenance["approved_scientific_objective"]
        == approved["specification"]["scientific_objective"]["value"]
    )
    assert (
        state_provenance["approved_requested_quantity"]
        == approved["specification"]["requested_quantity"]["value"]
    )
    assert state_provenance["compiled_objective"] == "molecular_ground_state_energy"
    assert (
        state_provenance["compiled_execution_manifest_sha256"]
        == sha256_fingerprint(manifest)
    )
    assert state_provenance["molecule_structure_sha256"] == results["structure_sha256"]
    assert state_provenance["mapper"] == "jordan_wigner"
    assert state_provenance["ansatz"] == "uccsd"
    assert results["approved_experiment"] == state_provenance
    assert verification["approved_experiment"] == state_provenance
    assert receipt["approved_experiment"] == state_provenance
    for projection in (results, verification, receipt):
        assert projection["ibm_execution"]["job_identifier"] == "fake-job-0001"
        assert projection["ibm_execution"]["prepared_submission_sha256"]
        assert (
            projection["ibm_execution"]["target_precision"]
            == approved["specification"]["precision"]["value"]
        )
    assert verification["verification_passed"] is True
    assert receipt["authorized"] is True
    submission = json.loads(
        (run_directory / "ibm-worker" / "submission.json").read_text(
            encoding="utf-8"
        )
    )
    assert submission["experiment_identifier"] == approved["experiment_identifier"]
    assert (
        submission["experiment_sha256"]
        == manifest["expected_experiment_sha256"]
    )
    assert submission["structure_sha256"] == results["structure_sha256"]
    assert (
        submission["target_precision"]
        == approved["specification"]["precision"]["value"]
    )
    assert (
        receipt["ibm_execution"]["prepared_submission_sha256"]
        == sha256_fingerprint(submission)
    )
    assert (
        json.loads(
            (run_directory / "ibm-worker" / "job.json").read_text(
                encoding="utf-8"
            )
        )["job_identifier"]
        == "fake-job-0001"
    )

    state_path = run_directory / "state.json"
    interrupted_state = json.loads(state_path.read_text(encoding="utf-8"))
    interrupted_state["status"] = "running_on_ibm"
    write_json_atomic(
        state_path,
        interrupted_state,
        maximum_bytes=2 * 1024 * 1024,
    )
    recovered = _coordinator(tmp_path, adapter)
    with TestClient(
        create_app(
            coordinator=recovered,
            experiment_store=experiment_store,
            natural_language_store=natural_store,
        )
    ) as client:
        recovered_state = wait_for_terminal(client, terminal["run_identifier"])
        recovered_receipt = client.get(
            f"/api/v1/runs/{terminal['run_identifier']}/receipt"
        ).json()
    assert recovered_state["status"] == "authorized"
    assert recovered_receipt["approved_experiment"] == state_provenance
    assert recovered_receipt["ibm_execution"]["job_identifier"] == "fake-job-0001"
    assert adapter.submissions == 1
    assert adapter.retrievals == 1
    assert provider.request_count == 1


@pytest.mark.parametrize(
    "unsupported_intent",
    [
        "excited-state energy",
        "dipole moment",
        "geometry optimisation",
        "binding energy",
        "ionisation energy",
        "vibrational spectrum",
        "transition energy",
    ],
)
def test_approved_bridge_rejects_unsupported_intent_before_preflight(
    tmp_path: Path,
    unsupported_intent: str,
) -> None:
    provider = ControlledProvider([json.dumps(_complete_draft())])
    natural_store = NaturalLanguageInterpretationStore(
        tmp_path / "interpretations",
        provider,
    )
    adapter = FakeIBMAdapter()
    local_executor = AuthorizedLocalExecutor()
    with TestClient(
        create_app(
            coordinator=_coordinator(
                tmp_path,
                adapter,
                local_executor,
            ),
            experiment_store=ExperimentStore(tmp_path / "dynamic-experiments"),
            natural_language_store=natural_store,
        )
    ) as client:
        _, approved = _approve_lih(client)
        record_path = (
            tmp_path
            / "interpretations"
            / "approved"
            / approved["experiment_identifier"]
            / "experiment.json"
        )
        altered = json.loads(record_path.read_text(encoding="utf-8"))
        altered["specification"]["scientific_objective"] = {
            "value": unsupported_intent,
            "provenance": "explicit",
        }
        altered["specification"]["requested_quantity"] = {
            "value": unsupported_intent,
            "provenance": "explicit",
        }
        altered["specification_sha256"] = sha256_fingerprint(
            altered["specification"]
        )
        write_json_atomic(
            record_path,
            altered,
            maximum_bytes=2 * 1024 * 1024,
        )
        response = client.post(
            "/api/v1/runs",
            json={
                "approved_experiment_identifier": approved[
                    "experiment_identifier"
                ],
                "execution_target": "ibm_quantum",
            },
        )
    assert response.status_code == 422
    assert (
        response.json()["detail"]["code"]
        == "approved_experiment_not_executable"
    )
    assert local_executor.calls == 0
    assert adapter.submissions == 0


def test_approved_unnamed_h2_compiles_from_formula_and_structure(
    tmp_path: Path,
) -> None:
    provider = ControlledProvider(
        [json.dumps(_real_qwen_h2_identity_omitted_draft())]
    )
    natural_store = NaturalLanguageInterpretationStore(
        tmp_path / "interpretations",
        provider,
    )
    adapter = FakeIBMAdapter()
    with TestClient(
        create_app(
            coordinator=_coordinator(tmp_path, adapter),
            experiment_store=ExperimentStore(tmp_path / "dynamic-experiments"),
            natural_language_store=natural_store,
        )
    ) as client:
        interpreted = client.post(
            "/api/v1/experiments/interpret",
            json={"question": H2_QUESTION},
        )
        assert interpreted.status_code == 201
        interpretation = interpreted.json()
        specification = interpretation["specification"]
        assert specification["molecule"]["name"] == {
            "value": None,
            "provenance": "missing",
        }
        assert specification["molecule"]["formula"] == {
            "value": "H2",
            "provenance": "explicit",
        }
        assert specification["molecule"]["atoms"]["value"] == [
            {"element": "H", "coordinates": [-0.3675, 0.0, 0.0]},
            {"element": "H", "coordinates": [0.3675, 0.0, 0.0]},
        ]
        assert specification["molecule"]["bond_lengths"]["value"] == [
            {
                "atom_indices": [0, 1],
                "value": 0.735,
                "unit": "angstrom",
            }
        ]
        assert specification["mapper"]["value"] == "jordan_wigner"
        assert (
            specification["requested_execution_target"]["value"]
            == "ibm_quantum"
        )
        approved_response = client.post(
            f"/api/v1/experiments/{interpretation['interpretation_identifier']}/approve",
            json={
                "specification": specification,
                "accepted_assumptions": True,
            },
        )
        assert approved_response.status_code == 201
        approved = approved_response.json()
        created = client.post(
            "/api/v1/runs",
            json={
                "approved_experiment_identifier": approved[
                    "experiment_identifier"
                ],
                "execution_target": "ibm_quantum",
            },
        )
        assert created.status_code == 202, created.json()
        terminal = wait_for_terminal(client, created.json()["run_identifier"])
        assert terminal["status"] == "authorized"

    assert provider.request_count == 1
    assert adapter.submissions == 1
    molecule = terminal["molecule"]
    assert molecule["molecular_formula"] == "H2"
    assert "molecule_name" not in molecule
    assert molecule["elements"] == ["H", "H"]
    assert [atom["coordinates"] for atom in molecule["atoms"]] == [
        [-0.3675, 0.0, 0.0],
        [0.3675, 0.0, 0.0],
    ]
    assert molecule["bonds"] == [
        {
            "bond_identifier": "bond.0-1",
            "atom_identifiers": ["h-1", "h-2"],
            "declared_distance": 0.735,
            "derived_distance": 0.735,
        }
    ]


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (
            lambda record: record.update(
                {"status": "approved_pending_compiler_support"}
            ),
            "approved_experiment_not_executable",
        ),
        (
            lambda record: record["specification"]["charge"].update({"value": 1}),
            "approved_experiment_not_executable",
        ),
    ],
)
def test_approved_bridge_rejects_not_ready_or_hash_mismatched_records(
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], None],
    expected_code: str,
) -> None:
    provider = ControlledProvider([json.dumps(_complete_draft())])
    natural_store = NaturalLanguageInterpretationStore(
        tmp_path / "interpretations",
        provider,
    )
    adapter = FakeIBMAdapter()
    with TestClient(
        create_app(
            coordinator=_coordinator(tmp_path, adapter),
            experiment_store=ExperimentStore(tmp_path / "dynamic-experiments"),
            natural_language_store=natural_store,
        )
    ) as client:
        _, approved = _approve_lih(client)
        record_path = (
            tmp_path
            / "interpretations"
            / "approved"
            / approved["experiment_identifier"]
            / "experiment.json"
        )
        record = json.loads(record_path.read_text(encoding="utf-8"))
        mutation(record)
        write_json_atomic(
            record_path,
            record,
            maximum_bytes=2 * 1024 * 1024,
        )
        response = client.post(
            "/api/v1/runs",
            json={
                "approved_experiment_identifier": approved[
                    "experiment_identifier"
                ],
                "execution_target": "ibm_quantum",
            },
        )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == expected_code
    assert adapter.submissions == 0


@pytest.mark.parametrize(
    ("field_path", "replacement"),
    [
        (("precision",), {"value": None, "provenance": "missing"}),
        (("basis",), {"value": "6-31g", "provenance": "explicit"}),
        (("shots",), {"value": 4096, "provenance": "explicit"}),
    ],
)
def test_approved_bridge_rejects_complete_hash_bound_but_uncompilable_records(
    tmp_path: Path,
    field_path: tuple[str, ...],
    replacement: dict[str, Any],
) -> None:
    provider = ControlledProvider([json.dumps(_complete_draft())])
    natural_store = NaturalLanguageInterpretationStore(
        tmp_path / "interpretations",
        provider,
    )
    adapter = FakeIBMAdapter()
    with TestClient(
        create_app(
            coordinator=_coordinator(tmp_path, adapter),
            experiment_store=ExperimentStore(tmp_path / "dynamic-experiments"),
            natural_language_store=natural_store,
        )
    ) as client:
        _, approved = _approve_lih(client)
        record_path = (
            tmp_path
            / "interpretations"
            / "approved"
            / approved["experiment_identifier"]
            / "experiment.json"
        )
        record = json.loads(record_path.read_text(encoding="utf-8"))
        altered = deepcopy(record)
        altered["specification"][field_path[0]] = replacement
        altered["specification_sha256"] = sha256_fingerprint(
            altered["specification"]
        )
        write_json_atomic(
            record_path,
            altered,
            maximum_bytes=2 * 1024 * 1024,
        )
        response = client.post(
            "/api/v1/runs",
            json={
                "approved_experiment_identifier": approved[
                    "experiment_identifier"
                ],
                "execution_target": "ibm_quantum",
            },
        )
    assert response.status_code == 422
    assert (
        response.json()["detail"]["code"]
        == "approved_experiment_not_executable"
    )
    assert adapter.submissions == 0


def test_approved_bridge_rejects_unknown_unapproved_and_conflicting_target(
    tmp_path: Path,
) -> None:
    provider = ControlledProvider([json.dumps(_complete_draft())])
    natural_store = NaturalLanguageInterpretationStore(
        tmp_path / "interpretations",
        provider,
    )
    adapter = FakeIBMAdapter()
    with TestClient(
        create_app(
            coordinator=_coordinator(tmp_path, adapter),
            experiment_store=ExperimentStore(tmp_path / "dynamic-experiments"),
            natural_language_store=natural_store,
        )
    ) as client:
        interpretation, approved = _approve_lih(client)
        unknown = client.post(
            "/api/v1/runs",
            json={
                "approved_experiment_identifier": "experiment-" + "0" * 32,
                "execution_target": "ibm_quantum",
            },
        )
        unapproved = client.post(
            "/api/v1/runs",
            json={
                "approved_experiment_identifier": interpretation[
                    "interpretation_identifier"
                ],
                "execution_target": "ibm_quantum",
            },
        )
        conflict = client.post(
            "/api/v1/runs",
            json={
                "approved_experiment_identifier": approved[
                    "experiment_identifier"
                ],
                "execution_target": "local_simulator",
            },
        )
    assert unknown.status_code == 404
    assert unapproved.status_code == 404
    assert conflict.status_code == 422
    assert conflict.json()["detail"]["code"] == "execution_target_mismatch"
    assert adapter.submissions == 0
