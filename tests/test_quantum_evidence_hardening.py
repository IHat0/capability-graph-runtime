from __future__ import annotations

import json
import subprocess
import warnings
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from cgr.quantum_preflight.acceptance import (
    AcceptanceFailure,
    EXIT_MUTATION,
    EXIT_REPEAT,
    _require_checks,
    evaluate_mutation_sensitivity,
    evaluate_repeat_determinism,
    run_acceptance,
)
from cgr.quantum_preflight.artifacts import artifact_document, artifact_reference, write_json_atomic
from cgr.quantum_preflight.identities import (
    ScientificResultArtifact,
    ScientificResultIdentity,
    inspect_result_artifact,
)
from cgr.quantum_preflight.results import EnergyResult
from cgr.quantum_preflight.manifests import load_manifest
from cgr.quantum_preflight.warnings import capture_warnings, normalize_warning_message, warning_evidence

ROOT = Path(__file__).resolve().parents[1]
H = "a" * 64


def _result(*, duration: float = 1.0, energy: float = 2.0) -> EnergyResult:
    return EnergyResult(
        solver_identifier="numpy_minimum_eigensolver",
        solver_version="0.4.0",
        hamiltonian_sha256=H,
        environment_sha256="b" * 64,
        electronic_energy_hartree=energy - 1.0,
        nuclear_repulsion_energy_hartree=1.0,
        total_energy_hartree=energy,
        raw_eigenvalue_hartree=energy - 1.2,
        particle_count=2.0,
        number_of_spatial_orbitals=2,
        number_of_spin_orbitals=4,
        number_of_qubits=4,
        particle_sector_filter_applied=True,
        completed=True,
        duration_seconds=duration,
    )


def _identity(**updates: object) -> ScientificResultIdentity:
    value: dict[str, object] = {
        "result_kind": "exact_ground_state",
        "experiment_sha256": "1" * 64,
        "molecular_structure_sha256": "2" * 64,
        "electronic_problem_sha256": "3" * 64,
        "active_space_sha256": "4" * 64,
        "fermionic_hamiltonian_sha256": "5" * 64,
        "qubit_hamiltonian_sha256": H,
        "solver_identifier": "numpy_minimum_eigensolver",
        "solver_version": "0.4.0",
        "solver_configuration_sha256": "6" * 64,
        "environment_compatibility_sha256": "7" * 64,
        "electronic_energy": 1.0,
        "nuclear_repulsion_energy": 1.0,
        "total_energy": 2.0,
        "particle_count": 2.0,
        "number_of_spatial_orbitals": 2,
        "number_of_spin_orbitals": 4,
        "number_of_qubits": 4,
        "converged": True,
        "auxiliary_scientific_values": {"raw_eigenvalue_hartree": 0.8},
        "particle_sector_filtering_policy": "default_particle_sector_filter",
        "mapper_identifier": "jordan_wigner",
        "verification_policy_sha256": "8" * 64,
    }
    value.update(updates)
    return ScientificResultIdentity.model_validate(value)


def _artifact(
    *,
    duration: float = 1.0,
    identity: ScientificResultIdentity | None = None,
    execution_metadata: dict[str, str] | None = None,
) -> ScientificResultArtifact:
    identity = identity or _identity()
    return ScientificResultArtifact(
        scientific_identity=identity,
        scientific_result_sha256=identity.fingerprint,
        execution_result=_result(duration=duration, energy=identity.total_energy),
        execution_metadata=execution_metadata or {},
    )


@pytest.mark.quantum_unit
def test_duration_changes_full_hash_but_not_scientific_identity() -> None:
    first = _artifact(duration=1.0)
    second = _artifact(duration=2.0)
    first_ref = artifact_reference("exact_result", "exact", first.model_dump(mode="json"), filename="a.json")
    second_ref = artifact_reference("exact_result", "exact", second.model_dump(mode="json"), filename="b.json")
    assert first.scientific_result_sha256 == second.scientific_result_sha256
    assert first_ref.content_sha256 != second_ref.content_sha256


@pytest.mark.quantum_unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("total_energy", 2.1),
        ("qubit_hamiltonian_sha256", "9" * 64),
        ("solver_configuration_sha256", "0" * 64),
        ("environment_compatibility_sha256", "d" * 64),
        ("converged", False),
    ],
)
def test_scientific_mutations_change_identity(field: str, value: object) -> None:
    assert _identity().fingerprint != _identity(**{field: value}).fingerprint


@pytest.mark.quantum_unit
def test_volatile_execution_fields_are_absent_from_projection() -> None:
    fields = ScientificResultIdentity.model_fields
    assert not {"duration_seconds", "run_id", "timestamp", "output_path", "log_path"}.intersection(fields)


