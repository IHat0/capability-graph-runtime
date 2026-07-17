"""Trusted fail-closed adjudication of hostile candidate claims and files."""

from __future__ import annotations

from typing import Any

from cgr.quantum_preflight.contracts import QuantumChemistryExperiment
from cgr.quantum_preflight.identities import ScientificResultIdentity
from cgr.science import ArtifactPointer, sha256_fingerprint

from .contracts import (
    CandidateAdjudicationReceipt,
    CandidateExecutionEvidence,
    CandidateFinding,
    CandidateOutputSummary,
)
from .findings import finding, ordered_findings, primary_failure
from .protocol import (
    CandidateOutputPackage,
    load_candidate_summary,
    validate_artifact_claim_path,
)
from .trusted import TrustedReferenceView, trusted_payload_fingerprints

REQUIRED_ARTIFACTS = {
    "molecular_structure",
    "electronic_problem",
    "active_space",
    "fermionic_hamiltonian",
    "qubit_hamiltonian",
    "ansatz_manifest",
    "optimization_trace",
    "candidate_result",
    "environment",
}
REQUIRED_LINEAGE = {
    ("molecular_structure", "electronic_problem"),
    ("electronic_problem", "active_space"),
    ("active_space", "fermionic_hamiltonian"),
    ("fermionic_hamiltonian", "qubit_hamiltonian"),
    ("qubit_hamiltonian", "candidate_result"),
    ("optimization_trace", "candidate_result"),
}


def adjudicate_candidate(
    *,
    experiment: QuantumChemistryExperiment,
    execution: CandidateExecutionEvidence,
    package: CandidateOutputPackage,
    trusted: TrustedReferenceView,
    candidate_dependency_lock_sha256: str,
) -> CandidateAdjudicationReceipt:
    findings: list[CandidateFinding] = list(package.findings)
    _execution_findings(execution, findings)
    summary, protocol_findings = load_candidate_summary(package)
    findings.extend(protocol_findings)
    artifact_pointers = tuple(item.pointer for item in package.files)
    scientific_sha: str | None = None
    if summary is not None:
        scientific_sha = _validate_summary_and_science(
            experiment=experiment,
            execution=execution,
            package=package,
            summary=summary,
            trusted=trusted,
            findings=findings,
        )
    ordered = ordered_findings(findings)
    primary = primary_failure(ordered)
    receipt_values: dict[str, Any] = {
        "candidate_identifier": execution.candidate_identifier,
        "candidate_source_tree_sha256": execution.source_tree_sha256,
        "input_experiment_sha256": experiment.fingerprint,
        "candidate_image_identifier": execution.image_identifier,
        "candidate_dependency_lock_sha256": candidate_dependency_lock_sha256,
        "sandbox_policy_sha256": execution.sandbox_policy_sha256,
        "execution_evidence": ArtifactPointer(
            artifact_identifier="candidate_execution",
            content_sha256=execution.fingerprint,
        ),
        "candidate_output_package_sha256": package.package_sha256,
        "candidate_artifacts": artifact_pointers,
        "recomputed_scientific_result_sha256": scientific_sha,
        "trusted_reference_receipt_sha256": trusted.receipt_content_sha256,
        "findings": ordered,
        "primary_failure_code": primary,
        "authorized": not any(item.blocking for item in ordered),
        "authorization_policy_sha256": sha256_fingerprint(
            {
                "execution": "passed",
                "protocol": "passed",
                "scientific_specification": "passed",
                "hamiltonian": "passed",
                "result": "passed",
                "integrity": "passed",
                "security": "no_blocking_findings",
            }
        ),
    }
    provisional = CandidateAdjudicationReceipt.model_construct(
        **receipt_values,
        receipt_content_sha256="0" * 64,
    )
    receipt_values["receipt_content_sha256"] = sha256_fingerprint(
        provisional.canonical_identity()
    )
    return CandidateAdjudicationReceipt.model_validate(receipt_values)


