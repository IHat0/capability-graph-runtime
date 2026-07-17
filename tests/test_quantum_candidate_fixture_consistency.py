from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

from cgr.quantum_candidate.contracts import CandidateOutputSummary
from cgr.quantum_candidate.contracts import CandidateSandboxPolicy
from cgr.quantum_candidate.protocol import (
    collect_candidate_output,
    load_candidate_summary,
)
from cgr.quantum_preflight.manifests import load_manifest

SUPPORT = Path(
    "benchmark-fixtures/quantum-candidate-v1/_support/standalone_candidate.py"
)
PUBLIC_MANIFEST = Path("benchmark-manifests/quantum-preflight/lih-ground-state-v1.json")
BENCHMARK_MANIFEST = Path(
    "benchmark-manifests/quantum-candidate/lih-candidate-benchmark-v1.json"
)


def _load_candidate_support() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "standalone_candidate_fixture", SUPPORT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _public_experiment() -> dict[str, Any]:
    return load_manifest(PUBLIC_MANIFEST).experiment.model_dump(mode="json")


def test_wrong_bond_preparation_is_a_deep_copy() -> None:
    candidate = _load_candidate_support()
    public_experiment = _public_experiment()
    original = copy.deepcopy(public_experiment)

    prepared = candidate.prepare_execution_experiment(
        "wrong-bond-distance", public_experiment
    )

    assert prepared is not public_experiment
    assert prepared["molecular_system"] is not public_experiment["molecular_system"]
    assert (
        prepared["molecular_system"]["atoms"]
        is not public_experiment["molecular_system"]["atoms"]
    )
    assert public_experiment == original
    assert prepared["molecular_system"]["atoms"][1]["coordinates"][2] == 1.7
    assert prepared["molecular_system"]["declared_bond_distance"] == 1.7
    assert prepared["molecular_system"]["coordinate_unit"] == "angstrom"