@pytest.mark.quantum_unit
def test_timestamp_run_and_path_change_only_complete_execution_artifact() -> None:
    first = _artifact(
        execution_metadata={"execution_identifier": "run-001", "timestamp": "one", "output_path": "a"}
    )
    second = _artifact(
        execution_metadata={"execution_identifier": "run-002", "timestamp": "two", "output_path": "b"}
    )
    first_ref = artifact_reference("exact_result", "exact", first.model_dump(mode="json"), filename="a.json")
    second_ref = artifact_reference("exact_result", "exact", second.model_dump(mode="json"), filename="b.json")
    assert first.scientific_result_sha256 == second.scientific_result_sha256
    assert first_ref.content_sha256 != second_ref.content_sha256


@pytest.mark.quantum_unit
def test_forged_result_identity_is_recomputed() -> None:
    value = _artifact().model_dump(mode="json")
    value["scientific_result_sha256"] = "f" * 64
    with pytest.raises(ValidationError, match="canonical projection"):
        ScientificResultArtifact.model_validate(value)


@pytest.mark.quantum_unit
def test_legacy_flat_result_is_inspectable_but_not_hardened() -> None:
    inspected = inspect_result_artifact(_result().model_dump(mode="json"))
    assert inspected.legacy and not inspected.hardened


@pytest.mark.quantum_unit
def test_vqe_optimized_parameters_trace_and_convergence_enter_identity() -> None:
    common = {
        "result_kind": "vqe_ground_state",
        "particle_sector_filtering_policy": None,
        "estimator_type": "statevector_estimator",
        "initial_point_sha256": "a" * 64,
        "optimized_parameters_sha256": "b" * 64,
        "optimization_trace_sha256": "c" * 64,
        "ansatz_sha256": "d" * 64,
        "ansatz_identifier": "uccsd",
        "initial_state_identifier": "hartree_fock",
    }
    baseline = _identity(**common)
    for field in ("optimized_parameters_sha256", "optimization_trace_sha256"):
        changed = _identity(**{**common, field: "e" * 64})
        assert changed.fingerprint != baseline.fingerprint
    assert _identity().fingerprint != baseline.fingerprint


@pytest.mark.quantum_unit
def test_warning_normalization_removes_paths_addresses_and_spacing() -> None:
    normalized = normalize_warning_message("  C:\\tmp\\x.py  object 0xABC123  ")
    assert "C:" not in normalized and "0xABC123" not in normalized and "  " not in normalized


@pytest.mark.quantum_unit
def test_warning_order_is_stable_and_count_changes_identity() -> None:
    with capture_warnings("vqe_execution") as first_capture:
        warnings.warn("BlueprintCircuit is deprecated", DeprecationWarning)
        warnings.warn("unknown runtime condition", RuntimeWarning)
    with capture_warnings("vqe_execution") as second_capture:
        warnings.warn("unknown runtime condition", RuntimeWarning)
        warnings.warn("BlueprintCircuit is deprecated", DeprecationWarning)
    assert warning_evidence(first_capture).fingerprint == warning_evidence(second_capture).fingerprint
    with capture_warnings("vqe_execution") as third_capture:
        warnings.warn("BlueprintCircuit is deprecated", DeprecationWarning)
        warnings.warn("BlueprintCircuit is deprecated", DeprecationWarning)
        warnings.warn("unknown runtime condition", RuntimeWarning)
    assert warning_evidence(first_capture).fingerprint != warning_evidence(third_capture).fingerprint


@pytest.mark.quantum_unit
def test_known_and_unknown_warnings_are_classified_and_blocking_is_configurable() -> None:
    class SparseEfficiencyWarning(Warning):
        pass

    with capture_warnings("problem_construction") as captured:
        warnings.warn("BlueprintCircuit is deprecated", DeprecationWarning)
        warnings.warn("sparse efficiency issue", SparseEfficiencyWarning)
        warnings.warn("unclassified", RuntimeWarning)
    evidence = warning_evidence(captured)
    codes = {item.code for item in evidence.warnings}
    assert {"qiskit_blueprint_circuit_deprecated", "scipy_sparse_efficiency_warning", "dependency_runtime_warning"} <= codes
    blocked = warning_evidence(captured, blocking_codes=frozenset({"dependency_runtime_warning"}))
    assert blocked.status == "blocking"


def _summary(seed: str) -> dict[str, object]:
    keys = (
        "experiment_fingerprint", "structure_sha256", "qcschema_sha256",
        "electronic_problem_sha256", "active_space_sha256", "fermionic_hamiltonian_sha256",
        "qubit_hamiltonian_sha256", "exact_scientific_result_sha256",
        "vqe_scientific_result_sha256", "scientific_outcome_sha256",
        "optimized_parameters_sha256", "optimization_trace_scientific_sha256",
    )
    value: dict[str, object] = {key: seed * 64 for key in keys}
    value.update(
        authorized=True,
        exact_total_energy_hartree=2.0 if seed == "a" else 2.1,
        vqe_total_energy_hartree=2.000001 if seed == "a" else 2.100001,
    )
    return value


