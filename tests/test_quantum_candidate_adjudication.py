from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cgr.quantum_candidate.adjudication import adjudicate_candidate
from cgr.quantum_candidate.contracts import (
    CandidateExecutionEvidence,
    CandidateSandboxPolicy,
)
from cgr.quantum_candidate.protocol import collect_candidate_output
from cgr.quantum_preflight.manifests import load_manifest
from cgr.science import sha256_fingerprint
from cgr.science.canonical import canonical_json

MANIFEST = Path("benchmark-manifests/quantum-preflight/lih-ground-state-v1.json")
ROLES = {
    "molecular_structure": "molecular-structure.json",
    "electronic_problem": "electronic-problem.json",
    "active_space": "active-space.json",
    "fermionic_hamiltonian": "fermionic-hamiltonian.json",
    "qubit_hamiltonian": "qubit-hamiltonian.json",
    "ansatz_manifest": "ansatz-manifest.json",
    "optimization_trace": "optimization-trace.json",
    "candidate_result": "candidate-result.json",
    "environment": "environment.json",
}
LINEAGE = (
    ("molecular_structure", "electronic_problem"),
    ("electronic_problem", "active_space"),
    ("active_space", "fermionic_hamiltonian"),
    ("fermionic_hamiltonian", "qubit_hamiltonian"),
    ("qubit_hamiltonian", "candidate_result"),
    ("optimization_trace", "candidate_result"),
)


def _fixture() -> tuple[Any, dict[str, Any], Any]:
    experiment = load_manifest(MANIFEST).experiment
    molecule = experiment.molecular_system.model_dump(mode="json")
    payloads: dict[str, Any] = {
        "molecular_structure": {
            **molecule,
            "driver_spin": experiment.molecular_system.driver_spin,
            "total_electron_count": experiment.molecular_system.total_electron_count,
        },
        "electronic_problem": {"basis_set": experiment.electronic_structure.basis_set},
        "active_space": {
            "active_electron_count": experiment.electronic_structure.active_electron_count,
            "active_spatial_orbital_count": experiment.electronic_structure.active_spatial_orbital_count,
            "declared_active_orbital_indices": list(
                experiment.electronic_structure.active_orbital_indices
            ),
        },
        "fermionic_hamiltonian": {
            "schema_version": "test.fermionic/1",
            "terms": [{"label": "+_0 -_0", "coefficient": 1.0}],
        },
        "qubit_hamiltonian": {
            "schema_version": "test.qubit/1",
            "mapper": experiment.quantum_model.mapper,
            "terms": [{"label": "Z", "coefficient": 1.0}],
        },
        "ansatz_manifest": {},
        "optimization_trace": [{"evaluation": 1, "energy_hartree": 1.0}],
        "environment": {"runtime": "test"},
    }
    payloads["candidate_result"] = {
        "solver_identifier": "statevector_vqe",
        "solver_version": "1.0.0",
        "hamiltonian_sha256": sha256_fingerprint(payloads["qubit_hamiltonian"]),
        "electronic_energy_hartree": 1.0,
        "nuclear_repulsion_energy_hartree": 1.0,
        "total_energy_hartree": 2.0,
        "raw_eigenvalue_hartree": 1.0,
        "particle_count": 2.0,
        "number_of_spatial_orbitals": 2,
        "number_of_spin_orbitals": 4,
        "number_of_qubits": 4,
        "converged": True,
        "estimator_type": "statevector_estimator",
        "initial_point_sha256": "1" * 64,
        "optimized_parameters_sha256": "2" * 64,
        "ansatz_identifier": "uccsd",
        "initial_state_identifier": "hartree_fock",
        "completed": True,
    }
    payloads["candidate_result"]["environment_sha256"] = sha256_fingerprint(
        payloads["environment"]
    )
    payloads["candidate_result"]["initial_point_sha256"] = sha256_fingerprint(
        [0.0, 0.0]
    )
    payloads["ansatz_manifest"] = {
        "ansatz": "uccsd",
        "initial_state": "hartree_fock",
        "mapper": experiment.quantum_model.mapper,
        "number_of_parameters": 2,
        "active_space_sha256": sha256_fingerprint(payloads["active_space"]),
        "hamiltonian_sha256": sha256_fingerprint(payloads["qubit_hamiltonian"]),
        "initial_point_sha256": payloads["candidate_result"]["initial_point_sha256"],
        "optimized_parameters_sha256": payloads["candidate_result"][
            "optimized_parameters_sha256"
        ],
    }
    trusted = SimpleNamespace(
        receipt_content_sha256="3" * 64,
        exact_total_energy=2.0,
        molecular_structure=copy.deepcopy(payloads["molecular_structure"]),
        electronic_problem=copy.deepcopy(payloads["electronic_problem"]),
        active_space=copy.deepcopy(payloads["active_space"]),
        fermionic_hamiltonian=copy.deepcopy(payloads["fermionic_hamiltonian"]),
        qubit_hamiltonian=copy.deepcopy(payloads["qubit_hamiltonian"]),
    )
    return experiment, payloads, trusted


