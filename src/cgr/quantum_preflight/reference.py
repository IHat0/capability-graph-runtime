"""Trusted PySCF/Qiskit Nature LiH reference execution boundary."""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.metadata
import io
import json
import math
import numbers
import time
from typing import Any

from cgr.science import canonical_json, sha256_fingerprint

from .adapters.qiskit_algorithms import deterministic_vqe, exact_eigensolver
from .contracts import QuantumChemistryExperiment
from .errors import QuantumDependencyError, QuantumExecutionError, QuantumIntegrityError
from .operators import (
    maximum_antihermitian_coefficient,
    serialize_fermionic_operator,
    serialize_qubit_operator,
)
from .results import EnergyResult, OptimizationEvaluation, VQEResult


@dataclasses.dataclass(frozen=True)
class PreparedProblem:
    """Authorized Hamiltonian shared by two solver functions, with no result values."""

    active_problem: Any
    mapper: Any
    fermionic_operator: Any
    qubit_operator: Any
    payloads: dict[str, Any]


CANONICAL_QPY_MAXIMUM_BYTES = 4 * 1024 * 1024
CANONICAL_BOUND_CIRCUIT_DECOMPOSITION_PASSES = 32
CANONICAL_BOUND_CIRCUIT_MAXIMUM_INSTRUCTIONS = 100_000
CANONICAL_BOUND_CIRCUIT_MAXIMUM_BYTES = 16 * 1024 * 1024
_SUPPORTED_BOUND_CIRCUIT_LEAF_CLASSES = frozenset(
    {
        "qiskit.circuit.barrier.Barrier",
        "qiskit.circuit.delay.Delay",
        "qiskit.circuit.measure.Measure",
        "qiskit.circuit.reset.Reset",
    }
)
_SUPPORTED_BOUND_CIRCUIT_LEAF_PREFIX = (
    "qiskit.circuit.library.standard_gates."
)


def canonical_qpy_bytes(circuit: Any) -> bytes:
    """Return the bounded pinned QPY serialization of an actual circuit."""
    try:
        from qiskit import qpy  # type: ignore[import-not-found]
    except (ImportError, ModuleNotFoundError) as exc:
        raise QuantumDependencyError("Qiskit QPY serialization is unavailable.") from exc
    stream = io.BytesIO()
    qpy.dump(circuit, stream)
    payload = stream.getvalue()
    if not payload or len(payload) > CANONICAL_QPY_MAXIMUM_BYTES:
        raise QuantumIntegrityError("Canonical circuit serialization is empty or oversized.")
    return payload


def canonical_qpy_sha256(circuit: Any) -> str:
    """Hash the pinned QPY serialization of an actual circuit object."""
    return hashlib.sha256(canonical_qpy_bytes(circuit)).hexdigest()