def _execution_findings(
    execution: CandidateExecutionEvidence,
    findings: list[CandidateFinding],
) -> None:
    if execution.forbidden_cgr_import_attempted:
        findings.append(
            finding(
                "candidate_forbidden_dependency",
                "Candidate source attempted to import trusted CGR internals.",
            )
        )
    if execution.network_access_attempted:
        findings.append(
            finding(
                "candidate_network_attempt",
                "Candidate source attempted outbound network access; the sandbox remained offline.",
            )
        )
    if execution.output_policy_violated and not any(
        item.code == "candidate_output_path_violation" for item in findings
    ):
        findings.append(
            finding(
                "candidate_output_path_violation",
                "Candidate output violated a sandbox quota or path policy.",
            )
        )
    mapping = {
        "syntax_error": ("candidate_syntax_error", "Candidate source did not parse."),
        "import_error": (
            "candidate_import_error",
            "Candidate failed while importing a dependency.",
        ),
        "runtime_error": (
            "candidate_runtime_error",
            "Candidate raised during execution.",
        ),
        "timeout": ("candidate_timeout", "Candidate exceeded its wall-clock bound."),
    }
    if execution.execution_category in mapping:
        code, explanation = mapping[execution.execution_category]
        findings.append(finding(code, explanation))
    if not execution.network_disabled:
        findings.append(
            finding(
                "candidate_network_attempt",
                "Candidate execution was not network-isolated.",
            )
        )
    if execution.trusted_evidence_exposed:
        findings.append(
            finding(
                "candidate_output_path_violation",
                "Trusted evidence was exposed inside the hostile candidate boundary.",
            )
        )