def _adjudicate(
    tmp_path: Path,
    payloads: dict[str, Any],
    trusted: Any,
    *,
    claimed_scientific_sha: str | None = None,
    authorized: bool | None = None,
    lineage: tuple[tuple[str, str], ...] = LINEAGE,
    forged_role: str | None = None,
) -> Any:
    experiment = load_manifest(MANIFEST).experiment
    output = tmp_path / "output"
    output.mkdir()
    claims = []
    for role, filename in ROLES.items():
        data = canonical_json(payloads[role]).encode()
        (output / filename).write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        claims.append(
            {
                "role": role,
                "path": filename,
                "content_sha256": "f" * 64 if role == forged_role else digest,
            }
        )
    summary = {
        "schema_version": "cgr.quantum-candidate-output/1.0.0",
        "candidate_identifier": "test-candidate",
        "input_manifest_sha256": "a" * 64,
        "execution_completed": True,
        "claimed_workflow": "lih_statevector_vqe",
        "artifacts": claims,
        "lineage": [
            {"source_role": source, "destination_role": destination}
            for source, destination in lineage
        ],
        "claimed_molecular_specification": experiment.molecular_system.model_dump(
            mode="json"
        ),
        "claimed_active_space": experiment.electronic_structure.model_dump(mode="json"),
        "claimed_mapper": payloads["qubit_hamiltonian"].get("mapper", "jordan_wigner"),
        "claimed_solver": "statevector_vqe",
        "claimed_energies": {
            key: payloads["candidate_result"].get(key)
            for key in (
                "electronic_energy_hartree",
                "nuclear_repulsion_energy_hartree",
                "total_energy_hartree",
            )
        },
        "claimed_converged": bool(payloads["candidate_result"].get("converged")),
        "claimed_scientific_result_sha256": claimed_scientific_sha,
        "authorized": authorized,
        "diagnostics": {},
    }
    (output / "candidate-summary.json").write_text(
        canonical_json(summary), encoding="utf-8"
    )
    package = collect_candidate_output(output, CandidateSandboxPolicy())
    execution = CandidateExecutionEvidence(
        candidate_identifier="test-candidate",
        source_tree_sha256="b" * 64,
        input_manifest_sha256="a" * 64,
        image_identifier="sha256:" + "c" * 64,
        sandbox_policy_sha256=CandidateSandboxPolicy().fingerprint,
        mount_manifest=CandidateSandboxPolicy().mounts,
        execution_category="completed",
        exit_code=0,
        timed_out=False,
        elapsed_seconds=0.1,
        stdout_sha256=hashlib.sha256(b"").hexdigest(),
        stderr_sha256=hashlib.sha256(b"").hexdigest(),
        stdout_bytes=0,
        stderr_bytes=0,
        output_bytes=package.total_bytes,
        output_files=len(package.files),
        network_disabled=True,
        trusted_evidence_exposed=False,
    )
    return adjudicate_candidate(
        experiment=experiment,
        execution=execution,
        package=package,
        trusted=trusted,
        candidate_dependency_lock_sha256="d" * 64,
    )


