"""Atomic orchestration of the trusted reference workflow and evidence receipt."""

from __future__ import annotations

import os
import math
import signal
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from cgr.science import (
    ArtifactLineageEdge,
    ArtifactLineageGraph,
    ArtifactReference,
    sha256_fingerprint,
)

from .artifacts import SCHEMA_VERSION, artifact_document, artifact_reference, write_json_atomic
from .contracts import ManifestEnvelope
from .environment import environment_manifest
from .errors import (
    QuantumIntegrityError,
    QuantumPreflightError,
    QuantumTimeoutError,
    QuantumVerificationError,
)
from .receipt import assemble_receipt
from .reference import prepare_problem, run_exact, run_vqe
from .identities import (
    AuthorizedScientificOutcome,
    ScientificResultArtifact,
    ScientificResultIdentity,
    verifier_outcome_identities,
)
from .verification import blocking_findings, verify_execution
from .warnings import CapturedWarning, capture_warnings, warning_evidence
from .results import EnergyResult, VQEResult

_FILENAMES = {
    "experiment": "experiment.json",
    "molecular_structure": "molecular-structure.json",
    "environment": "environment.json",
    "qcschema": "qcschema.json",
    "electronic_problem": "electronic-problem.json",
    "active_space": "active-space.json",
    "fermionic_hamiltonian": "fermionic-hamiltonian.json",
    "qubit_hamiltonian": "qubit-hamiltonian.json",
    "exact_result": "exact-result.json",
    "vqe_result": "vqe-result.json",
    "optimization_trace": "optimization-trace.json",
    "ansatz_manifest": "ansatz-manifest.json",
    "compatibility_warnings": "compatibility-warnings.json",
    "verification_report": "verification-report.json",
    "lineage": "lineage.json",
    "receipt": "receipt.json",
}
_ARTIFACT_TYPES = {
    "experiment": "quantum_chemistry_experiment",
    "molecular_structure": "molecular_structure",
    "environment": "environment_manifest",
    "qcschema": "qcschema",
    "electronic_problem": "electronic_structure_problem_summary",
    "active_space": "active_space",
    "fermionic_hamiltonian": "fermionic_hamiltonian",
    "qubit_hamiltonian": "qubit_hamiltonian",
    "exact_result": "exact_ground_state_result",
    "vqe_result": "vqe_ground_state_result",
    "optimization_trace": "optimization_trace",
    "ansatz_manifest": "circuit_ansatz_manifest",
    "compatibility_warnings": "compatibility_warnings",
    "verification_report": "verification_report",
    "lineage": "artifact_lineage",
    "receipt": "quantum_preflight_receipt",
}


def run_trusted_reference(
    manifest: ManifestEnvelope,
    *,
    result_root: Path,
    lock_path: Path,
    image_identifier: str,
    maximum_seconds: int | None = None,
    ibm_preflight_evidence_path: Path | None = None,
) -> dict[str, Any]:
    """Execute trusted LiH and atomically commit one immutable evidence directory."""
    policy = manifest.experiment.execution_policy
    timeout = maximum_seconds or policy.maximum_duration_seconds
    if timeout <= 0 or timeout > policy.maximum_duration_seconds:
        raise ValueError("Runtime timeout must be positive and no larger than the manifest policy.")
    run_id, final_path = _next_run_path(result_root, manifest.experiment.experiment_identifier)
    temporary = final_path.with_name(f".{run_id}.{uuid.uuid4().hex}.tmp")
    temporary.mkdir(parents=True)
    (temporary / "logs").mkdir()
    maximum_bytes = policy.maximum_result_bytes
    try:
        with _wall_clock_limit(timeout):
            summary = _execute(
                manifest,
                temporary,
                execution_identifier=run_id,
                lock_path=lock_path,
                image_identifier=image_identifier,
                maximum_bytes=maximum_bytes,
                ibm_preflight_evidence_path=ibm_preflight_evidence_path,
            )
        os.replace(temporary, final_path)
        return {**summary, "run_id": run_id, "receipt_path": str(final_path / "receipt.json")}
    except Exception as exc:
        failure = {
            "authorized": False,
            "execution_completed": False,
            "failure_type": type(exc).__name__,
            "exit_code": exc.exit_code if isinstance(exc, QuantumPreflightError) else 3,
        }
        if temporary.exists():
            write_json_atomic(
                temporary / "failure-summary.json",
                failure,
                maximum_bytes=maximum_bytes,
            )
        failed = final_path.with_name(f"{run_id}-failed")
        if temporary.exists():
            os.replace(temporary, failed)
        raise