def _validate_summary_and_science(
    *,
    experiment: QuantumChemistryExperiment,
    execution: CandidateExecutionEvidence,
    package: CandidateOutputPackage,
    summary: CandidateOutputSummary,
    trusted: TrustedReferenceView,
    findings: list[CandidateFinding],
) -> str | None:
    if summary.authorized is True:
        findings.append(
            finding(
                "candidate_untrusted_authorization_claim",
                "Candidate-provided authorization has no authority.",
                observed=True,
            )
        )
    if summary.input_manifest_sha256 != execution.input_manifest_sha256:
        findings.append(
            finding(
                "candidate_protocol_invalid",
                "Candidate summary references a different public input.",
                expected=execution.input_manifest_sha256,
                observed=summary.input_manifest_sha256,
            )
        )
    if (
        not summary.execution_completed
        or summary.claimed_workflow != "lih_statevector_vqe"
    ):
        findings.append(
            finding(
                "candidate_protocol_invalid",
                "Candidate did not attest completion of the required public workflow profile.",
            )
        )
    package_files = package.by_path()
    claims = {item.role: item for item in summary.artifacts}
    claimed_paths = [item.path for item in summary.artifacts]
    if len(claimed_paths) != len(set(claimed_paths)):
        findings.append(
            finding(
                "candidate_lineage_mismatch",
                "Multiple scientific roles are cross-linked to the same candidate artifact.",
            )
        )
    if missing := sorted(REQUIRED_ARTIFACTS - set(claims)):
        findings.append(
            finding(
                "candidate_evidence_incomplete",
                "Candidate omitted required scientific artifact roles: "
                + ", ".join(missing),
            )
        )
        return None
    payloads: dict[str, Any] = {}
    for role, claim in claims.items():
        if not validate_artifact_claim_path(claim):
            findings.append(
                finding(
                    "candidate_output_path_violation",
                    "Candidate artifact claim uses an unsafe absolute, URL, or traversal path.",
                    subject_artifact=role,
                )
            )
            continue
        collected = package_files.get(claim.path)
        if collected is None:
            findings.append(
                finding(
                    "candidate_evidence_incomplete",
                    "Candidate inventory references a missing file.",
                    subject_artifact=role,
                )
            )
            continue
        if (
            claim.content_sha256 is not None
            and claim.content_sha256 != collected.content_sha256
        ):
            findings.append(
                finding(
                    "candidate_content_hash_mismatch",
                    "Candidate-provided content hash disagrees with the collected bytes.",
                    subject_artifact=role,
                    expected=collected.content_sha256,
                    observed=claim.content_sha256,
                )
            )
        if collected.payload is None:
            findings.append(
                finding(
                    "candidate_protocol_invalid",
                    "Candidate scientific artifact is not valid JSON.",
                    subject_artifact=role,
                )
            )
            continue
        payloads[role] = collected.payload
    if REQUIRED_ARTIFACTS - set(payloads):
        return None
    _scientific_specification_findings(experiment, payloads, summary, findings)
    _workflow_and_internal_lineage_findings(experiment, payloads, summary, findings)
    trusted_hashes = trusted_payload_fingerprints(trusted)
    comparison_codes = {
        "molecular_structure": "candidate_structure_mismatch",
        "electronic_problem": "candidate_basis_mismatch",
        "active_space": "candidate_active_space_mismatch",
    }
    for role, code in comparison_codes.items():
        already_classified = (
            role == "molecular_structure"
            and any(
                item.code
                in {
                    "candidate_coordinate_unit_mismatch",
                    "candidate_structure_mismatch",
                    "candidate_charge_mismatch",
                    "candidate_multiplicity_mismatch",
                }
                for item in findings
            )
        ) or any(item.code == code for item in findings)
        if (
            sha256_fingerprint(payloads[role]) != trusted_hashes[role]
            and not already_classified
        ):
            findings.append(
                finding(
                    code,
                    f"Candidate {role} differs from the independently verified reference.",
                    subject_artifact=role,
                    expected=trusted_hashes[role],
                    observed=sha256_fingerprint(payloads[role]),
                )
            )
    for role, code in (
        ("fermionic_hamiltonian", "candidate_fermionic_hamiltonian_mismatch"),
        ("qubit_hamiltonian", "candidate_qubit_hamiltonian_mismatch"),
    ):
        observed = sha256_fingerprint(payloads[role])
        if observed != trusted_hashes[role]:
            findings.append(
                finding(
                    code,
                    f"Candidate {role} differs from the independently verified reference.",
                    subject_artifact=role,
                    expected=trusted_hashes[role],
                    observed=observed,
                )
            )
    _result_findings(experiment, payloads["candidate_result"], trusted, findings)
    observed_lineage = {
        (item.source_role, item.destination_role) for item in summary.lineage
    }
    if not REQUIRED_LINEAGE.issubset(observed_lineage):
        findings.append(
            finding(
                "candidate_lineage_mismatch",
                "Candidate lineage omits required scientific derivation edges.",
            )
        )
    result_hamiltonian = payloads["candidate_result"].get("hamiltonian_sha256")
    expected_hamiltonian = sha256_fingerprint(payloads["qubit_hamiltonian"])
    if result_hamiltonian != expected_hamiltonian:
        findings.append(
            finding(
                "candidate_lineage_mismatch",
                "Candidate result is cross-linked to a different Hamiltonian.",
                expected=expected_hamiltonian,
                observed=result_hamiltonian,
            )
        )
    if payloads["candidate_result"].get("nuclear_repulsion_energy_hartree") is None:
        return None
    try:
        identity = _candidate_identity(experiment, payloads)
    except (KeyError, TypeError, ValueError) as exc:
        findings.append(
            finding(
                "candidate_protocol_invalid",
                "Candidate result cannot be normalized into the trusted result identity.",
                observed=type(exc).__name__,
            )
        )
        return None
    if summary.claimed_scientific_result_sha256 is not None and (
        summary.claimed_scientific_result_sha256 != identity.fingerprint
    ):
        findings.append(
            finding(
                "candidate_scientific_identity_mismatch",
                "Candidate-provided scientific identity was not reproduced by the adjudicator.",
                expected=identity.fingerprint,
                observed=summary.claimed_scientific_result_sha256,
            )
        )
    return identity.fingerprint


