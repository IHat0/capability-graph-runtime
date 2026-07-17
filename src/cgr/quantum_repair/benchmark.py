"""Separate 30-case repair benchmark adapter and acceptance report."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from cgr.quantum_candidate.contracts import CandidateAdjudicationReceipt
from cgr.quantum_candidate.contracts import CandidateSandboxPolicy
from cgr.quantum_candidate.trusted import load_verified_trusted_reference
from cgr.quantum_preflight.artifacts import write_json_atomic
from cgr.quantum_preflight.manifests import load_manifest

from .benchmark_provider import (
    ReviewedBenchmarkRepairProvider,
    materialize_benchmark_source,
)
from .contracts import (
    QuantumRepairBenchmarkManifest,
    QuantumRepairDirective,
    QuantumRepairPolicy,
)
from .orchestrator import run_repair
from .persistence import create_source_manifest, read_json
from .replay import verify_repair_run


def load_repair_benchmark(path: Path) -> QuantumRepairBenchmarkManifest:
    return QuantumRepairBenchmarkManifest.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def run_repair_benchmark(
    *,
    benchmark_manifest_path: Path,
    diagnosis_manifest_path: Path,
    trusted_reference_directory: Path,
    result_root: Path,
    candidate_image_identifier: str,
    candidate_lock_path: Path,
    fixture_root: Path,
    diagnosis_support_path: Path,
) -> dict[str, Any]:
    benchmark = load_repair_benchmark(benchmark_manifest_path)
    diagnosis_hash = hashlib.sha256(diagnosis_manifest_path.read_bytes()).hexdigest()
    if diagnosis_hash != benchmark.diagnosis_benchmark_manifest_sha256:
        raise ValueError("Frozen diagnosis benchmark manifest changed.")
    public_path = (
        benchmark_manifest_path.parent / benchmark.public_experiment_manifest
    ).resolve()
    public_manifest = load_manifest(public_path)
    trusted = load_verified_trusted_reference(
        trusted_reference_directory, public_manifest.experiment
    )
    directory = _next_benchmark_directory(result_root)
    directory.mkdir(parents=True)
    write_json_atomic(
        directory / "benchmark-manifest.json",
        benchmark.model_dump(mode="json"),
        maximum_bytes=2 * 1024 * 1024,
    )
    cases_root = directory / "cases"
    cases_root.mkdir()
    materialized: dict[str, Path] = {}
    source_hashes: dict[str, str] = {}
    for case in benchmark.cases:
        case_root = cases_root / case.case_identifier
        case_root.mkdir()
        source = case_root / "source-initial"
        materialize_benchmark_source(
            template_root=fixture_root / "_template",
            support_root=fixture_root / "_support",
            diagnosis_support=diagnosis_support_path,
            destination=source,
            candidate_identifier=case.case_identifier,
            defects=case.initial_defects,
        )
        manifest = create_source_manifest(source, case.case_identifier)
        write_json_atomic(
            case_root / "source-initial-manifest.json",
            manifest.model_dump(mode="json"),
            maximum_bytes=512 * 1024,
        )
        materialized[case.case_identifier] = source
        source_hashes[case.case_identifier] = manifest.source_manifest_sha256
    control_hash = source_hashes["valid-control"]
    reports: list[dict[str, Any]] = []
    for case in benchmark.cases:
        case_root = cases_root / case.case_identifier
        provider = ReviewedBenchmarkRepairProvider(
            public_manifest.experiment.model_dump(mode="json")
        )

        def policy_for_attempt(
            attempt_index: int,
            expected: tuple[str, ...] = case.expected_findings,
        ) -> CandidateSandboxPolicy:
            short_timeout = (
                attempt_index == 0
                and bool(expected)
                and expected[0] == "candidate_timeout"
            )
            return CandidateSandboxPolicy(wall_clock_seconds=1 if short_timeout else 90)

        result = run_repair(
            task_identifier=case.case_identifier,
            candidate_source=materialized[case.case_identifier],
            public_manifest=public_manifest,
            trusted=trusted,
            result_root=case_root,
            candidate_image_identifier=candidate_image_identifier,
            candidate_lock_path=candidate_lock_path,
            provider=provider,
            repair_policy=QuantumRepairPolicy(maximum_attempts=case.expected_attempts),
            prohibited_source_hashes=(
                set() if case.authorized_without_repair else {control_hash}
            ),
            sandbox_policy_factory=policy_for_attempt,
        )
        run_directory = Path(result["repair_run_directory"])
        observed_findings: list[str | None] = []
        intermediate_authorizations = 0
        network_enabled = 0
        trusted_exposure = 0
        directive_matches = True
        for index in range(result["attempts"]):
            attempt_root = run_directory / "attempts" / f"attempt-{index:03d}"
            receipt = CandidateAdjudicationReceipt.model_validate(
                read_json(attempt_root / "adjudication" / "receipt.json")
            )
            if index + 1 < result["attempts"] and receipt.authorized:
                intermediate_authorizations += 1
            observed_findings.append(receipt.primary_failure_code)
            execution = read_json(
                attempt_root / "candidate-execution" / "execution.json"
            )
            network_enabled += int(not execution["network_disabled"])
            trusted_exposure += int(execution["trusted_evidence_exposed"])
            directive_path = attempt_root / "repair-directive.json"
            if (
                receipt.primary_failure_code is not None
                and index + 1 < result["attempts"]
            ):
                directive = QuantumRepairDirective.model_validate(
                    read_json(directive_path)
                )
                directive_matches &= (
                    directive.primary_finding_code == receipt.primary_failure_code
                )
            else:
                directive_matches &= not directive_path.exists()
        expected_sequence = list(case.expected_findings)
        observed_sequence = [item for item in observed_findings if item is not None]
        replay = verify_repair_run(run_directory)
        report = {
            "case_identifier": case.case_identifier,
            "expected_attempts": case.expected_attempts,
            "observed_attempts": result["attempts"],
            "expected_findings": expected_sequence,
            "observed_findings": observed_sequence,
            "initially_authorized": observed_findings[0] is None,
            "finally_authorized": result["authorized"],
            "provider_invocations": provider.invocations,
            "diagnosis_matches": observed_sequence == expected_sequence,
            "attempt_count_matches": result["attempts"] == case.expected_attempts,
            "provider_count_matches": provider.invocations
            == max(case.expected_attempts - 1, 0),
            "directive_matches": directive_matches,
            "false_intermediate_authorizations": intermediate_authorizations,
            "network_enabled_executions": network_enabled,
            "trusted_evidence_exposure_attempts": trusted_exposure,
            "replay_verified": replay["replay_verified"],
            "terminal_status": result["terminal_status"],
        }
        reports.append(report)
        write_json_atomic(
            case_root / "case-report.json", report, maximum_bytes=512 * 1024
        )
    total = len(benchmark.cases)
    controls = [item for item in reports if item["case_identifier"] == "valid-control"]
    repaired = [item for item in reports if item["case_identifier"] != "valid-control"]
    expectation_failures = sum(
        1
        for item in reports
        if not all(
            item[key]
            for key in (
                "diagnosis_matches",
                "attempt_count_matches",
                "provider_count_matches",
                "directive_matches",
                "replay_verified",
            )
        )
        or not item["finally_authorized"]
        or item["false_intermediate_authorizations"]
        or item["network_enabled_executions"]
        or item["trusted_evidence_exposure_attempts"]
    )
    summary = {
        "schema_version": "cgr.quantum-repair-benchmark-summary/1.0.0",
        "repair_benchmark_passed": expectation_failures == 0 and total == 30,
        "total_cases": total,
        "controls_authorized_without_repair": sum(
            1
            for item in controls
            if item["initially_authorized"] and item["finally_authorized"]
        ),
        "initially_rejected_cases": sum(
            1 for item in repaired if not item["initially_authorized"]
        ),
        "eventually_authorized_repaired_cases": sum(
            1 for item in repaired if item["finally_authorized"]
        ),
        "diagnosis_mismatches": sum(
            1 for item in reports if not item["diagnosis_matches"]
        ),
        "directive_mismatches": sum(
            1 for item in reports if not item["directive_matches"]
        ),
        "patch_policy_failures": sum(
            1 for item in reports if item["terminal_status"] == "patch_rejected"
        ),
        "false_intermediate_authorizations": sum(
            item["false_intermediate_authorizations"] for item in reports
        ),
        "receipt_verification_failures": sum(
            1 for item in reports if not item["replay_verified"]
        ),
        "network_enabled_executions": sum(
            item["network_enabled_executions"] for item in reports
        ),
        "trusted_evidence_exposure_cases": sum(
            1 for item in reports if item["trusted_evidence_exposure_attempts"]
        ),
        "missing_cases": 30 - total,
        "skipped_cases": 0,
    }
    write_json_atomic(
        directory / "repair-benchmark-summary.json", summary, maximum_bytes=512 * 1024
    )
    write_json_atomic(
        directory / "repair-benchmark-report.json",
        {"summary": summary, "cases": reports},
        maximum_bytes=8 * 1024 * 1024,
    )
    return {
        **summary,
        "repair_benchmark_report_path": str(directory / "repair-benchmark-report.json"),
        "repair_benchmark_summary_path": str(
            directory / "repair-benchmark-summary.json"
        ),
    }


def _next_benchmark_directory(result_root: Path) -> Path:
    base = result_root / "quantum-repair-benchmark"
    base.mkdir(parents=True, exist_ok=True)
    for index in range(1, 1_000_000):
        candidate = base / f"benchmark-{index:03d}"
        if not candidate.exists():
            return candidate
    raise ValueError("No repair benchmark identifier remains available.")