def _execute(
    manifest: ManifestEnvelope,
    directory: Path,
    *,
    execution_identifier: str,
    lock_path: Path,
    image_identifier: str,
    maximum_bytes: int,
    ibm_preflight_evidence_path: Path | None,
) -> dict[str, Any]:
    experiment = manifest.experiment
    captured_warnings: list[CapturedWarning] = []
    payloads: dict[str, Any] = {
        "experiment": experiment.model_dump(mode="json"),
        "environment": environment_manifest(lock_path, image_identifier=image_identifier),
    }
    references: dict[str, ArtifactReference] = {}
    references["experiment"] = _record(directory, "experiment", payloads["experiment"], maximum_bytes=maximum_bytes)
    references["environment"] = _record(
        directory,
        "environment",
        payloads["environment"],
        parents=(references["experiment"].pointer,),
        maximum_bytes=maximum_bytes,
    )
    with capture_warnings("problem_construction") as phase_warnings:
        prepared = prepare_problem(experiment)
    captured_warnings.extend(phase_warnings)
    payloads.update(prepared.payloads)
    construction_order = (
        ("molecular_structure", (references["experiment"].pointer,)),
        ("qcschema", ()),
        ("electronic_problem", ()),
        ("active_space", ()),
        ("fermionic_hamiltonian", ()),
        ("qubit_hamiltonian", ()),
    )
    previous = references["experiment"].pointer
    for name, explicit_parents in construction_order:
        parents = explicit_parents or (previous,)
        references[name] = _record(
            directory, name, payloads[name], parents=parents, maximum_bytes=maximum_bytes
        )
        previous = references[name].pointer
    payloads["hamiltonian_metrics"]["qubit_sha256"] = references[
        "qubit_hamiltonian"
    ].content_sha256

    # VQE executes before the exact solver exists. The function signature has
    # no reference-energy argument, enforcing optimizer/reference separation.
    with capture_warnings("vqe_execution") as phase_warnings:
        vqe, trace, ansatz, ibm_preflight = run_vqe(
            prepared,
            experiment,
            hamiltonian_sha256=references["qubit_hamiltonian"].content_sha256,
            environment_sha256=references["environment"].content_sha256,
            capture_ibm_preflight=ibm_preflight_evidence_path is not None,
        )
    captured_warnings.extend(phase_warnings)
    payloads["optimization_trace"] = trace
    references["optimization_trace"] = _record(
        directory,
        "optimization_trace",
        trace,
        parents=(references["qubit_hamiltonian"].pointer,),
        maximum_bytes=maximum_bytes,
    )
    payloads["ansatz_manifest"] = ansatz
    references["ansatz_manifest"] = _record(
        directory,
        "ansatz_manifest",
        ansatz,
        parents=(references["active_space"].pointer, references["qubit_hamiltonian"].pointer),
        maximum_bytes=maximum_bytes,
    )
    vqe_identity = _scientific_result_identity(
        "vqe_ground_state", vqe, experiment, payloads, references
    )
    vqe_artifact = ScientificResultArtifact(
        scientific_identity=vqe_identity,
        scientific_result_sha256=vqe_identity.fingerprint,
        execution_result=vqe,
        execution_metadata={"execution_identifier": execution_identifier},
    )
    payloads["vqe_result"] = vqe_artifact.model_dump(mode="json")
    references["vqe_result"] = _record(
        directory,
        "vqe_result",
        payloads["vqe_result"],
        parents=(
            references["qubit_hamiltonian"].pointer,
            references["optimization_trace"].pointer,
            references["environment"].pointer,
        ),
        maximum_bytes=maximum_bytes,
    )
    with capture_warnings("exact_execution") as phase_warnings:
        exact = run_exact(
            prepared,
            hamiltonian_sha256=references["qubit_hamiltonian"].content_sha256,
            environment_sha256=references["environment"].content_sha256,
        )
    captured_warnings.extend(phase_warnings)
    exact_identity = _scientific_result_identity(
        "exact_ground_state", exact, experiment, payloads, references
    )
    exact_artifact = ScientificResultArtifact(
        scientific_identity=exact_identity,
        scientific_result_sha256=exact_identity.fingerprint,
        execution_result=exact,
        execution_metadata={"execution_identifier": execution_identifier},
    )
    payloads["exact_result"] = exact_artifact.model_dump(mode="json")
    references["exact_result"] = _record(
        directory,
        "exact_result",
        payloads["exact_result"],
        parents=(references["qubit_hamiltonian"].pointer, references["environment"].pointer),
        maximum_bytes=maximum_bytes,
    )
    compatibility = warning_evidence(captured_warnings)
    payloads["compatibility_warnings"] = compatibility.model_dump(mode="json")
    references["compatibility_warnings"] = _record(
        directory,
        "compatibility_warnings",
        payloads["compatibility_warnings"],
        parents=(references["environment"].pointer,),
        maximum_bytes=maximum_bytes,
    )
    lineage = _lineage(references)
    results = verify_execution(experiment, references, payloads, lineage)
    payloads["verification_report"] = {
        "schema_version": "cgr.quantum-verification-report/1.0.0",
        "results": [result.model_dump(mode="json") for result in results],
        "numerical_agreement": payloads["numerical_agreement"],
        "hamiltonian_metrics": payloads["hamiltonian_metrics"],
        "active_space": payloads["active_space"],
    }
    references["verification_report"] = _record(
        directory,
        "verification_report",
        payloads["verification_report"],
        parents=tuple(reference.pointer for reference in references.values()),
        maximum_bytes=maximum_bytes,
    )
    lineage = lineage.add(_edge(references["exact_result"], references["verification_report"], "verified_by"))
    lineage = lineage.add(_edge(references["vqe_result"], references["verification_report"], "verified_by"))
    payloads["lineage"] = lineage.model_dump(mode="json")
    references["lineage"] = _record(
        directory,
        "lineage",
        payloads["lineage"],
        parents=tuple(reference.pointer for reference in references.values()),
        maximum_bytes=maximum_bytes,
    )
    agreement = payloads["numerical_agreement"]
    outcome = AuthorizedScientificOutcome(
        experiment_sha256=experiment.fingerprint,
        molecular_structure_sha256=references["molecular_structure"].content_sha256,
        electronic_problem_sha256=references["electronic_problem"].content_sha256,
        active_space_sha256=references["active_space"].content_sha256,
        fermionic_hamiltonian_sha256=references["fermionic_hamiltonian"].content_sha256,
        qubit_hamiltonian_sha256=references["qubit_hamiltonian"].content_sha256,
        exact_scientific_result_sha256=exact_identity.fingerprint,
        vqe_scientific_result_sha256=vqe_identity.fingerprint,
        exact_total_energy=exact.total_energy_hartree,
        vqe_total_energy=vqe.total_energy_hartree,
        absolute_difference=agreement["absolute_difference_hartree"],
        tolerance=agreement["tolerance_hartree"],
        comparison_passed=agreement["passed"],
        verification_policy_sha256=experiment.verification_policy.fingerprint,
        verifier_outcomes=verifier_outcome_identities(results),
        authorization_decision=not blocking_findings(results),
        environment_compatibility_sha256=payloads["environment"][
            "environment_compatibility_sha256"
        ],
        compatibility_warnings_sha256=compatibility.fingerprint,
        compatibility_status=compatibility.status,
    )
    receipt = assemble_receipt(
        execution_identifier=execution_identifier,
        experiment=references["experiment"].pointer,
        artifacts=tuple(reference.pointer for reference in references.values()),
        verification_results=results,
        lineage=references["lineage"].pointer,
        compatibility_warnings=references["compatibility_warnings"].pointer,
        scientific_outcome=outcome,
        execution_completed=True,
    )
    payloads["receipt"] = receipt.model_dump(mode="json")
    references["receipt"] = _record(
        directory,
        "receipt",
        payloads["receipt"],
        parents=(references["verification_report"].pointer, references["lineage"].pointer),
        maximum_bytes=maximum_bytes,
    )
    write_json_atomic(
        directory / "manifest.json",
        manifest.model_dump(mode="json"),
        maximum_bytes=maximum_bytes,
    )
    summary = {
        "experiment_identifier": experiment.experiment_identifier,
        "experiment_sha256": references["experiment"].content_sha256,
        "experiment_fingerprint": experiment.fingerprint,
        "structure_sha256": references["molecular_structure"].content_sha256,
        "qcschema_sha256": references["qcschema"].content_sha256,
        "electronic_problem_sha256": references["electronic_problem"].content_sha256,
        "active_space_sha256": references["active_space"].content_sha256,
        "fermionic_hamiltonian_sha256": references["fermionic_hamiltonian"].content_sha256,
        "qubit_hamiltonian_sha256": references["qubit_hamiltonian"].content_sha256,
        "exact_result_sha256": references["exact_result"].content_sha256,
        "vqe_result_sha256": references["vqe_result"].content_sha256,
        "exact_scientific_result_sha256": exact_identity.fingerprint,
        "vqe_scientific_result_sha256": vqe_identity.fingerprint,
        "scientific_outcome_sha256": outcome.fingerprint,
        "environment_compatibility_sha256": payloads["environment"][
            "environment_compatibility_sha256"
        ],
        "network_disabled": payloads["environment"]["network_disabled"],
        "compatibility_warnings_sha256": compatibility.fingerprint,
        "compatibility_status": compatibility.status,
        "initial_point_sha256": vqe.initial_point_sha256,
        "optimized_parameters_sha256": vqe.optimized_parameters_sha256,
        "optimization_trace_scientific_sha256": references[
            "optimization_trace"
        ].content_sha256,
        "receipt_sha256": references["receipt"].content_sha256,
        "exact_total_energy_hartree": exact.total_energy_hartree,
        "vqe_total_energy_hartree": vqe.total_energy_hartree,
        "absolute_difference_hartree": payloads["numerical_agreement"]["absolute_difference_hartree"],
        "tolerance_hartree": experiment.verification_policy.energy_difference_tolerance_hartree,
        "execution_completed": True,
        "scientific_verification_passed": not blocking_findings(results),
        "authorized": receipt.authorized,
    }
    write_json_atomic(directory / "summary.json", summary, maximum_bytes=maximum_bytes)
    if not receipt.authorized:
        raise QuantumVerificationError("Trusted execution completed but was not authorized.")
    if ibm_preflight_evidence_path is not None:
        if ibm_preflight is None:
            raise QuantumIntegrityError("IBM preflight material was not captured.")
        nuclear = payloads["electronic_problem"].get(
            "nuclear_repulsion_energy_hartree"
        )
        if not isinstance(nuclear, (int, float)) or not math.isfinite(float(nuclear)):
            raise QuantumIntegrityError("IBM preflight nuclear evidence is unavailable.")
        write_json_atomic(
            ibm_preflight_evidence_path,
            {
                **ibm_preflight,
                "ansatz_sha256": references["ansatz_manifest"].content_sha256,
                "local_receipt_sha256": references["receipt"].content_sha256,
                "nuclear_repulsion_energy_hartree": float(nuclear),
            },
            maximum_bytes=maximum_bytes,
        )
    return summary