def _workflow_and_internal_lineage_findings(
    experiment: QuantumChemistryExperiment,
    payloads: dict[str, Any],
    summary: CandidateOutputSummary,
    findings: list[CandidateFinding],
) -> None:
    result = payloads["candidate_result"]
    ansatz = payloads["ansatz_manifest"]
    quantum = experiment.quantum_model
    expected_workflow = {
        "solver_identifier": "statevector_vqe",
        "ansatz_identifier": quantum.ansatz,
        "initial_state_identifier": quantum.initial_state,
        "estimator_type": quantum.simulator_type,
    }
    if (
        any(result.get(key) != value for key, value in expected_workflow.items())
        or result.get("completed") is not True
        or summary.claimed_solver != "statevector_vqe"
        or summary.claimed_converged != (result.get("converged") is True)
        or summary.claimed_molecular_specification
        != experiment.molecular_system.model_dump(mode="json")
        or summary.claimed_active_space
        != experiment.electronic_structure.model_dump(mode="json")
    ):
        findings.append(
            finding(
                "candidate_protocol_invalid",
                "Candidate workflow claims do not reproduce the required public workflow profile.",
            )
        )
    result_energy_claims = {
        key: result.get(key)
        for key in (
            "electronic_energy_hartree",
            "nuclear_repulsion_energy_hartree",
            "total_energy_hartree",
        )
    }
    if any(
        summary.claimed_energies.get(key) != value
        for key, value in result_energy_claims.items()
    ):
        findings.append(
            finding(
                "candidate_protocol_invalid",
                "Candidate summary energy claims disagree with its result artifact.",
            )
        )
    expected_links = {
        "active_space_sha256": sha256_fingerprint(payloads["active_space"]),
        "hamiltonian_sha256": sha256_fingerprint(payloads["qubit_hamiltonian"]),
        "initial_point_sha256": result.get("initial_point_sha256"),
        "optimized_parameters_sha256": result.get("optimized_parameters_sha256"),
    }
    broken_link = any(ansatz.get(key) != value for key, value in expected_links.items())
    broken_link = broken_link or result.get("environment_sha256") != sha256_fingerprint(
        payloads["environment"]
    )
    broken_link = broken_link or ansatz.get("ansatz") != quantum.ansatz
    broken_link = broken_link or ansatz.get("initial_state") != quantum.initial_state
    broken_link = broken_link or ansatz.get("mapper") != quantum.mapper
    parameter_count = ansatz.get("number_of_parameters")
    if isinstance(parameter_count, int) and parameter_count >= 0:
        broken_link = broken_link or result.get(
            "initial_point_sha256"
        ) != sha256_fingerprint([0.0] * parameter_count)
    else:
        broken_link = True
    if (
        not isinstance(payloads["optimization_trace"], list)
        or not payloads["optimization_trace"]
    ):
        broken_link = True
    if broken_link:
        findings.append(
            finding(
                "candidate_lineage_mismatch",
                "Candidate internal workflow hashes do not link its active space, ansatz, Hamiltonian, environment, trace, and result.",
            )
        )


def _scientific_specification_findings(
    experiment: QuantumChemistryExperiment,
    payloads: dict[str, Any],
    summary: CandidateOutputSummary,
    findings: list[CandidateFinding],
) -> None:
    declared_molecule = experiment.molecular_system.model_dump(mode="json")
    structure = payloads["molecular_structure"]
    if structure.get("coordinate_unit") != declared_molecule["coordinate_unit"]:
        findings.append(
            finding(
                "candidate_coordinate_unit_mismatch",
                "Candidate coordinate unit differs from the manifest.",
            )
        )
    if (
        structure.get("atoms") != declared_molecule["atoms"]
        or structure.get("declared_bond_distance")
        != declared_molecule["declared_bond_distance"]
    ):
        findings.append(
            finding(
                "candidate_structure_mismatch",
                "Candidate geometry differs from the declared molecule.",
            )
        )
    if structure.get("molecular_charge") != declared_molecule["molecular_charge"]:
        findings.append(
            finding(
                "candidate_charge_mismatch",
                "Candidate molecular charge differs from the manifest.",
            )
        )
    if structure.get("spin_multiplicity") != declared_molecule["spin_multiplicity"]:
        findings.append(
            finding(
                "candidate_multiplicity_mismatch",
                "Candidate spin multiplicity differs from the manifest.",
            )
        )
    electronic = payloads["electronic_problem"]
    if electronic.get("basis_set") != experiment.electronic_structure.basis_set:
        findings.append(
            finding(
                "candidate_basis_mismatch", "Candidate basis differs from the manifest."
            )
        )
    active = payloads["active_space"]
    expected_active = experiment.electronic_structure
    if (
        active.get("active_electron_count") != expected_active.active_electron_count
        or active.get("active_spatial_orbital_count")
        != expected_active.active_spatial_orbital_count
        or active.get("declared_active_orbital_indices")
        != list(expected_active.active_orbital_indices)
    ):
        findings.append(
            finding(
                "candidate_active_space_mismatch",
                "Candidate active space differs from the manifest.",
            )
        )
    if (
        payloads["qubit_hamiltonian"].get("mapper") != experiment.quantum_model.mapper
        or summary.claimed_mapper != experiment.quantum_model.mapper
    ):
        findings.append(
            finding(
                "candidate_mapper_mismatch",
                "Candidate mapper differs from the manifest.",
            )
        )