def test_valid_candidate_is_authorized_with_recomputed_receipt(tmp_path: Path) -> None:
    _, payloads, trusted = _fixture()
    receipt = _adjudicate(tmp_path, payloads, trusted)
    assert receipt.authorized is True
    assert receipt.primary_failure_code is None
    assert receipt.recomputed_scientific_result_sha256
    assert receipt.receipt_content_sha256 == receipt.fingerprint


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("coordinate", "candidate_coordinate_unit_mismatch"),
        ("structure", "candidate_structure_mismatch"),
        ("charge", "candidate_charge_mismatch"),
        ("multiplicity", "candidate_multiplicity_mismatch"),
        ("basis", "candidate_basis_mismatch"),
        ("active", "candidate_active_space_mismatch"),
        ("mapper", "candidate_mapper_mismatch"),
        ("fermionic", "candidate_fermionic_hamiltonian_mismatch"),
        ("qubit", "candidate_qubit_hamiltonian_mismatch"),
        ("nuclear", "candidate_nuclear_repulsion_missing"),
        ("total", "candidate_total_energy_semantics_invalid"),
        ("convergence", "candidate_vqe_not_converged"),
        ("energy", "candidate_energy_disagreement"),
        ("cross_link", "candidate_lineage_mismatch"),
        ("workflow", "candidate_protocol_invalid"),
    ],
)
def test_scientific_failure_classification(
    tmp_path: Path, mutation: str, expected: str
) -> None:
    _, payloads, trusted = _fixture()
    if mutation == "coordinate":
        payloads["molecular_structure"]["coordinate_unit"] = "bohr"
    elif mutation == "structure":
        payloads["molecular_structure"]["declared_bond_distance"] = 1.7
    elif mutation == "charge":
        payloads["molecular_structure"]["molecular_charge"] = 1
    elif mutation == "multiplicity":
        payloads["molecular_structure"]["spin_multiplicity"] = 3
    elif mutation == "basis":
        payloads["electronic_problem"]["basis_set"] = "6-31g"
    elif mutation == "active":
        payloads["active_space"]["active_electron_count"] = 4
    elif mutation == "mapper":
        payloads["qubit_hamiltonian"]["mapper"] = "parity"
    elif mutation == "fermionic":
        payloads["fermionic_hamiltonian"]["terms"] = []
    elif mutation == "qubit":
        payloads["qubit_hamiltonian"]["terms"] = []
    elif mutation == "nuclear":
        payloads["candidate_result"].pop("nuclear_repulsion_energy_hartree")
    elif mutation == "total":
        payloads["candidate_result"]["total_energy_hartree"] = 1.0
    elif mutation == "convergence":
        payloads["candidate_result"]["converged"] = False
    elif mutation == "energy":
        payloads["candidate_result"]["electronic_energy_hartree"] = 1.1
        payloads["candidate_result"]["total_energy_hartree"] = 2.1
    elif mutation == "cross_link":
        payloads["candidate_result"]["hamiltonian_sha256"] = "e" * 64
    elif mutation == "workflow":
        payloads["candidate_result"]["solver_identifier"] = "unreviewed_solver"
    receipt = _adjudicate(tmp_path, payloads, trusted)
    assert receipt.authorized is False
    assert receipt.primary_failure_code == expected


def test_candidate_claims_are_recomputed_and_have_no_authority(tmp_path: Path) -> None:
    _, payloads, trusted = _fixture()
    receipt = _adjudicate(
        tmp_path,
        payloads,
        trusted,
        claimed_scientific_sha="f" * 64,
        authorized=True,
        forged_role="environment",
    )
    codes = {item.code for item in receipt.findings}
    assert "candidate_untrusted_authorization_claim" in codes
    assert "candidate_content_hash_mismatch" in codes
    assert "candidate_scientific_identity_mismatch" in codes
    assert receipt.authorized is False