@pytest.mark.quantum_unit
def test_acceptance_repeat_and_mutation_evaluators(monkeypatch: pytest.MonkeyPatch) -> None:
    repeat = _summary("a")
    monkeypatch.setattr(
        "cgr.quantum_preflight.acceptance._results_equivalent_except_duration", lambda left, right: True
    )
    assert all(evaluate_repeat_determinism(repeat, deepcopy(repeat), vqe_tolerance=1e-8).values())
    mutation = _summary("b")
    assert all(evaluate_mutation_sensitivity(repeat, mutation).values())


@pytest.mark.quantum_unit
def test_acceptance_cannot_treat_missing_or_skipped_work_as_success() -> None:
    with pytest.raises(AcceptanceFailure) as repeat_failure:
        _require_checks({"run_a_authorized": False}, EXIT_REPEAT, "repeat")
    assert repeat_failure.value.exit_code == EXIT_REPEAT
    with pytest.raises(AcceptanceFailure) as mutation_failure:
        _require_checks({"mutation_authorized": False}, EXIT_MUTATION, "mutation")
    assert mutation_failure.value.exit_code == EXIT_MUTATION


@pytest.mark.quantum_unit
def test_mocked_acceptance_persists_sanitized_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = load_manifest(
        ROOT / "benchmark-manifests/quantum-preflight/lih-ground-state-v1.json"
    )
    calls = 0

    def fake_executor(executed_manifest, *, result_root, **kwargs):
        nonlocal calls
        del kwargs
        calls += 1
        mutated = executed_manifest.experiment.molecular_system.declared_bond_distance == 1.7
        summary = _summary("b" if mutated else "a")
        run_directory = result_root / executed_manifest.experiment.experiment_identifier / "run-001"
        run_directory.mkdir(parents=True)
        write_json_atomic(
            run_directory / "compatibility-warnings.json",
            artifact_document(
                "compatibility_warnings",
                {"schema_version": "cgr.compatibility-warnings/1.0.0", "warnings": [], "status": "clean"},
            ),
            maximum_bytes=4096,
        )
        summary.update(
            receipt_path=str(run_directory / "receipt.json"),
            run_id="run-001",
            compatibility_warnings_sha256="c" * 64,
            compatibility_status="clean",
        )
        return summary

    monkeypatch.setattr(
        "cgr.quantum_preflight.acceptance._results_equivalent_except_duration",
        lambda left, right: True,
    )
    monkeypatch.setattr(
        "cgr.quantum_preflight.acceptance.evaluate_evidence_integrity",
        lambda first, second, mutation: {"mocked_cross_links": True},
    )
    summary = run_acceptance(
        manifest,
        result_root=tmp_path,
        lock_path=ROOT / "requirements/quantum-preflight.lock",
        image_identifier="sha256:test",
        executor=fake_executor,
    )
    report = Path(summary["acceptance_report_path"])
    value = json.loads(report.read_text(encoding="utf-8"))
    assert calls == 3 and value["acceptance_passed"] is True
    assert str(tmp_path) not in report.read_text(encoding="utf-8")


@pytest.mark.quantum_unit
def test_quantum_scripts_enforce_isolation_entrypoints_and_permission_probe() -> None:
    acceptance = (ROOT / "scripts/run-lih-quantum-preflight-acceptance.sh").read_text(encoding="utf-8")
    integration = (ROOT / "scripts/run-quantum-preflight-integration.sh").read_text(encoding="utf-8")
    for source in (acceptance, integration):
        assert "--network none" in source and "--read-only" in source
        assert "--cap-drop ALL" in source and "--pids-limit" in source
        assert "/var/run/docker.sock" not in source and "AWS_SECRET" not in source and "IBM_QUANTUM" not in source
    assert "--entrypoint python" in acceptance and "--entrypoint python" in integration
    assert "CGR_QUANTUM_INTEGRATION=1" in integration and "CGR_QUANTUM_IMAGE_ID" in integration
    assert ".cgr-write-probe" in acceptance and "sudo chown -R 10001:10001" in acceptance


@pytest.mark.quantum_unit
def test_intended_shell_scripts_have_executable_git_mode() -> None:
    scripts = (
        "scripts/build-quantum-preflight-image.sh",
        "scripts/run-lih-quantum-preflight.sh",
        "scripts/run-lih-quantum-preflight-acceptance.sh",
        "scripts/run-quantum-preflight-integration.sh",
    )
    output = subprocess.check_output(["git", "ls-files", "--stage", *scripts], cwd=ROOT, text=True)
    modes = {line.split()[3]: line.split()[0] for line in output.splitlines()}
    assert all(modes.get(script) == "100755" for script in scripts)