def test_wrong_bond_executes_and_hashes_one_consistent_1_7_workflow(
    tmp_path: Path, monkeypatch: Any
) -> None:
    candidate = _load_candidate_support()
    public_experiment = _public_experiment()
    original = copy.deepcopy(public_experiment)
    calls: dict[str, Any] = {}

    def fake_construct(execution_experiment: dict[str, Any]) -> tuple[Any, ...]:
        calls["construct_experiment"] = copy.deepcopy(execution_experiment)
        distance = execution_experiment["molecular_system"]["atoms"][1]["coordinates"][
            2
        ]
        structure = copy.deepcopy(execution_experiment["molecular_system"])
        structure.update({"driver_spin": 0, "total_electron_count": 4})
        electronic = {
            "basis_set": execution_experiment["electronic_structure"]["basis_set"],
            "execution_geometry_bond_distance": distance,
        }
        active = {
            "active_electron_count": 2,
            "active_spatial_orbital_count": 2,
            "declared_active_orbital_indices": [1, 2],
            "electronic_problem_sha256": candidate.fingerprint(electronic),
        }
        fermionic = {
            "electronic_problem_sha256": candidate.fingerprint(electronic),
            "terms": [{"label": "+_1 -_1", "coefficient": distance}],
        }
        qubit = {
            "mapper": execution_experiment["quantum_model"]["mapper"],
            "fermionic_hamiltonian_sha256": candidate.fingerprint(fermionic),
            "terms": [{"label": "IZ", "coefficient": distance}],
        }
        payloads = {
            "molecular_structure": structure,
            "electronic_problem": electronic,
            "active_space": active,
            "fermionic_hamiltonian": fermionic,
            "qubit_hamiltonian": qubit,
        }
        return payloads, "problem-1.7", "mapper-1.7", "ansatz-1.7"

    def fake_solve(
        execution_experiment: dict[str, Any],
        payloads: dict[str, Any],
        problem: Any,
        mapper: Any,
        ansatz: Any,
    ) -> None:
        calls["solve_experiment"] = copy.deepcopy(execution_experiment)
        calls["solve_inputs"] = (problem, mapper, ansatz)
        active_sha = candidate.fingerprint(payloads["active_space"])
        qubit_sha = candidate.fingerprint(payloads["qubit_hamiltonian"])
        environment = {"runtime": "fixture-test"}
        payloads["environment"] = environment
        payloads["optimization_trace"] = [{"evaluation": 1, "energy_hartree": -1.0}]
        payloads["ansatz_manifest"] = {
            "active_space_sha256": active_sha,
            "hamiltonian_sha256": qubit_sha,
        }
        payloads["candidate_result"] = {
            "hamiltonian_sha256": qubit_sha,
            "environment_sha256": candidate.fingerprint(environment),
            "electronic_energy_hartree": -1.5,
            "nuclear_repulsion_energy_hartree": 0.5,
            "total_energy_hartree": -1.0,
            "converged": True,
        }

    monkeypatch.setattr(candidate, "construct", fake_construct)
    monkeypatch.setattr(candidate, "solve", fake_solve)
    input_sha256 = "a" * 64
    candidate.emit(
        "wrong-bond-distance",
        "standalone-qiskit-candidate",
        input_sha256,
        public_experiment,
        tmp_path,
    )

    assert public_experiment == original
    assert calls["construct_experiment"] == calls["solve_experiment"]
    assert (
        calls["construct_experiment"]["molecular_system"]["declared_bond_distance"]
        == 1.7
    )
    assert calls["solve_inputs"] == ("problem-1.7", "mapper-1.7", "ansatz-1.7")

    artifacts = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in tmp_path.glob("*.json")
        if path.name != "candidate-summary.json"
    }
    summary_payload = json.loads(
        (tmp_path / "candidate-summary.json").read_text(encoding="utf-8")
    )
    summary = CandidateOutputSummary.model_validate(summary_payload)
    structure = artifacts["molecular-structure.json"]
    electronic = artifacts["electronic-problem.json"]
    fermionic = artifacts["fermionic-hamiltonian.json"]
    qubit = artifacts["qubit-hamiltonian.json"]
    result = artifacts["candidate-result.json"]

    assert structure["atoms"][1]["coordinates"][2] == 1.7
    assert structure["declared_bond_distance"] == 1.7
    assert structure["coordinate_unit"] == "angstrom"
    assert electronic["execution_geometry_bond_distance"] == 1.7
    assert fermionic["electronic_problem_sha256"] == candidate.fingerprint(electronic)
    assert qubit["fermionic_hamiltonian_sha256"] == candidate.fingerprint(fermionic)
    assert result["hamiltonian_sha256"] == candidate.fingerprint(qubit)
    assert summary.input_manifest_sha256 == input_sha256
    assert summary.claimed_molecular_specification == original["molecular_system"]
    assert summary.claimed_molecular_specification["declared_bond_distance"] == 1.6
    assert {(edge.source_role, edge.destination_role) for edge in summary.lineage} == {
        ("molecular_structure", "electronic_problem"),
        ("electronic_problem", "active_space"),
        ("active_space", "fermionic_hamiltonian"),
        ("fermionic_hamiltonian", "qubit_hamiltonian"),
        ("qubit_hamiltonian", "candidate_result"),
        ("optimization_trace", "candidate_result"),
    }
    for claim in summary.artifacts:
        assert (
            claim.content_sha256
            == hashlib.sha256((tmp_path / claim.path).read_bytes()).hexdigest()
        )


def test_neighboring_benchmark_expectations_remain_unchanged() -> None:
    benchmark = json.loads(BENCHMARK_MANIFEST.read_text(encoding="utf-8"))
    expectations = {
        item["case_identifier"]: item["expected_primary_finding"]
        for item in benchmark["cases"]
    }
    assert expectations["wrong-bond-distance"] == "candidate_structure_mismatch"
    assert expectations["malformed-output"] == "candidate_protocol_invalid"
    assert expectations["forged-content-hash"] == "candidate_content_hash_mismatch"
    assert expectations["cross-linked-artifacts"] == "candidate_lineage_mismatch"
    assert expectations["wrong-basis"] == "candidate_basis_mismatch"
    assert expectations["wrong-charge"] == "candidate_charge_mismatch"
    assert expectations["wrong-multiplicity"] == "candidate_multiplicity_mismatch"
    assert expectations["valid-control"] is None


def test_malformed_output_remains_a_protocol_failure(tmp_path: Path) -> None:
    (tmp_path / "candidate-summary.json").write_text("{not-json", encoding="utf-8")
    package = collect_candidate_output(tmp_path, CandidateSandboxPolicy())
    summary, findings = load_candidate_summary(package)
    assert summary is None
    assert [item.code for item in findings] == ["candidate_protocol_invalid"]