def _scientific_result_identity(
    result_kind: str,
    result: EnergyResult,
    experiment: Any,
    payloads: dict[str, Any],
    references: dict[str, ArtifactReference],
) -> ScientificResultIdentity:
    vqe_result = result if isinstance(result, VQEResult) else None
    if vqe_result is not None:
        solver_configuration = experiment.quantum_model.model_dump(mode="json")
    else:
        solver_configuration = {
            "exact_solver": experiment.verification_policy.exact_solver,
            "particle_sector_filtering_policy": "default_particle_sector_filter",
        }
    return ScientificResultIdentity(
        result_kind=result_kind,
        experiment_sha256=experiment.fingerprint,
        molecular_structure_sha256=references["molecular_structure"].content_sha256,
        electronic_problem_sha256=references["electronic_problem"].content_sha256,
        active_space_sha256=references["active_space"].content_sha256,
        fermionic_hamiltonian_sha256=references["fermionic_hamiltonian"].content_sha256,
        qubit_hamiltonian_sha256=references["qubit_hamiltonian"].content_sha256,
        solver_identifier=result.solver_identifier,
        solver_version=result.solver_version,
        solver_configuration_sha256=sha256_fingerprint(solver_configuration),
        environment_compatibility_sha256=payloads["environment"][
            "environment_compatibility_sha256"
        ],
        electronic_energy=result.electronic_energy_hartree,
        nuclear_repulsion_energy=result.nuclear_repulsion_energy_hartree,
        total_energy=result.total_energy_hartree,
        particle_count=result.particle_count,
        number_of_spatial_orbitals=result.number_of_spatial_orbitals,
        number_of_spin_orbitals=result.number_of_spin_orbitals,
        number_of_qubits=result.number_of_qubits,
        converged=vqe_result.converged if vqe_result is not None else result.completed,
        auxiliary_scientific_values={"raw_eigenvalue_hartree": result.raw_eigenvalue_hartree},
        particle_sector_filtering_policy=(
            None if vqe_result is not None else "default_particle_sector_filter"
        ),
        estimator_type=(
            experiment.quantum_model.simulator_type if vqe_result is not None else None
        ),
        initial_point_sha256=(
            vqe_result.initial_point_sha256 if vqe_result is not None else None
        ),
        optimized_parameters_sha256=(
            vqe_result.optimized_parameters_sha256 if vqe_result is not None else None
        ),
        optimization_trace_sha256=(
            references["optimization_trace"].content_sha256
            if vqe_result is not None
            else None
        ),
        ansatz_sha256=(
            references["ansatz_manifest"].content_sha256
            if vqe_result is not None
            else None
        ),
        ansatz_identifier=(
            vqe_result.ansatz_identifier if vqe_result is not None else None
        ),
        initial_state_identifier=(
            vqe_result.initial_state_identifier if vqe_result is not None else None
        ),
        mapper_identifier=experiment.quantum_model.mapper,
        verification_policy_sha256=experiment.verification_policy.fingerprint,
    )


