"""Stable candidate failure taxonomy and deterministic primary selection."""

from __future__ import annotations

from dataclasses import dataclass
from .contracts import CandidateFinding, FindingCategory, FindingPhase, RepairDirective


@dataclass(frozen=True)
class FindingDefinition:
    category: FindingCategory
    phase: FindingPhase
    priority: int
    action: str
    retryable: bool = False
    reconstruct: bool = False
    edit: bool = True


_DEFINITIONS: dict[str, FindingDefinition] = {
    "candidate_output_path_violation": FindingDefinition(
        "security", "candidate_output_collection", 10, "remove_unsafe_output_path"
    ),
    "candidate_network_attempt": FindingDefinition(
        "security", "candidate_execution", 11, "remove_network_access"
    ),
    "candidate_forbidden_dependency": FindingDefinition(
        "security", "candidate_execution", 12, "remove_forbidden_dependency"
    ),
    "candidate_untrusted_authorization_claim": FindingDefinition(
        "authorization", "authorization", 13, "remove_candidate_authorization_claim"
    ),
    "candidate_timeout": FindingDefinition(
        "resource",
        "candidate_execution",
        20,
        "bound_candidate_execution",
        retryable=True,
    ),
    "candidate_syntax_error": FindingDefinition(
        "execution", "candidate_execution", 30, "correct_python_syntax"
    ),
    "candidate_import_error": FindingDefinition(
        "execution", "candidate_execution", 31, "correct_candidate_imports"
    ),
    "candidate_runtime_error": FindingDefinition(
        "execution", "candidate_execution", 32, "correct_runtime_failure"
    ),
    "candidate_output_missing": FindingDefinition(
        "protocol", "candidate_output_collection", 40, "emit_required_candidate_summary"
    ),
    "candidate_protocol_invalid": FindingDefinition(
        "protocol",
        "candidate_protocol_validation",
        41,
        "conform_to_candidate_output_schema",
    ),
    "candidate_evidence_incomplete": FindingDefinition(
        "protocol",
        "candidate_protocol_validation",
        42,
        "emit_complete_scientific_evidence",
        reconstruct=True,
    ),
    "candidate_coordinate_unit_mismatch": FindingDefinition(
        "structure",
        "scientific_identity_validation",
        50,
        "correct_coordinate_units",
        reconstruct=True,
    ),
    "candidate_structure_mismatch": FindingDefinition(
        "structure",
        "scientific_identity_validation",
        51,
        "correct_molecular_structure",
        reconstruct=True,
    ),
    "candidate_charge_mismatch": FindingDefinition(
        "structure",
        "scientific_identity_validation",
        52,
        "correct_molecular_charge",
        reconstruct=True,
    ),
    "candidate_multiplicity_mismatch": FindingDefinition(
        "structure",
        "scientific_identity_validation",
        53,
        "correct_spin_multiplicity",
        reconstruct=True,
    ),
    "candidate_basis_mismatch": FindingDefinition(
        "electronic_problem",
        "scientific_identity_validation",
        54,
        "correct_basis_construction",
        reconstruct=True,
    ),
    "candidate_active_space_mismatch": FindingDefinition(
        "active_space",
        "scientific_identity_validation",
        55,
        "correct_active_space_construction",
        reconstruct=True,
    ),
    "candidate_mapper_mismatch": FindingDefinition(
        "hamiltonian",
        "hamiltonian_validation",
        56,
        "correct_qubit_mapper",
        reconstruct=True,
    ),
    "candidate_fermionic_hamiltonian_mismatch": FindingDefinition(
        "hamiltonian",
        "hamiltonian_validation",
        60,
        "recompute_fermionic_hamiltonian",
        reconstruct=True,
    ),
    "candidate_qubit_hamiltonian_mismatch": FindingDefinition(
        "hamiltonian",
        "hamiltonian_validation",
        61,
        "recompute_qubit_hamiltonian",
        reconstruct=True,
    ),
    "candidate_nuclear_repulsion_missing": FindingDefinition(
        "result",
        "result_validation",
        70,
        "record_nuclear_repulsion_energy",
        reconstruct=True,
    ),
    "candidate_total_energy_semantics_invalid": FindingDefinition(
        "result",
        "result_validation",
        71,
        "correct_total_energy_semantics",
        reconstruct=True,
    ),
    "candidate_vqe_not_converged": FindingDefinition(
        "solver", "result_validation", 72, "correct_vqe_convergence", retryable=True
    ),
    "candidate_energy_disagreement": FindingDefinition(
        "result",
        "result_validation",
        73,
        "correct_scientific_computation",
        reconstruct=True,
    ),
    "candidate_lineage_mismatch": FindingDefinition(
        "lineage",
        "evidence_integrity_validation",
        80,
        "rebuild_candidate_lineage",
        reconstruct=True,
    ),
    "candidate_content_hash_mismatch": FindingDefinition(
        "integrity",
        "evidence_integrity_validation",
        81,
        "recompute_artifact_content_hashes",
    ),
    "candidate_scientific_identity_mismatch": FindingDefinition(
        "integrity",
        "evidence_integrity_validation",
        82,
        "recompute_scientific_result_identity",
        reconstruct=True,
    ),
}


def finding(
    code: str,
    explanation: str,
    *,
    subject_artifact: str | None = None,
    expected: str | int | float | bool | None = None,
    observed: str | int | float | bool | None = None,
) -> CandidateFinding:
    definition = _DEFINITIONS[code]
    required = _required_evidence(code)
    return CandidateFinding(
        code=code,
        category=definition.category,
        phase=definition.phase,
        subject_artifact=subject_artifact,
        expected=expected,
        observed=observed,
        explanation=explanation,
        repair_directive=RepairDirective(
            action=definition.action,
            target=(
                "candidate output protocol"
                if definition.category in {"protocol", "integrity", "lineage"}
                else "candidate source"
            ),
            required_evidence_after_edit=required,
        ),
        retryable=definition.retryable,
        scientific_reconstruction_required=definition.reconstruct,
        source_edit_required=definition.edit,
    )


def primary_failure(findings: tuple[CandidateFinding, ...]) -> str | None:
    blocking = [item for item in findings if item.blocking]
    if not blocking:
        return None
    return min(
        blocking, key=lambda item: (_DEFINITIONS[item.code].priority, item.code)
    ).code


def ordered_findings(values: list[CandidateFinding]) -> tuple[CandidateFinding, ...]:
    unique = {item.fingerprint: item for item in values}
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                _DEFINITIONS[item.code].priority,
                item.code,
                item.fingerprint,
            ),
        )
    )


def known_finding_codes() -> tuple[str, ...]:
    return tuple(sorted(_DEFINITIONS))


def _required_evidence(code: str) -> tuple[str, ...]:
    if "hamiltonian" in code or code in {
        "candidate_active_space_mismatch",
        "candidate_mapper_mismatch",
    }:
        return ("hamiltonian_recomputed", "result_recomputed")
    if code.startswith("candidate_") and any(
        token in code
        for token in ("structure", "coordinate", "charge", "multiplicity", "basis")
    ):
        return (
            "scientific_specification_matches_manifest",
            "hamiltonian_recomputed",
            "result_recomputed",
        )
    if code in {"candidate_energy_disagreement", "candidate_vqe_not_converged"}:
        return ("optimization_trace_present", "result_recomputed")
    return ("blocking_finding_resolved",)
