"""Fail-closed Scientific Executable Verifier for trusted LiH evidence."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import (
    ArtifactLineageGraph,
    ArtifactPointer,
    ArtifactReference,
    FindingSeverity,
    ScientificVerificationOutcome,
    ScientificVerificationResult,
    VerificationFinding,
)

from .contracts import QuantumChemistryExperiment
from .environment import DIRECT_VERSIONS, environment_compatibility_identity
from .identities import ScientificResultArtifact, inspect_result_artifact
from .operators import encode_float
from .results import EnergyResult, VQEResult
from .warnings import CompatibilityWarningEvidence

VERIFIER_VERSION = CapabilityVersion(major=1, minor=0, patch=0)


def _finding(
    code: str,
    message: str,
    *,
    expected: str | int | float | bool | None = None,
    observed: str | int | float | bool | None = None,
) -> VerificationFinding:
    return VerificationFinding(
        code=code,
        severity=FindingSeverity.ERROR,
        message=message,
        expected=expected,
        observed=observed,
        blocking=True,
    )


def _result(
    verifier: str,
    subject: ArtifactPointer,
    findings: list[VerificationFinding],
    evidence: tuple[ArtifactPointer, ...] = (),
) -> ScientificVerificationResult:
    outcome = (
        ScientificVerificationOutcome.FAILED
        if findings
        else ScientificVerificationOutcome.PASSED
    )
    return ScientificVerificationResult(
        verifier_identifier=verifier,
        verifier_version=VERIFIER_VERSION,
        subject=subject,
        outcome=outcome,
        findings=tuple(findings),
        summary=(
            f"{verifier} found {len(findings)} blocking failure(s)."
            if findings
            else f"{verifier} passed."
        ),
        evidence=evidence,
    )


def verify_execution(
    experiment: QuantumChemistryExperiment,
    references: dict[str, ArtifactReference],
    payloads: dict[str, Any],
    lineage: ArtifactLineageGraph,
) -> tuple[ScientificVerificationResult, ...]:
    """Run independent verifier families over exact content-addressed evidence."""
    subject = references["experiment"].pointer
    verifiers: tuple[Callable[[], ScientificVerificationResult], ...] = (
        lambda: _verify_specification(experiment, subject),
        lambda: _verify_molecule(experiment, subject, payloads["molecular_structure"]),
        lambda: _verify_electronic(experiment, subject, payloads),
        lambda: _verify_hamiltonian(experiment, subject, payloads, references),
        lambda: _verify_exact(subject, payloads["exact_result"], references),
        lambda: _verify_vqe(experiment, subject, payloads, references),
        lambda: _verify_agreement(experiment, subject, payloads),
        lambda: _verify_lineage(subject, references, lineage),
        lambda: _verify_environment(subject, payloads["environment"]),
        lambda: _verify_compatibility(
            subject,
            payloads["compatibility_warnings"],
            references["compatibility_warnings"].pointer,
        ),
    )
    return tuple(verifier() for verifier in verifiers)


def _verify_specification(
    experiment: QuantumChemistryExperiment, subject: ArtifactPointer
) -> ScientificVerificationResult:
    findings: list[VerificationFinding] = []
    supported = {
        "mapper": "jordan_wigner",
        "ansatz": "uccsd",
        "initial_state": "hartree_fock",
        "exact_solver": "numpy_minimum_eigensolver",
        "simulator": "statevector_estimator",
    }
    observed = {
        "mapper": experiment.quantum_model.mapper,
        "ansatz": experiment.quantum_model.ansatz,
        "initial_state": experiment.quantum_model.initial_state,
        "exact_solver": experiment.verification_policy.exact_solver,
        "simulator": experiment.quantum_model.simulator_type,
    }
    for name, expected in supported.items():
        if observed[name] != expected:
            findings.append(
                _finding(
                    f"spec.unsupported_{name}",
                    f"Unsupported {name} declaration.",
                    expected=expected,
                    observed=observed[name],
                )
            )
    if experiment.execution_policy.maximum_duration_seconds <= 0:
        findings.append(_finding("spec.unbounded_duration", "Execution duration is unbounded."))
    return _result("quantum.specification", subject, findings)


def _verify_molecule(
    experiment: QuantumChemistryExperiment,
    subject: ArtifactPointer,
    executed: dict[str, Any],
) -> ScientificVerificationResult:
    declared = experiment.molecular_system.model_dump(mode="json")
    findings: list[VerificationFinding] = []
    for field in ("atoms", "coordinate_unit", "molecular_charge", "spin_multiplicity"):
        if executed.get(field) != declared[field]:
            findings.append(
                _finding(
                    f"molecule.{field}_mismatch",
                    f"Executed molecular {field} differs from the manifest.",
                    expected=str(declared[field]),
                    observed=str(executed.get(field)),
                )
            )
    if executed.get("driver_spin") != experiment.molecular_system.driver_spin:
        findings.append(_finding("molecule.spin_mismatch", "Driver spin differs from multiplicity."))
    return _result("quantum.molecular_identity", subject, findings)


def _verify_electronic(
    experiment: QuantumChemistryExperiment,
    subject: ArtifactPointer,
    payloads: dict[str, Any],
) -> ScientificVerificationResult:
    problem = payloads["electronic_problem"]
    active = payloads["active_space"]
    model = experiment.electronic_structure
    findings: list[VerificationFinding] = []
    expected: dict[str, str | bool] = {
        "basis_set": model.basis_set,
        "reference_method": model.reference_method,
        "driver_identifier": model.driver_identifier,
        "frozen_core_policy": model.frozen_core,
    }
    for field, value in expected.items():
        if problem.get(field) != value:
            findings.append(_finding(f"electronic.{field}_mismatch", f"{field} differs.", expected=value, observed=problem.get(field)))
    if active.get("resolved_active_orbital_indices") != list(model.active_orbital_indices):
        findings.append(_finding("electronic.active_orbitals_mismatch", "Resolved active orbitals differ from the manifest."))
    if active.get("active_electron_count") != model.active_electron_count:
        findings.append(_finding("electronic.active_electrons_mismatch", "Active electron count differs."))
    for field in ("pre_transform_particle_count", "pre_transform_spatial_orbitals"):
        if problem.get(field) is None:
            findings.append(_finding(f"electronic.{field}_missing", f"{field} was not recorded."))
    if problem.get("nuclear_repulsion_energy_hartree") is None:
        findings.append(_finding("electronic.nuclear_repulsion_missing", "Nuclear repulsion energy was not recorded separately."))
    return _result("quantum.electronic_structure", subject, findings)


def _verify_hamiltonian(
    experiment: QuantumChemistryExperiment,
    subject: ArtifactPointer,
    payloads: dict[str, Any],
    references: dict[str, ArtifactReference],
) -> ScientificVerificationResult:
    fermionic = payloads["fermionic_hamiltonian"]
    qubit = payloads["qubit_hamiltonian"]
    findings: list[VerificationFinding] = []
    if not fermionic.get("terms"):
        findings.append(_finding("hamiltonian.fermionic_empty", "Fermionic operator is empty."))
    if not qubit.get("terms"):
        findings.append(_finding("hamiltonian.qubit_empty", "Qubit operator is empty."))
    if qubit.get("mapper") != experiment.quantum_model.mapper:
        findings.append(_finding("hamiltonian.mapper_mismatch", "Produced mapper differs from the manifest."))
    residual = payloads["hamiltonian_metrics"].get("maximum_antihermitian_coefficient")
    if not isinstance(residual, (int, float)) or not math.isfinite(residual):
        findings.append(_finding("hamiltonian.hermiticity_nonfinite", "Hermiticity metric is non-finite."))
    elif residual > experiment.verification_policy.hermiticity_tolerance:
        findings.append(_finding("hamiltonian.non_hermitian", "Qubit Hamiltonian is not Hermitian within tolerance.", expected=experiment.verification_policy.hermiticity_tolerance, observed=residual))
    if payloads["hamiltonian_metrics"].get("qubit_sha256") != references["qubit_hamiltonian"].content_sha256:
        findings.append(_finding("hamiltonian.fingerprint_mismatch", "Qubit Hamiltonian fingerprint is internally inconsistent."))
    return _result("quantum.hamiltonian", subject, findings)


def _verify_exact(
    subject: ArtifactPointer,
    value: EnergyResult | dict[str, Any],
    references: dict[str, ArtifactReference],
) -> ScientificVerificationResult:
    findings: list[VerificationFinding] = []
    exact_artifact = _hardened_result(value, "exact", findings)
    if exact_artifact is None:
        return _result("quantum.exact_result", subject, findings)
    exact = exact_artifact.execution_result
    if not isinstance(exact, EnergyResult) or isinstance(exact, VQEResult):
        findings.append(_finding("exact.result_kind_mismatch", "Exact result payload has the wrong type."))
        return _result("quantum.exact_result", subject, findings)
    if not exact.completed:
        findings.append(_finding("exact.incomplete", "Exact solver did not complete."))
    if exact.hamiltonian_sha256 != references["qubit_hamiltonian"].content_sha256:
        findings.append(_finding("exact.hamiltonian_mismatch", "Exact result references the wrong Hamiltonian."))
    if exact.particle_count is None:
        findings.append(_finding("exact.particle_sector_missing", "Exact particle-sector evidence is missing."))
    if exact.particle_sector_filter_applied is not True:
        findings.append(_finding("exact.particle_filter_missing", "Exact particle-sector filtering was not applied."))
    return _result("quantum.exact_result", subject, findings)


def _verify_vqe(
    experiment: QuantumChemistryExperiment,
    subject: ArtifactPointer,
    payloads: dict[str, Any],
    references: dict[str, ArtifactReference],
) -> ScientificVerificationResult:
    findings: list[VerificationFinding] = []
    vqe_artifact = _hardened_result(payloads["vqe_result"], "vqe", findings)
    if vqe_artifact is None or not isinstance(vqe_artifact.execution_result, VQEResult):
        if vqe_artifact is not None:
            findings.append(_finding("vqe.result_kind_mismatch", "VQE result payload has the wrong type."))
        return _result("quantum.vqe_result", subject, findings)
    vqe = vqe_artifact.execution_result
    if not vqe.completed or not vqe.converged:
        findings.append(_finding("vqe.incomplete", "VQE did not complete and converge."))
    if vqe.hamiltonian_sha256 != references["qubit_hamiltonian"].content_sha256:
        findings.append(_finding("vqe.hamiltonian_mismatch", "VQE result references the wrong Hamiltonian."))
    if vqe.ansatz_identifier != experiment.quantum_model.ansatz:
        findings.append(_finding("vqe.ansatz_mismatch", "VQE ansatz differs from the manifest."))
    if vqe.initial_state_identifier != experiment.quantum_model.initial_state:
        findings.append(_finding("vqe.initial_state_mismatch", "VQE initial state differs from the manifest."))
    if not payloads["optimization_trace"]:
        findings.append(_finding("vqe.optimization_trace_missing", "Optimization trace is empty."))
    return _result("quantum.vqe_result", subject, findings)


def _verify_agreement(
    experiment: QuantumChemistryExperiment,
    subject: ArtifactPointer,
    payloads: dict[str, Any],
) -> ScientificVerificationResult:
    findings: list[VerificationFinding] = []
    exact_artifact = _hardened_result(payloads["exact_result"], "exact", findings)
    vqe_artifact = _hardened_result(payloads["vqe_result"], "vqe", findings)
    if exact_artifact is None or vqe_artifact is None:
        return _result("quantum.numerical_agreement", subject, findings)
    exact = exact_artifact.execution_result
    vqe = vqe_artifact.execution_result
    if isinstance(exact, VQEResult) or not isinstance(vqe, VQEResult):
        findings.append(_finding("agreement.result_kind_mismatch", "Agreement result kinds are invalid."))
        return _result("quantum.numerical_agreement", subject, findings)
    difference = abs(vqe.total_energy_hartree - exact.total_energy_hartree)
    payloads["numerical_agreement"] = {
        "exact_total_energy_hex": encode_float(exact.total_energy_hartree),
        "vqe_total_energy_hex": encode_float(vqe.total_energy_hartree),
        "absolute_difference_hartree": difference,
        "tolerance_hartree": experiment.verification_policy.energy_difference_tolerance_hartree,
        "units": "hartree",
        "passed": difference <= experiment.verification_policy.energy_difference_tolerance_hartree,
    }
    if difference > experiment.verification_policy.energy_difference_tolerance_hartree:
        findings.append(_finding("agreement.energy_tolerance_exceeded", "VQE total energy differs from the exact reference beyond tolerance.", expected=experiment.verification_policy.energy_difference_tolerance_hartree, observed=difference))
    return _result("quantum.numerical_agreement", subject, findings)


def _verify_lineage(
    subject: ArtifactPointer,
    references: dict[str, ArtifactReference],
    lineage: ArtifactLineageGraph,
) -> ScientificVerificationResult:
    actual = {(edge.source.artifact_identifier, edge.destination.artifact_identifier) for edge in lineage.edges}
    required = {
        ("experiment", "molecular_structure"),
        ("molecular_structure", "qcschema"),
        ("qcschema", "electronic_problem"),
        ("electronic_problem", "active_space"),
        ("active_space", "fermionic_hamiltonian"),
        ("fermionic_hamiltonian", "qubit_hamiltonian"),
        ("qubit_hamiltonian", "exact_result"),
        ("qubit_hamiltonian", "vqe_result"),
    }
    findings = [
        _finding("lineage.required_edge_missing", f"Missing lineage edge {source}->{destination}.", expected=f"{source}->{destination}")
        for source, destination in sorted(required - actual)
    ]
    known = {reference.pointer for reference in references.values()}
    if any(edge.source not in known or edge.destination not in known for edge in lineage.edges):
        findings.append(_finding("lineage.unknown_artifact", "Lineage references an unknown or substituted artifact."))
    return _result("quantum.lineage", subject, findings)


def _verify_environment(
    subject: ArtifactPointer, environment: dict[str, Any]
) -> ScientificVerificationResult:
    findings: list[VerificationFinding] = []
    if environment.get("os") != "linux":
        findings.append(_finding("environment.not_linux", "Scientific runtime is not Linux.", expected="linux", observed=environment.get("os")))
    if environment.get("python_major_minor") != "3.12":
        findings.append(_finding("environment.python_mismatch", "Scientific runtime is not Python 3.12."))
    if environment.get("network_disabled") is not True:
        findings.append(_finding("environment.network_available", "Outbound network was not proven unavailable."))
    if environment.get("credential_variable_names_present"):
        findings.append(_finding("environment.credential_metadata_present", "Credential variable names entered the runtime."))
    observed_versions = environment.get("direct_package_versions", {})
    if not isinstance(observed_versions, dict):
        observed_versions = {}
    for package, expected in DIRECT_VERSIONS.items():
        if observed_versions.get(package) != expected:
            findings.append(
                _finding(
                    "environment.package_version_mismatch",
                    f"{package} does not match the pinned direct version.",
                    expected=expected,
                    observed=observed_versions.get(package),
                )
            )
    lock_hash = environment.get("dependency_lock_sha256")
    if not isinstance(lock_hash, str) or len(lock_hash) != 64:
        findings.append(_finding("environment.lock_fingerprint_missing", "Dependency lock fingerprint is missing."))
    if not environment.get("container_image_identifier"):
        findings.append(_finding("environment.image_identifier_missing", "Container image identifier is missing."))
    limits = environment.get("thread_limits", {})
    if any(value != "1" for name, value in limits.items() if name != "PYTHONHASHSEED") or limits.get("PYTHONHASHSEED") != "0":
        findings.append(_finding("environment.thread_policy_mismatch", "Deterministic thread policy is incomplete."))
    try:
        compatibility = environment_compatibility_identity(environment)
    except (TypeError, ValueError) as exc:
        findings.append(_finding("environment.compatibility_identity_invalid", str(exc)))
    else:
        if environment.get("environment_compatibility_sha256") != compatibility.fingerprint:
            findings.append(
                _finding(
                    "environment.compatibility_identity_mismatch",
                    "Environment compatibility identity was not recomputed correctly.",
                    expected=compatibility.fingerprint,
                    observed=environment.get("environment_compatibility_sha256"),
                )
            )
    return _result("quantum.environment", subject, findings)


def _verify_compatibility(
    subject: ArtifactPointer,
    value: dict[str, Any],
    evidence: ArtifactPointer,
) -> ScientificVerificationResult:
    warnings = CompatibilityWarningEvidence.model_validate(value)
    findings = [
        _finding(
            f"compatibility.{item.code}",
            item.normalized_message,
            observed=item.dependency_version,
        )
        for item in warnings.warnings
        if item.blocking
    ]
    return _result("quantum.compatibility", subject, findings, (evidence,))


def _hardened_result(
    value: EnergyResult | dict[str, Any],
    label: str,
    findings: list[VerificationFinding],
) -> ScientificResultArtifact | None:
    if isinstance(value, EnergyResult):
        findings.append(
            _finding(
                f"{label}.legacy_result",
                "Legacy flat result lacks a recomputable scientific-result identity.",
            )
        )
        return None
    try:
        inspection = inspect_result_artifact(value)
    except (TypeError, ValueError) as exc:
        findings.append(
            _finding(f"{label}.scientific_identity_invalid", str(exc))
        )
        return None
    if not inspection.hardened or inspection.artifact is None:
        findings.append(
            _finding(
                f"{label}.legacy_result",
                inspection.reason or "Legacy result is not authorized by the hardened verifier.",
            )
        )
        return None
    return inspection.artifact


def blocking_findings(results: tuple[ScientificVerificationResult, ...]) -> tuple[VerificationFinding, ...]:
    return tuple(
        finding
        for result in results
        for finding in result.findings
        if finding.blocking or result.outcome != ScientificVerificationOutcome.PASSED
    )
