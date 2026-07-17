"""Standalone public-input Qiskit workflow used by benchmark candidate fixtures."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import time
from pathlib import Path
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def encode_complex(value: complex | float) -> dict[str, str]:
    number = complex(value)
    real = float(number.real)
    imaginary = float(number.imag)
    real = 0.0 if real == 0.0 else real
    imaginary = 0.0 if imaginary == 0.0 else imaginary
    return {"real_hex": real.hex(), "imag_hex": imaginary.hex()}


def serialize_terms(terms: Any) -> list[dict[str, Any]]:
    combined: dict[str, complex] = {}
    for label, coefficient in terms:
        combined[str(label)] = combined.get(str(label), 0j) + complex(coefficient)
    return [
        {"label": label, "coefficient": encode_complex(combined[label])}
        for label in sorted(combined)
    ]


def first_numeric(value: Any) -> float | None:
    while isinstance(value, (list, tuple)) and value:
        value = value[0]
    try:
        number = float(complex(value).real)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def construct(experiment: dict[str, Any]) -> tuple[dict[str, Any], Any, Any, Any]:
    from qiskit_nature.second_q.circuit.library import HartreeFock, UCCSD
    from qiskit_nature.second_q.drivers import MethodType, PySCFDriver
    from qiskit_nature.second_q.formats.molecule_info import DistanceUnit
    from qiskit_nature.second_q.mappers import JordanWignerMapper
    from qiskit_nature.second_q.transformers import ActiveSpaceTransformer

    molecule = experiment["molecular_system"]
    electronic = experiment["electronic_structure"]
    atom = "; ".join(
        f"{item['element']} {item['coordinates'][0]} {item['coordinates'][1]} {item['coordinates'][2]}"
        for item in molecule["atoms"]
    )
    unit = (
        DistanceUnit.ANGSTROM
        if molecule["coordinate_unit"] == "angstrom"
        else DistanceUnit.BOHR
    )
    driver = PySCFDriver(
        atom=atom,
        unit=unit,
        charge=molecule["molecular_charge"],
        spin=molecule["spin_multiplicity"] - 1,
        basis=electronic["basis_set"],
        method=MethodType.RHF,
    )
    problem = driver.run()
    alpha = [float(item) for item in problem.orbital_occupations]
    beta = [float(item) for item in problem.orbital_occupations_b]
    occupations = [a + b for a, b in zip(alpha, beta)]
    active_indices = list(electronic["active_orbital_indices"])
    transformer = ActiveSpaceTransformer(
        num_electrons=electronic["active_electron_count"],
        num_spatial_orbitals=electronic["active_spatial_orbital_count"],
    )
    active_problem = transformer.transform(problem)
    fermionic = active_problem.hamiltonian.second_q_op()
    mapper = JordanWignerMapper()
    qubit = mapper.map(fermionic)
    total_electrons = (
        sum({"H": 1, "Li": 3}[item["element"]] for item in molecule["atoms"])
        - molecule["molecular_charge"]
    )
    payloads = {
        "molecular_structure": {
            **molecule,
            "driver_spin": molecule["spin_multiplicity"] - 1,
            "total_electron_count": total_electrons,
        },
        "electronic_problem": {
            "driver_identifier": electronic["driver_identifier"],
            "basis_set": electronic["basis_set"],
            "reference_method": electronic["reference_method"],
            "frozen_core_policy": electronic["frozen_core"],
            "pre_transform_particle_count": list(problem.num_particles),
            "pre_transform_spatial_orbitals": int(problem.num_spatial_orbitals),
            "pre_transform_spin_orbitals": int(problem.num_spin_orbitals),
            "orbital_occupations_alpha": alpha,
            "orbital_occupations_beta": beta,
            "orbital_occupations_total": occupations,
            "nuclear_repulsion_energy_hartree": float(
                problem.hamiltonian.nuclear_repulsion_energy
            ),
        },
        "active_space": {
            "active_electron_count": electronic["active_electron_count"],
            "active_spatial_orbital_count": int(active_problem.num_spatial_orbitals),
            "active_spin_orbital_count": int(active_problem.num_spin_orbitals),
            "declared_active_orbital_indices": active_indices,
            "resolved_active_orbital_indices": active_indices,
            "active_particle_count": list(active_problem.num_particles),
            "orbital_occupations_used": [
                occupations[index] for index in active_indices
            ],
            "resolution_policy": "validated_qiskit_nature_default_for_0.8.0",
        },
        "fermionic_hamiltonian": {
            "schema_version": "cgr.fermionic-operator/1.0.0",
            "coefficient_encoding": "ieee754-binary64-hex",
            "register_length": int(fermionic.register_length),
            "terms": serialize_terms(fermionic.items()),
        },
        "qubit_hamiltonian": {
            "schema_version": "cgr.qubit-operator/1.0.0",
            "coefficient_encoding": "ieee754-binary64-hex",
            "number_of_qubits": int(qubit.num_qubits),
            "mapper": experiment["quantum_model"]["mapper"],
            "terms": serialize_terms(qubit.to_list()),
        },
    }
    initial_state = HartreeFock(
        active_problem.num_spatial_orbitals, active_problem.num_particles, mapper
    )
    ansatz = UCCSD(
        active_problem.num_spatial_orbitals,
        active_problem.num_particles,
        mapper,
        initial_state=initial_state,
    )
    return payloads, active_problem, mapper, ansatz


def solve(
    experiment: dict[str, Any],
    payloads: dict[str, Any],
    problem: Any,
    mapper: Any,
    ansatz: Any,
) -> None:
    from qiskit.primitives import StatevectorEstimator
    from qiskit_algorithms import VQE
    from qiskit_algorithms.optimizers import SLSQP
    from qiskit_algorithms.utils import algorithm_globals
    from qiskit_nature.second_q.algorithms import GroundStateEigensolver

    quantum = experiment["quantum_model"]
    algorithm_globals.random_seed = quantum["random_seed"]
    initial_point = [0.0] * int(ansatz.num_parameters)
    trace: list[dict[str, Any]] = []

    def callback(evaluation: int, parameters: Any, mean: Any, metadata: Any) -> None:
        del metadata
        trace.append(
            {
                "evaluation": int(evaluation),
                "energy_hartree": float(complex(mean).real),
                "parameter_fingerprint": fingerprint(
                    [float(item) for item in parameters]
                ),
            }
        )

    optimizer = SLSQP(
        maxiter=quantum["maximum_iterations"],
        ftol=quantum["convergence_threshold"],
        disp=False,
    )
    solver = VQE(
        estimator=StatevectorEstimator(seed=quantum["random_seed"]),
        ansatz=ansatz,
        optimizer=optimizer,
        initial_point=initial_point,
        callback=callback,
    )
    started = time.perf_counter()
    interpreted = GroundStateEigensolver(mapper, solver).solve(problem)
    duration = time.perf_counter() - started
    raw = interpreted.raw_result
    point = [float(item) for item in getattr(raw, "optimal_point", ())]
    qubit_sha = fingerprint(payloads["qubit_hamiltonian"])
    environment = {
        "python": importlib.metadata.version("pip"),
        "qiskit": importlib.metadata.version("qiskit"),
        "qiskit_algorithms": importlib.metadata.version("qiskit-algorithms"),
        "qiskit_nature": importlib.metadata.version("qiskit-nature"),
        "pyscf": importlib.metadata.version("pyscf"),
    }
    payloads["environment"] = environment
    payloads["optimization_trace"] = trace
    payloads["ansatz_manifest"] = {
        "schema_version": "candidate.ansatz-manifest/1.0.0",
        "ansatz": quantum["ansatz"],
        "number_of_qubits": int(ansatz.num_qubits),
        "number_of_parameters": int(ansatz.num_parameters),
        "initial_state": quantum["initial_state"],
        "mapper": quantum["mapper"],
        "active_space_sha256": fingerprint(payloads["active_space"]),
        "hamiltonian_sha256": qubit_sha,
        "initial_point_sha256": fingerprint(initial_point),
        "optimized_parameters_sha256": fingerprint(point),
        "circuit_depth": int(ansatz.decompose().depth()),
        "operation_counts": dict(ansatz.decompose().count_ops()),
        "qiskit_version": importlib.metadata.version("qiskit"),
    }
    status = getattr(getattr(raw, "optimizer_result", None), "status", None)
    payloads["candidate_result"] = {
        "solver_identifier": "statevector_vqe",
        "solver_version": importlib.metadata.version("qiskit-algorithms"),
        "hamiltonian_sha256": qubit_sha,
        "environment_sha256": fingerprint(environment),
        "electronic_energy_hartree": float(
            complex(interpreted.electronic_energies[0]).real
        ),
        "nuclear_repulsion_energy_hartree": float(interpreted.nuclear_repulsion_energy),
        "total_energy_hartree": float(complex(interpreted.total_energies[0]).real),
        "raw_eigenvalue_hartree": float(complex(interpreted.computed_energies[0]).real),
        "particle_count": first_numeric(getattr(interpreted, "num_particles", None)),
        "number_of_spatial_orbitals": int(problem.num_spatial_orbitals),
        "number_of_spin_orbitals": int(problem.num_spin_orbitals),
        "number_of_qubits": int(ansatz.num_qubits),
        "completed": True,
        "duration_seconds": duration,
        "optimizer_identifier": quantum["optimizer"],
        "optimizer_status": "completed" if status is None else f"status_{status}",
        "optimizer_evaluations": max(
            int(getattr(raw, "cost_function_evals", len(trace))), len(trace), 1
        ),
        "initial_point_sha256": fingerprint(initial_point),
        "optimized_parameters_sha256": fingerprint(point),
        "ansatz_identifier": quantum["ansatz"],
        "initial_state_identifier": quantum["initial_state"],
        "converged": bool(point)
        and math.isfinite(float(interpreted.total_energies[0].real)),
        "estimator_type": "statevector_estimator",
    }


def mutate(mode: str, payloads: dict[str, Any]) -> dict[str, Any]:
    result = payloads["candidate_result"]
    if mode == "wrong-bond-distance":
        payloads["molecular_structure"]["atoms"][1]["coordinates"][2] = 1.7
        payloads["molecular_structure"]["declared_bond_distance"] = 1.7
    elif mode == "angstrom-bohr-confusion":
        payloads["molecular_structure"]["coordinate_unit"] = "bohr"
    elif mode == "wrong-charge":
        payloads["molecular_structure"]["molecular_charge"] = 1
    elif mode == "wrong-multiplicity":
        payloads["molecular_structure"]["spin_multiplicity"] = 3
    elif mode == "wrong-basis":
        payloads["electronic_problem"]["basis_set"] = "6-31g"
    elif mode == "wrong-active-space":
        payloads["active_space"]["active_electron_count"] = 4
    elif mode == "wrong-mapper":
        payloads["qubit_hamiltonian"]["mapper"] = "parity"
    elif mode == "wrong-hamiltonian":
        payloads["qubit_hamiltonian"]["terms"][0]["coefficient"]["real_hex"] = float(
            0.125
        ).hex()
    elif mode == "electronic-energy-as-total":
        result["total_energy_hartree"] = result["electronic_energy_hartree"]
    elif mode == "missing-nuclear-repulsion":
        result.pop("nuclear_repulsion_energy_hartree")
    elif mode == "nonconverged-vqe":
        result["converged"] = False
    elif mode == "energy-disagreement":
        result["electronic_energy_hartree"] += 0.1
        result["total_energy_hartree"] += 0.1
    elif mode == "cross-linked-artifacts":
        result["hamiltonian_sha256"] = "f" * 64
    return payloads


def emit(
    mode: str,
    candidate_identifier: str,
    input_sha: str,
    experiment: dict[str, Any],
    output: Path,
) -> None:
    if mode == "bare-fabricated-energy":
        summary = summary_document(candidate_identifier, input_sha, experiment, {}, [])
        summary["claimed_energies"] = {"total_energy_hartree": 0.0}
        write_json(output / "candidate-summary.json", summary)
        return
    payloads, problem, mapper, ansatz = construct(experiment)
    solve(experiment, payloads, problem, mapper, ansatz)
    mutate(mode, payloads)
    paths = {
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
    claims = []
    for role, path in paths.items():
        data = canonical_bytes(payloads[role])
        (output / path).write_bytes(data)
        claims.append(
            {
                "role": role,
                "path": path,
                "content_sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    summary = summary_document(
        candidate_identifier, input_sha, experiment, payloads, claims
    )
    if mode == "forged-content-hash":
        summary["artifacts"][0]["content_sha256"] = "f" * 64
    if mode == "forged-scientific-identity":
        summary["claimed_scientific_result_sha256"] = "f" * 64
    if mode == "claims-authorization":
        summary["authorized"] = True
    write_json(output / "candidate-summary.json", summary)


def summary_document(
    candidate_identifier: str,
    input_sha: str,
    experiment: dict[str, Any],
    payloads: dict[str, Any],
    claims: list[dict[str, Any]],
) -> dict[str, Any]:
    result = payloads.get("candidate_result", {})
    lineage = (
        [
            {"source_role": source, "destination_role": destination}
            for source, destination in (
                ("molecular_structure", "electronic_problem"),
                ("electronic_problem", "active_space"),
                ("active_space", "fermionic_hamiltonian"),
                ("fermionic_hamiltonian", "qubit_hamiltonian"),
                ("qubit_hamiltonian", "candidate_result"),
                ("optimization_trace", "candidate_result"),
            )
        ]
        if claims
        else []
    )
    return {
        "schema_version": "cgr.quantum-candidate-output/1.0.0",
        "candidate_identifier": candidate_identifier,
        "input_manifest_sha256": input_sha,
        "execution_completed": True,
        "claimed_workflow": "lih_statevector_vqe",
        "artifacts": claims,
        "lineage": lineage,
        "claimed_molecular_specification": experiment["molecular_system"],
        "claimed_active_space": experiment["electronic_structure"],
        "claimed_mapper": payloads.get("qubit_hamiltonian", {}).get(
            "mapper", experiment["quantum_model"]["mapper"]
        ),
        "claimed_solver": "statevector_vqe",
        "claimed_energies": {
            key: result.get(key)
            for key in (
                "electronic_energy_hartree",
                "nuclear_repulsion_energy_hartree",
                "total_energy_hartree",
            )
        },
        "claimed_converged": bool(result.get("converged", False)),
        "diagnostics": {},
    }


def write_json(path: Path, value: Any) -> None:
    path.write_bytes(canonical_bytes(value))


def main(mode: str) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    document = json.loads(raw)
    args.output.mkdir(parents=True, exist_ok=True)
    emit(
        "standalone-qiskit-candidate" if mode == "valid" else mode,
        "standalone-qiskit-candidate",
        hashlib.sha256(raw).hexdigest(),
        document["experiment"],
        args.output,
    )
