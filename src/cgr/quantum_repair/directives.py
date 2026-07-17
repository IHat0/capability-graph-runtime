"""Sanitized finding-to-directive projection with no trusted-answer leakage."""

from __future__ import annotations

import re
from typing import Any

from cgr.quantum_candidate.contracts import CandidateAdjudicationReceipt

from .contracts import (
    QuantumRepairDirective,
    QuantumRepairPolicy,
    SourceManifest,
    sealed_values,
)

_TRUSTED_LEAK = re.compile(
    r"(?:exact[_ -]?energy|trusted[_ -]?(?:energy|hamiltonian|result)|scientific[_ -]?outcome[_ -]?sha256|-[0-9]+\.[0-9]{5,})",
    re.IGNORECASE,
)

_GUIDANCE: dict[str, tuple[str, tuple[str, ...]]] = {
    "candidate_structure_mismatch": (
        "Make candidate geometry match the public molecular specification and regenerate all downstream candidate-owned artifacts.",
        ("public_molecular_specification_used", "downstream_science_recomputed"),
    ),
    "candidate_coordinate_unit_mismatch": (
        "Use the public coordinate unit consistently and regenerate candidate-owned scientific artifacts.",
        ("public_coordinate_unit_used", "downstream_science_recomputed"),
    ),
    "candidate_charge_mismatch": (
        "Use the public molecular charge and reconstruct the electronic problem.",
        ("public_charge_used", "downstream_science_recomputed"),
    ),
    "candidate_multiplicity_mismatch": (
        "Use the public spin multiplicity and reconstruct the electronic problem.",
        ("public_multiplicity_used", "downstream_science_recomputed"),
    ),
    "candidate_basis_mismatch": (
        "Use the public basis declaration and reconstruct candidate-owned Hamiltonians and results.",
        ("public_basis_used", "downstream_science_recomputed"),
    ),
    "candidate_active_space_mismatch": (
        "Use the public active-space requirements and reconstruct dependent artifacts.",
        ("public_active_space_used", "downstream_science_recomputed"),
    ),
    "candidate_mapper_mismatch": (
        "Use the public mapper declaration and regenerate the qubit Hamiltonian and result.",
        ("public_mapper_used", "qubit_science_recomputed"),
    ),
    "candidate_total_energy_semantics_invalid": (
        "Compute molecular total energy from candidate-owned electronic and nuclear-repulsion components.",
        ("energy_components_recomputed", "total_energy_semantics_valid"),
    ),
    "candidate_nuclear_repulsion_missing": (
        "Record the candidate-computed nuclear-repulsion component and regenerate the result.",
        ("nuclear_repulsion_present", "result_recomputed"),
    ),
    "candidate_vqe_not_converged": (
        "Correct candidate convergence handling and retain a complete optimization trace.",
        ("optimization_trace_present", "convergence_demonstrated"),
    ),
    "candidate_energy_disagreement": (
        "Recompute the candidate workflow from public inputs without using withheld reference values.",
        ("downstream_science_recomputed", "trusted_comparison_rerun"),
    ),
}


def create_directive(
    *,
    task_identifier: str,
    repair_run_identifier: str,
    attempt_identifier: str,
    attempt_index: int,
    source_manifest: SourceManifest,
    adjudication: CandidateAdjudicationReceipt,
    policy: QuantumRepairPolicy,
    allowed_edit_paths: tuple[str, ...],
) -> QuantumRepairDirective:
    primary = adjudication.primary_failure_code
    if adjudication.authorized or primary is None:
        raise ValueError("Authorized candidates cannot receive repair directives.")
    guidance, invariants = _GUIDANCE.get(
        primary,
        (
            "Correct the diagnosed candidate-owned source or output protocol and regenerate affected evidence.",
            ("diagnosed_finding_resolved", "candidate_reexecuted"),
        ),
    )
    additional = tuple(
        sorted(item.code for item in adjudication.findings if item.code != primary)
    )
    values: dict[str, Any] = {
        "directive_identifier": f"directive-{attempt_index:03d}",
        "task_identifier": task_identifier,
        "repair_run_identifier": repair_run_identifier,
        "source_attempt_identifier": attempt_identifier,
        "source_manifest_sha256": source_manifest.source_manifest_sha256,
        "source_adjudication_receipt_sha256": adjudication.receipt_content_sha256,
        "primary_finding_code": primary,
        "additional_finding_codes": additional,
        "sanitized_explanations": (guidance,),
        "disposition": "repairable",
        "allowed_edit_paths": tuple(sorted(set(allowed_edit_paths))),
        "prohibited_edit_paths": policy.prohibited_paths,
        "maximum_files_changed": policy.maximum_files_changed,
        "maximum_changed_lines": policy.maximum_changed_lines,
        "maximum_patch_bytes": policy.maximum_patch_bytes,
        "allowed_file_types": policy.allowed_file_types,
        "required_invariants": tuple(sorted(invariants)),
        "required_reverification_gates": (
            "candidate_execution",
            "candidate_protocol",
            "evidence_integrity",
            "scientific_adjudication",
            "security_isolation",
        ),
        "deliberately_withheld": (
            "trusted_exact_energy",
            "trusted_hamiltonian_contents",
            "trusted_result_contents",
            "trusted_scientific_fingerprints",
        ),
        "attempt_number": attempt_index,
        "remaining_attempt_budget": max(policy.maximum_attempts - attempt_index - 1, 0),
        "creation_policy_version": "quantum-repair-directive-policy-v1",
    }
    directive = QuantumRepairDirective.model_validate(
        sealed_values(values, "directive_sha256")
    )
    assert_directive_sanitized(directive)
    return directive


def assert_directive_sanitized(directive: QuantumRepairDirective) -> None:
    rendered = " ".join(
        (
            *directive.sanitized_explanations,
            *directive.required_invariants,
            *directive.required_reverification_gates,
        )
    )
    if _TRUSTED_LEAK.search(rendered):
        raise ValueError("Repair directive failed trusted-answer leakage scan.")
    if any(
        "sha256:" in explanation for explanation in directive.sanitized_explanations
    ):
        raise ValueError(
            "Repair explanations cannot disclose opaque reference identities."
        )