def _result_findings(
    experiment: QuantumChemistryExperiment,
    result: dict[str, Any],
    trusted: TrustedReferenceView,
    findings: list[CandidateFinding],
) -> None:
    electronic = result.get("electronic_energy_hartree")
    nuclear = result.get("nuclear_repulsion_energy_hartree")
    total = result.get("total_energy_hartree")
    if nuclear is None:
        findings.append(
            finding(
                "candidate_nuclear_repulsion_missing",
                "Candidate result omits nuclear-repulsion energy.",
            )
        )
    if all(isinstance(value, (int, float)) for value in (electronic, nuclear, total)):
        electronic_value = float(electronic)  # type: ignore[arg-type]
        nuclear_value = float(nuclear)  # type: ignore[arg-type]
        total_value = float(total)  # type: ignore[arg-type]
        if abs(electronic_value + nuclear_value - total_value) > 1e-10:
            findings.append(
                finding(
                    "candidate_total_energy_semantics_invalid",
                    "Candidate total energy is inconsistent with its decomposition.",
                )
            )
    elif total is None:
        findings.append(
            finding(
                "candidate_total_energy_semantics_invalid",
                "Candidate result omits molecular total energy.",
            )
        )
    if result.get("converged") is not True:
        findings.append(
            finding(
                "candidate_vqe_not_converged",
                "Candidate VQE did not demonstrate convergence.",
            )
        )
    if (
        isinstance(total, (int, float))
        and abs(float(total) - trusted.exact_total_energy)
        > experiment.verification_policy.energy_difference_tolerance_hartree
    ):
        findings.append(
            finding(
                "candidate_energy_disagreement",
                "Candidate total energy differs from the independently verified exact result beyond policy tolerance.",
                expected=experiment.verification_policy.energy_difference_tolerance_hartree,
                observed=abs(float(total) - trusted.exact_total_energy),
            )
        )


def _candidate_identity(
    experiment: QuantumChemistryExperiment,
    payloads: dict[str, Any],
) -> ScientificResultIdentity:
    result = payloads["candidate_result"]
    return ScientificResultIdentity(
        result_kind="vqe_ground_state",
        experiment_sha256=experiment.fingerprint,
        molecular_structure_sha256=sha256_fingerprint(payloads["molecular_structure"]),
        electronic_problem_sha256=sha256_fingerprint(payloads["electronic_problem"]),
        active_space_sha256=sha256_fingerprint(payloads["active_space"]),
        fermionic_hamiltonian_sha256=sha256_fingerprint(
            payloads["fermionic_hamiltonian"]
        ),
        qubit_hamiltonian_sha256=sha256_fingerprint(payloads["qubit_hamiltonian"]),
        solver_identifier=str(result["solver_identifier"]),
        solver_version=str(result["solver_version"]),
        solver_configuration_sha256=experiment.quantum_model.fingerprint,
        environment_compatibility_sha256=sha256_fingerprint(payloads["environment"]),
        electronic_energy=float(result["electronic_energy_hartree"]),
        nuclear_repulsion_energy=float(result["nuclear_repulsion_energy_hartree"]),
        total_energy=float(result["total_energy_hartree"]),
        particle_count=float(result["particle_count"])
        if result.get("particle_count") is not None
        else None,
        number_of_spatial_orbitals=int(result["number_of_spatial_orbitals"]),
        number_of_spin_orbitals=int(result["number_of_spin_orbitals"]),
        number_of_qubits=int(result["number_of_qubits"]),
        converged=bool(result["converged"]),
        auxiliary_scientific_values={
            "raw_eigenvalue_hartree": float(result["raw_eigenvalue_hartree"])
        },
        estimator_type=str(result["estimator_type"]),
        initial_point_sha256=str(result["initial_point_sha256"]),
        optimized_parameters_sha256=str(result["optimized_parameters_sha256"]),
        optimization_trace_sha256=sha256_fingerprint(payloads["optimization_trace"]),
        ansatz_sha256=sha256_fingerprint(payloads["ansatz_manifest"]),
        ansatz_identifier=str(result["ansatz_identifier"]),
        initial_state_identifier=str(result["initial_state_identifier"]),
        mapper_identifier=str(payloads["qubit_hamiltonian"]["mapper"]),
        verification_policy_sha256=experiment.verification_policy.fingerprint,
    )