def _canonical_bound_numeric(value: Any, *, label: str) -> dict[str, str]:
    if isinstance(value, bool) or not isinstance(value, numbers.Complex):
        raise QuantumIntegrityError(
            f"Bound source circuit {label} has an unsupported numeric type."
        )
    try:
        numeric = complex(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise QuantumIntegrityError(
            f"Bound source circuit {label} is not a supported numeric value."
        ) from exc
    if not math.isfinite(numeric.real) or not math.isfinite(numeric.imag):
        raise QuantumIntegrityError(
            f"Bound source circuit {label} must be finite."
        )
    real = 0.0 if numeric.real == 0.0 else float(numeric.real)
    imaginary = 0.0 if numeric.imag == 0.0 else float(numeric.imag)
    if imaginary == 0.0:
        return {"type": "real", "hex": real.hex()}
    return {
        "type": "complex",
        "real_hex": real.hex(),
        "imaginary_hex": imaginary.hex(),
    }


def _bound_operation_class_identifier(operation: Any) -> str:
    operation_class = getattr(operation, "base_class", type(operation))
    module = getattr(operation_class, "__module__", None)
    qualname = getattr(operation_class, "__qualname__", None)
    if not isinstance(module, str) or not isinstance(qualname, str):
        raise QuantumIntegrityError(
            "Bound source circuit operation class identity is unavailable."
        )
    identifier = f"{module}.{qualname}"
    if not (
        identifier.startswith(_SUPPORTED_BOUND_CIRCUIT_LEAF_PREFIX)
        or identifier in _SUPPORTED_BOUND_CIRCUIT_LEAF_CLASSES
    ):
        raise QuantumIntegrityError(
            "Bound source circuit contains an unsupported primitive operation."
        )
    return identifier


def _bound_operation_payload(operation: Any) -> dict[str, Any]:
    name = getattr(operation, "name", None)
    if not isinstance(name, str) or not name:
        raise QuantumIntegrityError(
            "Bound source circuit operation name is unavailable."
        )
    condition = getattr(operation, "condition", None)
    if condition is not None:
        raise QuantumIntegrityError(
            "Bound source circuit classical conditions are unsupported."
        )
    try:
        number_of_qubits = int(operation.num_qubits)
        number_of_clbits = int(operation.num_clbits)
        parameters = list(operation.params)
    except (AttributeError, TypeError, ValueError) as exc:
        raise QuantumIntegrityError(
            "Bound source circuit operation shape is unsupported."
        ) from exc
    payload: dict[str, Any] = {
        "name": name,
        "base_class": _bound_operation_class_identifier(operation),
        "number_of_qubits": number_of_qubits,
        "number_of_clbits": number_of_clbits,
        "parameters": [
            _canonical_bound_numeric(value, label="operation parameter")
            for value in parameters
        ],
    }
    modifiers: dict[str, Any] = {}
    control_count = getattr(operation, "num_ctrl_qubits", None)
    control_state = getattr(operation, "ctrl_state", None)
    if control_count is not None or control_state is not None:
        if (
            isinstance(control_count, bool)
            or isinstance(control_state, bool)
            or not isinstance(control_count, numbers.Integral)
            or not isinstance(control_state, numbers.Integral)
            or int(control_count) < 1
            or int(control_state) < 0
        ):
            raise QuantumIntegrityError(
                "Bound source circuit control modifiers are invalid."
            )
        modifiers["number_of_control_qubits"] = int(control_count)
        modifiers["control_state"] = int(control_state)
        base_gate = getattr(operation, "base_gate", None)
        if base_gate is None:
            raise QuantumIntegrityError(
                "Bound source circuit controlled operation lacks its base gate."
            )
        base_name = getattr(base_gate, "name", None)
        if not isinstance(base_name, str) or not base_name:
            raise QuantumIntegrityError(
                "Bound source circuit controlled base-gate name is unavailable."
            )
        base_class = getattr(base_gate, "base_class", type(base_gate))
        base_module = getattr(base_class, "__module__", None)
        base_qualname = getattr(base_class, "__qualname__", None)
        if not isinstance(base_module, str) or not isinstance(base_qualname, str):
            raise QuantumIntegrityError(
                "Bound source circuit controlled base-gate identity is unavailable."
            )
        modifiers["base_gate"] = {
            "name": base_name,
            "base_class": f"{base_module}.{base_qualname}",
        }
    unit = getattr(operation, "unit", None)
    if unit is not None:
        if not isinstance(unit, str) or not unit or len(unit) > 32:
            raise QuantumIntegrityError(
                "Bound source circuit operation unit is unsupported."
            )
        modifiers["unit"] = unit
    if modifiers:
        payload["modifiers"] = modifiers
    return payload


def _bounded_bound_circuit_decomposition(circuit: Any) -> Any:
    try:
        from qiskit.circuit import QuantumCircuit  # type: ignore[import-not-found]
    except (ImportError, ModuleNotFoundError) as exc:
        raise QuantumDependencyError("Qiskit circuit APIs are unavailable.") from exc
    if not isinstance(circuit, QuantumCircuit):
        raise QuantumIntegrityError(
            "Bound source circuit identity requires a Qiskit QuantumCircuit."
        )
    if circuit.parameters:
        raise QuantumIntegrityError(
            "Bound source circuit contains unbound parameters."
        )
    for attribute in (
        "num_input_vars",
        "num_captured_vars",
        "num_declared_vars",
        "num_stretches",
    ):
        value = getattr(circuit, attribute, 0)
        if value:
            raise QuantumIntegrityError(
                "Bound source circuit real-time variables are unsupported."
            )
    calibrations = getattr(circuit, "calibrations", None)
    if calibrations:
        raise QuantumIntegrityError(
            "Bound source circuit calibrations are unsupported."
        )
    decomposed = circuit
    for decomposition_pass in range(
        CANONICAL_BOUND_CIRCUIT_DECOMPOSITION_PASSES + 1
    ):
        instructions = tuple(decomposed.data)
        if len(instructions) > CANONICAL_BOUND_CIRCUIT_MAXIMUM_INSTRUCTIONS:
            raise QuantumIntegrityError(
                "Bound source circuit exceeds the instruction limit."
            )
        try:
            fully_decomposed = all(
                item.operation.definition is None for item in instructions
            )
        except Exception as exc:
            raise QuantumIntegrityError(
                "Bound source circuit operation definitions are unavailable."
            ) from exc
        if fully_decomposed:
            if decomposed.parameters:
                raise QuantumIntegrityError(
                    "Decomposed source circuit contains unbound parameters."
                )
            return decomposed
        if decomposition_pass == CANONICAL_BOUND_CIRCUIT_DECOMPOSITION_PASSES:
            break
        try:
            decomposed = decomposed.decompose(reps=1)
        except Exception as exc:
            raise QuantumIntegrityError(
                "Bound source circuit decomposition failed."
            ) from exc
    raise QuantumIntegrityError(
        "Bound source circuit exceeded the decomposition-pass limit."
    )


def canonical_bound_circuit_payload(circuit: Any) -> dict[str, Any]:
    """Return a bounded semantic payload for a fully bound source circuit."""
    decomposed = _bounded_bound_circuit_decomposition(circuit)
    try:
        number_of_qubits = int(decomposed.num_qubits)
        number_of_clbits = int(decomposed.num_clbits)
    except (AttributeError, TypeError, ValueError) as exc:
        raise QuantumIntegrityError(
            "Bound source circuit bit counts are unavailable."
        ) from exc
    instructions: list[dict[str, Any]] = []
    for item in decomposed.data:
        try:
            qubits = [int(decomposed.find_bit(bit).index) for bit in item.qubits]
            clbits = [int(decomposed.find_bit(bit).index) for bit in item.clbits]
        except (AttributeError, IndexError, TypeError, ValueError) as exc:
            raise QuantumIntegrityError(
                "Bound source circuit bit ordering is invalid."
            ) from exc
        if any(index < 0 or index >= number_of_qubits for index in qubits):
            raise QuantumIntegrityError(
                "Bound source circuit qubit index is out of range."
            )
        if any(index < 0 or index >= number_of_clbits for index in clbits):
            raise QuantumIntegrityError(
                "Bound source circuit classical-bit index is out of range."
            )
        operation = _bound_operation_payload(item.operation)
        if operation["number_of_qubits"] != len(qubits):
            raise QuantumIntegrityError(
                "Bound source circuit operation qubit arity is inconsistent."
            )
        if operation["number_of_clbits"] != len(clbits):
            raise QuantumIntegrityError(
                "Bound source circuit operation classical-bit arity is inconsistent."
            )
        instructions.append(
            {
                "operation": operation,
                "qubits": qubits,
                "clbits": clbits,
            }
        )
    payload = {
        "schema_version": "cgr.bound-source-circuit/1.0.0",
        "number_of_qubits": number_of_qubits,
        "number_of_clbits": number_of_clbits,
        "global_phase": _canonical_bound_numeric(
            decomposed.global_phase,
            label="global phase",
        ),
        "instructions": instructions,
    }
    if len(canonical_json(payload).encode("utf-8")) > (
        CANONICAL_BOUND_CIRCUIT_MAXIMUM_BYTES
    ):
        raise QuantumIntegrityError(
            "Bound source circuit canonical payload exceeds the byte limit."
        )
    return payload


def canonical_bound_circuit_sha256(circuit: Any) -> str:
    """Hash a deterministic semantic identity for a fully bound source circuit."""
    return sha256_fingerprint(canonical_bound_circuit_payload(circuit))


def canonical_sparse_pauli_op_payload(operator: Any, *, mapper: str) -> dict[str, Any]:
    """Return the canonical SparsePauliOp coefficient and Pauli serialization."""
    return serialize_qubit_operator(
        operator.to_list(),
        number_of_qubits=int(operator.num_qubits),
        mapper=mapper,
    )


def canonical_sparse_pauli_op_sha256(operator: Any, *, mapper: str) -> str:
    """Hash the canonical SparsePauliOp coefficient and Pauli serialization."""
    return sha256_fingerprint(canonical_sparse_pauli_op_payload(operator, mapper=mapper))


def _qiskit_api() -> dict[str, Any]:
    try:
        from qiskit.primitives import StatevectorEstimator  # type: ignore[import-not-found]
        from qiskit_nature.second_q.algorithms import GroundStateEigensolver  # type: ignore[import-not-found]
        from qiskit_nature.second_q.circuit.library import (  # type: ignore[import-not-found]
            HartreeFock,
            UCCSD,
        )
        from qiskit_nature.second_q.drivers import (  # type: ignore[import-not-found]
            MethodType,
            PySCFDriver,
        )
        from qiskit_nature.second_q.formats.molecule_info import DistanceUnit  # type: ignore[import-not-found]
        from qiskit_nature.second_q.mappers import JordanWignerMapper  # type: ignore[import-not-found]
        from qiskit_nature.second_q.transformers import ActiveSpaceTransformer  # type: ignore[import-not-found]
    except (ImportError, ModuleNotFoundError) as exc:
        raise QuantumDependencyError(
            "Qiskit Nature/PySCF APIs are unavailable in the dedicated runtime."
        ) from exc
    return {
        "StatevectorEstimator": StatevectorEstimator,
        "GroundStateEigensolver": GroundStateEigensolver,
        "HartreeFock": HartreeFock,
        "UCCSD": UCCSD,
        "MethodType": MethodType,
        "PySCFDriver": PySCFDriver,
        "DistanceUnit": DistanceUnit,
        "JordanWignerMapper": JordanWignerMapper,
        "ActiveSpaceTransformer": ActiveSpaceTransformer,
    }


def prepare_problem(experiment: QuantumChemistryExperiment) -> PreparedProblem:
    """Construct and validate the exact declared electronic-structure problem."""
    api = _qiskit_api()
    molecule = experiment.molecular_system
    model = experiment.electronic_structure
    if model.driver_identifier != "pyscf" or experiment.quantum_model.mapper != "jordan_wigner":
        raise QuantumExecutionError("Manifest requests an unsupported driver or mapper.")
    atom = "; ".join(
        f"{item.element} {item.coordinates[0]} {item.coordinates[1]} {item.coordinates[2]}"
        for item in molecule.atoms
    )
    unit = (
        api["DistanceUnit"].ANGSTROM
        if molecule.coordinate_unit == "angstrom"
        else api["DistanceUnit"].BOHR
    )
    driver = api["PySCFDriver"](
        atom=atom,
        unit=unit,
        charge=molecule.molecular_charge,
        spin=molecule.driver_spin,
        basis=model.basis_set,
        method=api["MethodType"].RHF,
    )
    try:
        problem = driver.run()
        qcschema_object = driver.to_qcschema(include_dipole=True)
    except Exception as exc:
        raise QuantumExecutionError(f"PySCF driver failed: {exc}") from exc
    if problem.orbital_occupations is None or problem.orbital_occupations_b is None:
        raise QuantumIntegrityError("Driver did not expose alpha/beta orbital occupations.")
    occupations_alpha = [float(value) for value in problem.orbital_occupations]
    occupations_beta = [float(value) for value in problem.orbital_occupations_b]
    occupations = [
        alpha + beta for alpha, beta in zip(occupations_alpha, occupations_beta)
    ]
    indices = list(model.active_orbital_indices)
    if any(index >= int(problem.num_spatial_orbitals) for index in indices):
        raise QuantumIntegrityError("Declared active-orbital index is out of range.")
    resolved_electrons = int(round(sum(occupations[index] for index in indices)))
    if resolved_electrons != model.active_electron_count:
        raise QuantumIntegrityError(
            "Declared active orbitals resolve to a different electron count."
        )
    resolved = resolve_active_orbitals(
        total_electrons=molecule.total_electron_count,
        active_electrons=model.active_electron_count,
        active_orbitals=model.active_spatial_orbital_count,
        total_orbitals=int(problem.num_spatial_orbitals),
    )
    if resolved != indices:
        raise QuantumIntegrityError(
            "Qiskit Nature's resolved default active space differs from the manifest."
        )
    transformer = api["ActiveSpaceTransformer"](
        num_electrons=model.active_electron_count,
        num_spatial_orbitals=model.active_spatial_orbital_count,
    )
    active_problem = transformer.transform(problem)
    fermionic = active_problem.hamiltonian.second_q_op()
    mapper = api["JordanWignerMapper"]()
    qubit = mapper.map(fermionic)
    fermionic_payload = serialize_fermionic_operator(
        fermionic.items(), register_length=int(fermionic.register_length)
    )
    qubit_payload = serialize_qubit_operator(
        qubit.to_list(),
        number_of_qubits=int(qubit.num_qubits),
        mapper=experiment.quantum_model.mapper,
    )
    qcschema_payload = _json_value(qcschema_object)
    payloads = {
        "molecular_structure": {
            **molecule.model_dump(mode="json"),
            "driver_spin": molecule.driver_spin,
            "total_electron_count": molecule.total_electron_count,
        },
        "qcschema": qcschema_payload,
        "electronic_problem": {
            "driver_identifier": model.driver_identifier,
            "basis_set": model.basis_set,
            "reference_method": model.reference_method,
            "frozen_core_policy": model.frozen_core,
            "pre_transform_particle_count": _json_value(problem.num_particles),
            "pre_transform_spatial_orbitals": int(problem.num_spatial_orbitals),
            "pre_transform_spin_orbitals": int(problem.num_spin_orbitals),
            "orbital_occupations_alpha": occupations_alpha,
            "orbital_occupations_beta": occupations_beta,
            "orbital_occupations_total": occupations,
            "nuclear_repulsion_energy_hartree": float(
                problem.hamiltonian.nuclear_repulsion_energy
            ),
        },
        "active_space": {
            "active_electron_count": model.active_electron_count,
            "active_spatial_orbital_count": int(active_problem.num_spatial_orbitals),
            "active_spin_orbital_count": int(active_problem.num_spin_orbitals),
            "declared_active_orbital_indices": indices,
            "resolved_active_orbital_indices": resolved,
            "active_particle_count": _json_value(active_problem.num_particles),
            "orbital_occupations_used": [occupations[index] for index in indices],
            "resolution_policy": "validated_qiskit_nature_default_for_0.8.0",
        },
        "fermionic_hamiltonian": fermionic_payload,
        "qubit_hamiltonian": qubit_payload,
        "hamiltonian_metrics": {
            "maximum_antihermitian_coefficient": maximum_antihermitian_coefficient(qubit),
            "qubit_sha256": None,
        },
    }
    return PreparedProblem(active_problem, mapper, fermionic, qubit, payloads)


def resolve_active_orbitals(
    *,
    total_electrons: int,
    active_electrons: int,
    active_orbitals: int,
    total_orbitals: int,
) -> list[int]:
    """Mirror and record Qiskit Nature's closed-shell default active-space rule.

    Qiskit Nature 0.8.0 has a defect in explicit-list validation (integer is
    compared to list rather than list length). We therefore derive the exact
    indices its default policy must select, compare them to the manifest, and
    only then invoke the transformer without its broken optional argument.
    """
    inactive_electrons = total_electrons - active_electrons
    if inactive_electrons < 0 or inactive_electrons % 2:
        raise QuantumIntegrityError("Closed-shell inactive electron count is invalid.")
    first = inactive_electrons // 2
    resolved = list(range(first, first + active_orbitals))
    if not resolved or resolved[-1] >= total_orbitals:
        raise QuantumIntegrityError("Resolved active-orbital index is out of range.")
    return resolved


def run_exact(
    prepared: PreparedProblem,
    *,
    hamiltonian_sha256: str,
    environment_sha256: str,
) -> EnergyResult:
    """Run filtered exact diagonalization with no access to any VQE result."""
    api = _qiskit_api()
    solver = exact_eigensolver(
        filter_criterion=prepared.active_problem.get_default_filter_criterion()
    )
    ground_state = api["GroundStateEigensolver"](prepared.mapper, solver)
    started = time.perf_counter()
    try:
        interpreted = ground_state.solve(prepared.active_problem)
    except Exception as exc:
        raise QuantumExecutionError(f"Exact eigensolver failed: {exc}") from exc
    duration = time.perf_counter() - started
    return _energy_result(
        interpreted,
        result_type=EnergyResult,
        solver_identifier="numpy_minimum_eigensolver",
        solver_version=importlib.metadata.version("qiskit-algorithms"),
        hamiltonian_sha256=hamiltonian_sha256,
        environment_sha256=environment_sha256,
        duration_seconds=duration,
        number_of_spatial_orbitals=int(prepared.active_problem.num_spatial_orbitals),
        number_of_spin_orbitals=int(prepared.active_problem.num_spin_orbitals),
        number_of_qubits=int(prepared.qubit_operator.num_qubits),
        particle_sector_filter_applied=True,
    )


def run_vqe(
    prepared: PreparedProblem,
    experiment: QuantumChemistryExperiment,
    *,
    hamiltonian_sha256: str,
    environment_sha256: str,
    capture_ibm_preflight: bool = False,
) -> tuple[VQEResult, list[dict[str, Any]], dict[str, Any], dict[str, Any] | None]:
    """Run VQE independently; this function cannot receive an exact energy."""
    api = _qiskit_api()
    problem = prepared.active_problem
    quantum = experiment.quantum_model
    initial_state = api["HartreeFock"](
        problem.num_spatial_orbitals, problem.num_particles, prepared.mapper
    )
    ansatz = api["UCCSD"](
        problem.num_spatial_orbitals,
        problem.num_particles,
        prepared.mapper,
        initial_state=initial_state,
    )
    initial_point = [0.0] * int(ansatz.num_parameters)
    trace: list[OptimizationEvaluation] = []

    def callback(evaluation: int, parameters: Any, mean: Any, metadata: Any) -> None:
        del metadata
        trace.append(
            OptimizationEvaluation(
                evaluation=int(evaluation),
                energy_hartree=float(complex(mean).real),
                parameter_fingerprint=sha256_fingerprint([float(value) for value in parameters]),
            )
        )

    minimum = deterministic_vqe(
        estimator=api["StatevectorEstimator"](seed=quantum.random_seed),
        ansatz=ansatz,
        maximum_iterations=quantum.maximum_iterations,
        convergence_threshold=quantum.convergence_threshold,
        initial_point=initial_point,
        random_seed=quantum.random_seed,
        callback=callback,
    )
    ground_state = api["GroundStateEigensolver"](prepared.mapper, minimum)
    started = time.perf_counter()
    try:
        interpreted = ground_state.solve(problem)
    except Exception as exc:
        raise QuantumExecutionError(f"Statevector VQE failed: {exc}") from exc
    duration = time.perf_counter() - started
    raw = interpreted.raw_result
    point = [float(value) for value in getattr(raw, "optimal_point", ())]
    evaluations = int(getattr(raw, "cost_function_evals", len(trace)))
    status = getattr(getattr(raw, "optimizer_result", None), "status", None)
    vqe = _energy_result(
        interpreted,
        result_type=VQEResult,
        solver_identifier="statevector_vqe",
        solver_version=importlib.metadata.version("qiskit-algorithms"),
        hamiltonian_sha256=hamiltonian_sha256,
        environment_sha256=environment_sha256,
        duration_seconds=duration,
        number_of_spatial_orbitals=int(problem.num_spatial_orbitals),
        number_of_spin_orbitals=int(problem.num_spin_orbitals),
        number_of_qubits=int(prepared.qubit_operator.num_qubits),
        optimizer_identifier=quantum.optimizer,
        optimizer_status="completed" if status is None else f"status_{status}",
        optimizer_evaluations=max(evaluations, len(trace), 1),
        initial_point_sha256=sha256_fingerprint(initial_point),
        optimized_parameters_sha256=sha256_fingerprint(point),
        ansatz_identifier=quantum.ansatz,
        initial_state_identifier=quantum.initial_state,
        converged=bool(point) and math.isfinite(float(interpreted.total_energies[0].real)),
    )
    ansatz_manifest = {
        "schema_version": "cgr.ansatz-manifest/1.0.0",
        "ansatz": quantum.ansatz,
        "number_of_qubits": int(ansatz.num_qubits),
        "number_of_parameters": int(ansatz.num_parameters),
        "initial_state": quantum.initial_state,
        "mapper": quantum.mapper,
        "active_space_sha256": sha256_fingerprint(prepared.payloads["active_space"]),
        "hamiltonian_sha256": hamiltonian_sha256,
        "initial_point_sha256": vqe.initial_point_sha256,
        "optimized_parameters_sha256": vqe.optimized_parameters_sha256,
        "circuit_depth": int(ansatz.decompose().depth()),
        "operation_counts": dict(ansatz.decompose().count_ops()),
        "qiskit_version": importlib.metadata.version("qiskit"),
    }
    ibm_preflight: dict[str, Any] | None = None
    if capture_ibm_preflight:
        bound_ansatz = ansatz.assign_parameters(point, inplace=False)
        ibm_preflight = {
            "schema_version": "cgr.pulsate-ibm-local-preflight/1.0.0",
            "optimized_parameters": point,
            "optimized_parameters_sha256": vqe.optimized_parameters_sha256,
            "source_bound_circuit_sha256": canonical_bound_circuit_sha256(
                bound_ansatz
            ),
            "source_observable_sha256": canonical_sparse_pauli_op_sha256(
                prepared.qubit_operator,
                mapper=quantum.mapper,
            ),
            "number_of_qubits": int(ansatz.num_qubits),
            "number_of_parameters": int(ansatz.num_parameters),
            "circuit_depth": int(ansatz.decompose().depth()),
        }
    return (
        vqe,
        [item.model_dump(mode="json") for item in trace],
        ansatz_manifest,
        ibm_preflight,
    )


def _energy_result(
    interpreted: Any,
    *,
    result_type: type[EnergyResult],
    solver_identifier: str,
    solver_version: str,
    hamiltonian_sha256: str,
    environment_sha256: str,
    duration_seconds: float,
    **extra: Any,
) -> Any:
    electronic = float(complex(interpreted.electronic_energies[0]).real)
    nuclear = float(interpreted.nuclear_repulsion_energy)
    total = float(complex(interpreted.total_energies[0]).real)
    raw_value = float(complex(interpreted.computed_energies[0]).real)
    particle = _first_numeric(getattr(interpreted, "num_particles", None))
    return result_type(
        solver_identifier=solver_identifier,
        solver_version=solver_version,
        hamiltonian_sha256=hamiltonian_sha256,
        environment_sha256=environment_sha256,
        electronic_energy_hartree=electronic,
        nuclear_repulsion_energy_hartree=nuclear,
        total_energy_hartree=total,
        raw_eigenvalue_hartree=raw_value,
        particle_count=particle,
        completed=True,
        duration_seconds=duration_seconds,
        **extra,
    )


def _first_numeric(value: Any) -> float | None:
    if value is None:
        return None
    while isinstance(value, (list, tuple)) and value:
        value = value[0]
    try:
        numeric = float(complex(value).real)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _json_value(value: Any) -> Any:
    """Convert Qiskit Nature dataclasses/models to stable JSON data."""
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    elif hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)  # type: ignore[arg-type]
    if isinstance(value, complex):
        return {"real": value.real, "imag": value.imag}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "tolist"):
        return _json_value(value.tolist())
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return str(value)
    return value
