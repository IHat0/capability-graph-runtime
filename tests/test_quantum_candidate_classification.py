from __future__ import annotations

from pathlib import Path

import pytest

from cgr.quantum_candidate.benchmark import load_benchmark_manifest
from cgr.quantum_candidate.findings import (
    finding,
    known_finding_codes,
    ordered_findings,
    primary_failure,
)
from cgr.quantum_candidate.sandbox import _execution_category


def test_failure_taxonomy_is_complete_and_repair_directives_are_deterministic() -> None:
    codes = known_finding_codes()
    assert len(codes) == 27
    first = finding("candidate_active_space_mismatch", "active space differs")
    second = finding("candidate_active_space_mismatch", "active space differs")
    assert first == second
    assert first.repair_directive.action == "correct_active_space_construction"
    assert (
        "hamiltonian_recomputed" in first.repair_directive.required_evidence_after_edit
    )
    assert first.scientific_reconstruction_required is True


def test_primary_failure_selection_is_stable_and_security_first() -> None:
    values = [
        finding("candidate_energy_disagreement", "energy differs"),
        finding("candidate_network_attempt", "network attempted"),
        finding("candidate_runtime_error", "runtime failed"),
    ]
    assert primary_failure(ordered_findings(values)) == "candidate_network_attempt"
    assert (
        primary_failure(ordered_findings(list(reversed(values))))
        == "candidate_network_attempt"
    )


@pytest.mark.parametrize(
    ("exit_code", "timed_out", "stderr", "violated", "expected"),
    [
        (0, False, "", False, "completed"),
        (1, False, "SyntaxError: invalid", False, "syntax_error"),
        (1, False, "ModuleNotFoundError: missing", False, "import_error"),
        (1, False, "ValueError: bad", False, "runtime_error"),
        (None, True, "", False, "timeout"),
        (0, False, "", True, "output_violation"),
    ],
)
def test_execution_failure_classification(
    exit_code: int | None,
    timed_out: bool,
    stderr: str,
    violated: bool,
    expected: str,
) -> None:
    assert (
        _execution_category(
            exit_code=exit_code,
            timed_out=timed_out,
            stderr=stderr,
            output_violated=violated,
        )
        == expected
    )


def test_benchmark_manifest_enumerates_control_and_all_broken_workflows() -> None:
    manifest = load_benchmark_manifest(
        Path("benchmark-manifests/quantum-candidate/lih-candidate-benchmark-v1.json")
    )
    assert len(manifest.cases) == 27
    assert sum(case.authorization_expected for case in manifest.cases) == 1
    assert {case.case_identifier for case in manifest.cases} == {
        path.name
        for path in Path("benchmark-fixtures/quantum-candidate-v1").iterdir()
        if path.is_dir() and path.name != "_support"
    }
    assert all(
        case.expected_primary_finding is not None
        for case in manifest.cases
        if not case.authorization_expected
    )