def _record(
    directory: Path,
    name: str,
    payload: Any,
    *,
    parents: tuple[Any, ...] = (),
    maximum_bytes: int,
) -> ArtifactReference:
    reference = artifact_reference(
        name,
        _ARTIFACT_TYPES[name],
        payload,
        filename=_FILENAMES[name],
        parents=parents,
    )
    document = artifact_document(_ARTIFACT_TYPES[name], payload)
    write_json_atomic(directory / _FILENAMES[name], document, maximum_bytes=maximum_bytes)
    return reference


def _edge(source: ArtifactReference, destination: ArtifactReference, relationship: str) -> ArtifactLineageEdge:
    return ArtifactLineageEdge(
        source=source.pointer,
        destination=destination.pointer,
        relationship_type=relationship,
        producing_capability="trusted_quantum_preflight",
        producing_capability_version=SCHEMA_VERSION,
    )


def _lineage(references: dict[str, ArtifactReference]) -> ArtifactLineageGraph:
    links = (
        ("experiment", "molecular_structure", "defines"),
        ("molecular_structure", "qcschema", "drives"),
        ("qcschema", "electronic_problem", "constructs"),
        ("electronic_problem", "active_space", "transforms"),
        ("active_space", "fermionic_hamiltonian", "produces"),
        ("fermionic_hamiltonian", "qubit_hamiltonian", "maps"),
        ("qubit_hamiltonian", "exact_result", "solved_exactly_by"),
        ("qubit_hamiltonian", "vqe_result", "solved_variationally_by"),
        ("qubit_hamiltonian", "optimization_trace", "optimized_against"),
    )
    return ArtifactLineageGraph(
        edges=tuple(_edge(references[source], references[destination], relation) for source, destination, relation in links)
    )


def _next_run_path(root: Path, experiment_identifier: str) -> tuple[str, Path]:
    experiment_root = root / experiment_identifier
    experiment_root.mkdir(parents=True, exist_ok=True)
    for number in range(1, 1_000_000):
        run_id = f"run-{number:03d}"
        candidate = experiment_root / run_id
        if not candidate.exists() and not candidate.with_name(f"{run_id}-failed").exists():
            return run_id, candidate
    raise QuantumIntegrityError("No available monotonic run identifier.")


@contextmanager
def _wall_clock_limit(seconds: int) -> Iterator[None]:
    if os.name == "nt" or not hasattr(signal, "SIGALRM"):
        yield
        return

    def expired(signum: int, frame: Any) -> None:
        del signum, frame
        raise QuantumTimeoutError(f"Trusted workflow exceeded {seconds} seconds.")

    alarm = getattr(signal, "alarm")
    previous = signal.signal(signal.SIGALRM, expired)
    alarm(seconds)
    try:
        yield
    finally:
        alarm(0)
        signal.signal(signal.SIGALRM, previous)
