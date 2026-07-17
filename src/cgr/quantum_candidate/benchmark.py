"""Deterministic broken-workflow benchmark orchestration and reporting."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from cgr.quantum_preflight.artifacts import write_json_atomic
from cgr.quantum_preflight.manifests import load_manifest

from .adjudication import adjudicate_candidate
from .contracts import CandidateBenchmarkManifest, CandidateSandboxPolicy
from .protocol import source_tree_sha256, write_public_experiment
from .sandbox import execute_candidate
from .trusted import load_verified_trusted_reference


def load_benchmark_manifest(path: Path) -> CandidateBenchmarkManifest:
    value = json.loads(path.read_text(encoding="utf-8"))
    return CandidateBenchmarkManifest.model_validate(value)


def run_benchmark(
    *,
    benchmark_manifest_path: Path,
    trusted_reference_directory: Path,
    result_root: Path,
    fixture_root: Path,
    candidate_image_identifier: str,
    candidate_lock_path: Path,
    policy: CandidateSandboxPolicy | None = None,
) -> dict[str, Any]:
    policy = policy or CandidateSandboxPolicy()
    benchmark = load_benchmark_manifest(benchmark_manifest_path)
    declared_manifest_path = (
        benchmark_manifest_path.parent / benchmark.public_experiment_manifest
    ).resolve()
    declared = load_manifest(declared_manifest_path)
    trusted = load_verified_trusted_reference(
        trusted_reference_directory,
        declared.experiment,
    )
    lock_sha = hashlib.sha256(candidate_lock_path.read_bytes()).hexdigest()
    directory = _next_benchmark_directory(result_root)
    directory.mkdir(parents=True)
    cases_root = directory / "cases"
    cases_root.mkdir()
    write_json_atomic(
        directory / "benchmark-manifest.json",
        benchmark.model_dump(mode="json"),
        maximum_bytes=2 * 1024 * 1024,
    )
    write_json_atomic(
        directory / "trusted-reference.json",
        {
            "authorized": True,
            "receipt_content_sha256": trusted.receipt_content_sha256,
            "scientific_outcome_sha256": trusted.receipt.scientific_outcome_sha256,
        },
        maximum_bytes=4096,
    )
    public_input = directory / "public-experiment.json"
    input_sha = write_public_experiment(
        public_input,
        declared,
        candidate_dependency_lock_sha256=lock_sha,
    )
    case_reports: list[dict[str, Any]] = []
    for case in benchmark.cases:
        source_directory = (fixture_root / case.candidate_directory).resolve()
        if fixture_root.resolve() not in source_directory.parents:
            raise ValueError("Candidate fixture escapes the benchmark fixture root.")
        case_directory = cases_root / case.case_identifier
        case_directory.mkdir()
        bundle = case_directory / "candidate-bundle"
        _assemble_candidate_bundle(source_directory, fixture_root / "_support", bundle)
        write_json_atomic(
            case_directory / "source-manifest.json",
            {
                "case_identifier": case.case_identifier,
                "source_tree_sha256": source_tree_sha256(bundle),
                "files": sorted(
                    path.relative_to(bundle).as_posix()
                    for path in bundle.rglob("*")
                    if path.is_file()
                ),
            },
            maximum_bytes=128 * 1024,
        )
        execution, package = execute_candidate(
            candidate_identifier=case.case_identifier,
            image_identifier=candidate_image_identifier,
            input_manifest=public_input,
            input_manifest_sha256=input_sha,
            candidate_directory=bundle,
            output_directory=case_directory / "output",
            evidence_directory=case_directory,
            policy=policy.model_copy(
                update={"wall_clock_seconds": case.maximum_runtime_seconds}
            ),
        )
        receipt = adjudicate_candidate(
            experiment=declared.experiment,
            execution=execution,
            package=package,
            trusted=trusted,
            candidate_dependency_lock_sha256=lock_sha,
        )
        write_json_atomic(
            case_directory / "adjudication-report.json",
            {
                "candidate_identifier": case.case_identifier,
                "authorized": receipt.authorized,
                "primary_failure_code": receipt.primary_failure_code,
                "findings": [item.model_dump(mode="json") for item in receipt.findings],
            },
            maximum_bytes=2 * 1024 * 1024,
        )
        write_json_atomic(
            case_directory / "receipt.json",
            receipt.model_dump(mode="json"),
            maximum_bytes=4 * 1024 * 1024,
        )
        observed_codes = {item.code for item in receipt.findings}
        authorization_matches = receipt.authorized == case.authorization_expected
        diagnosis_matches = (
            receipt.primary_failure_code == case.expected_primary_finding
        )
        required_present = set(case.required_additional_findings).issubset(
            observed_codes
        )
        forbidden_absent = not set(case.forbidden_findings).intersection(observed_codes)
        execution_matches = (
            execution.execution_category == case.expected_execution_category
        )
        case_reports.append(
            {
                "case_identifier": case.case_identifier,
                "authorization_expected": case.authorization_expected,
                "authorized": receipt.authorized,
                "primary_failure_expected": case.expected_primary_finding,
                "primary_failure_observed": receipt.primary_failure_code,
                "execution_category_expected": case.expected_execution_category,
                "execution_category_observed": execution.execution_category,
                "authorization_matches": authorization_matches,
                "diagnosis_matches": diagnosis_matches,
                "required_findings_present": required_present,
                "forbidden_findings_absent": forbidden_absent,
                "execution_category_matches": execution_matches,
                "network_disabled": execution.network_disabled,
                "trusted_evidence_exposed": execution.trusted_evidence_exposed,
                "elapsed_seconds": execution.elapsed_seconds,
                "output_bytes": execution.output_bytes,
                "finding_categories": sorted(
                    {item.category for item in receipt.findings}
                ),
            }
        )
    total = len(benchmark.cases)
    false_accepts = sum(
        1
        for report in case_reports
        if report["authorized"] and not report["authorization_expected"]
    )
    false_rejects = sum(
        1
        for report in case_reports
        if not report["authorized"] and report["authorization_expected"]
    )
    expectation_mismatches = sum(
        1
        for report in case_reports
        if not all(
            report[key]
            for key in (
                "authorization_matches",
                "diagnosis_matches",
                "required_findings_present",
                "forbidden_findings_absent",
                "execution_category_matches",
                "network_disabled",
            )
        )
        or report["trusted_evidence_exposed"]
    )
    summary = {
        "schema_version": "cgr.quantum-candidate-benchmark-summary/1.0.0",
        "benchmark_identifier": benchmark.benchmark_identifier,
        "candidate_image_identifier": candidate_image_identifier,
        "candidate_dependency_lock_sha256": lock_sha,
        "trusted_reference_receipt_sha256": trusted.receipt_content_sha256,
        "total_cases": total,
        "authorized_controls": sum(
            1
            for item in case_reports
            if item["authorization_expected"] and item["authorized"]
        ),
        "correctly_rejected_negatives": sum(
            1
            for item in case_reports
            if not item["authorization_expected"] and not item["authorized"]
        ),
        "false_accepts": false_accepts,
        "false_rejects": false_rejects,
        "diagnosis_matches": sum(
            1 for item in case_reports if item["diagnosis_matches"]
        ),
        "diagnosis_mismatches": sum(
            1 for item in case_reports if not item["diagnosis_matches"]
        ),
        "expectation_mismatches": expectation_mismatches,
        "missing_cases": total - len(case_reports),
        "skipped_cases": 0,
        "network_enabled_cases": sum(
            1 for item in case_reports if not item["network_disabled"]
        ),
        "trusted_evidence_exposure_cases": sum(
            1 for item in case_reports if item["trusted_evidence_exposed"]
        ),
        "timeouts": sum(
            1
            for item in case_reports
            if item["execution_category_observed"] == "timeout"
        ),
        "runtime_failures": sum(
            1
            for item in case_reports
            if item["execution_category_observed"]
            in {"syntax_error", "import_error", "runtime_error"}
        ),
        "scientific_mismatches": sum(
            1
            for item in case_reports
            if set(item["finding_categories"])
            & {
                "scientific_specification",
                "structure",
                "electronic_problem",
                "active_space",
                "hamiltonian",
                "solver",
                "result",
            }
        ),
        "evidence_integrity_failures": sum(
            1
            for item in case_reports
            if set(item["finding_categories"]) & {"lineage", "integrity", "protocol"}
        ),
        "security_violations": sum(
            1 for item in case_reports if "security" in item["finding_categories"]
        ),
        "benchmark_passed": false_accepts == 0
        and false_rejects == 0
        and expectation_mismatches == 0
        and len(case_reports) == total,
    }
    report = {
        "schema_version": "cgr.quantum-candidate-benchmark-report/1.0.0",
        "summary": summary,
        "cases": case_reports,
    }
    write_json_atomic(
        directory / "benchmark-report.json", report, maximum_bytes=8 * 1024 * 1024
    )
    write_json_atomic(
        directory / "benchmark-summary.json", summary, maximum_bytes=256 * 1024
    )
    return {
        **summary,
        "benchmark_report_path": str(directory / "benchmark-report.json"),
        "benchmark_summary_path": str(directory / "benchmark-summary.json"),
    }


def _assemble_candidate_bundle(source: Path, support: Path, destination: Path) -> None:
    if not (source / "main.py").is_file():
        raise ValueError(f"Candidate fixture {source.name} has no main.py.")
    destination.mkdir()
    for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
        if path.name == "case.json" or path.is_dir():
            continue
        if path.is_symlink():
            raise ValueError("Candidate source bundles may not contain symbolic links.")
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    support_file = support / "standalone_candidate.py"
    if support_file.is_file():
        shutil.copy2(support_file, destination / "standalone_candidate.py")


def _next_benchmark_directory(root: Path) -> Path:
    base = root / "quantum-candidate-benchmark"
    base.mkdir(parents=True, exist_ok=True)
    for number in range(1, 1_000_000):
        candidate = base / f"benchmark-{number:03d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("No quantum-candidate benchmark identifier is available.")
