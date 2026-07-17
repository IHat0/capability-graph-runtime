"""Persisted repeat-and-mutation acceptance for the trusted LiH workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

from .artifacts import write_json_atomic
from .contracts import ManifestEnvelope
from .errors import (
    QuantumDependencyError,
    QuantumIntegrityError,
    QuantumManifestError,
    QuantumPreflightError,
    QuantumTimeoutError,
    QuantumVerificationError,
)
from .manifests import load_manifest, with_bond_distance
from .receipt import QuantumPreflightReceipt, verify_receipt_identities
from .runner import run_trusted_reference

ACCEPTANCE_SCHEMA = "cgr.quantum-preflight-acceptance/1.0.0"
EXIT_SUCCESS = 0
EXIT_SPECIFICATION = 2
EXIT_EXECUTION = 3
EXIT_VERIFICATION = 4
EXIT_REPEAT = 5
EXIT_MUTATION = 6
EXIT_INTEGRITY = 7
EXIT_ENVIRONMENT = 8
EXIT_TIMEOUT = 9
EXIT_OUTPUT = 10


class AcceptanceFailure(QuantumPreflightError):
    def __init__(self, message: str, *, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


Executor = Callable[..., dict[str, Any]]


def run_acceptance(
    manifest: ManifestEnvelope,
    *,
    result_root: Path,
    lock_path: Path,
    image_identifier: str,
    maximum_seconds: int | None = None,
    executor: Executor = run_trusted_reference,
) -> dict[str, Any]:
    """Execute two repeats and a runtime-only 1.7 Angstrom mutation."""
    if not image_identifier or image_identifier == "unrecorded":
        raise AcceptanceFailure("A resolved container image identifier is required.", exit_code=EXIT_ENVIRONMENT)
    acceptance_directory = _next_acceptance_directory(result_root)
    acceptance_directory.mkdir(parents=True)
    (acceptance_directory / "logs").mkdir()
    try:
        repeat_a = executor(
            manifest,
            result_root=acceptance_directory / "run-a",
            lock_path=lock_path,
            image_identifier=image_identifier,
            maximum_seconds=maximum_seconds,
        )
        repeat_b = executor(
            manifest,
            result_root=acceptance_directory / "run-b",
            lock_path=lock_path,
            image_identifier=image_identifier,
            maximum_seconds=maximum_seconds,
        )
        mutation_manifest = with_bond_distance(manifest, 1.7)
        mutation = executor(
            mutation_manifest,
            result_root=acceptance_directory / "mutation-1p7",
            lock_path=lock_path,
            image_identifier=image_identifier,
            maximum_seconds=maximum_seconds,
        )
        repeat_checks = evaluate_repeat_determinism(
            repeat_a,
            repeat_b,
            vqe_tolerance=manifest.experiment.quantum_model.convergence_threshold,
        )
        mutation_checks = evaluate_mutation_sensitivity(repeat_a, mutation)
        integrity_checks = evaluate_evidence_integrity(repeat_a, repeat_b, mutation)
        _require_checks(repeat_checks, EXIT_REPEAT, "repeat determinism")
        _require_checks(mutation_checks, EXIT_MUTATION, "mutation sensitivity")
        _require_checks(integrity_checks, EXIT_INTEGRITY, "evidence integrity")
        warnings = {
            "schema_version": "cgr.quantum-preflight-acceptance-warnings/1.0.0",
            "runs": [
                {
                    "run": label,
                    "fingerprint": summary["compatibility_warnings_sha256"],
                    "status": summary["compatibility_status"],
                    "evidence": _load_payload(
                        Path(summary["receipt_path"]).parent
                        / "compatibility-warnings.json"
                    ),
                }
                for label, summary in (
                    ("run-a", repeat_a),
                    ("run-b", repeat_b),
                    ("mutation-1p7", mutation),
                )
            ],
        }
        write_json_atomic(
            acceptance_directory / "compatibility-warnings.json",
            warnings,
            maximum_bytes=1_000_000,
        )
        report = {
            "schema_version": ACCEPTANCE_SCHEMA,
            "acceptance_identifier": acceptance_directory.name,
            "runs": {
                "run_a": _portable_summary(repeat_a),
                "run_b": _portable_summary(repeat_b),
                "mutation_1p7": _portable_summary(mutation),
            },
            "repeat_determinism": repeat_checks,
            "mutation_sensitivity": mutation_checks,
            "evidence_integrity": integrity_checks,
            "compatibility_warnings": warnings["runs"],
            "authorized": all(item["authorized"] for item in (repeat_a, repeat_b, mutation)),
            "acceptance_passed": True,
        }
        report_path = acceptance_directory / "acceptance-report.json"
        write_json_atomic(report_path, report, maximum_bytes=2_000_000)
        report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
        summary = {
            "schema_version": "cgr.quantum-preflight-acceptance-summary/1.0.0",
            "cgr_git_commit": os.environ.get("CGR_GIT_COMMIT", "unknown"),
            "container_image_identifier": image_identifier,
            "dependency_lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
            "acceptance_identifier": acceptance_directory.name,
            "acceptance_report_sha256": report_sha,
            "repeat_scientific_outcome_sha256": repeat_a["scientific_outcome_sha256"],
            "mutation_scientific_outcome_sha256": mutation["scientific_outcome_sha256"],
            "repeat_determinism_passed": True,
            "mutation_sensitivity_passed": True,
            "evidence_integrity_passed": True,
            "authorized": report["authorized"],
            "acceptance_passed": True,
        }
        write_json_atomic(
            acceptance_directory / "acceptance-summary.json",
            summary,
            maximum_bytes=1_000_000,
        )
        return {**summary, "acceptance_report_path": str(report_path)}
    except AcceptanceFailure:
        raise
    except QuantumPreflightError:
        raise
    except OSError as exc:
        raise AcceptanceFailure(f"Could not persist acceptance output: {exc}", exit_code=EXIT_OUTPUT) from exc


def evaluate_repeat_determinism(
    run_a: dict[str, Any],
    run_b: dict[str, Any],
    *,
    vqe_tolerance: float,
) -> dict[str, bool]:
    identity_fields = (
        "experiment_fingerprint",
        "structure_sha256",
        "qcschema_sha256",
        "electronic_problem_sha256",
        "active_space_sha256",
        "fermionic_hamiltonian_sha256",
        "qubit_hamiltonian_sha256",
        "exact_scientific_result_sha256",
        "vqe_scientific_result_sha256",
        "scientific_outcome_sha256",
        "optimized_parameters_sha256",
        "optimization_trace_scientific_sha256",
    )
    checks = {f"same_{field}": run_a.get(field) == run_b.get(field) for field in identity_fields}
    checks.update(
        {
            "run_a_authorized": run_a.get("authorized") is True,
            "run_b_authorized": run_b.get("authorized") is True,
            "exact_energy_within_1e_12": abs(
                run_a.get("exact_total_energy_hartree", float("inf"))
                - run_b.get("exact_total_energy_hartree", 0.0)
            )
            <= 1e-12,
            "vqe_energy_within_configured_tolerance": abs(
                run_a.get("vqe_total_energy_hartree", float("inf"))
                - run_b.get("vqe_total_energy_hartree", 0.0)
            )
            <= vqe_tolerance,
            "full_results_differ_only_by_execution_metadata": _results_equivalent_except_duration(
                run_a, run_b
            ),
        }
    )
    return checks


def evaluate_mutation_sensitivity(
    baseline: dict[str, Any], mutation: dict[str, Any]
) -> dict[str, bool]:
    fields = (
        "experiment_fingerprint",
        "structure_sha256",
        "qcschema_sha256",
        "electronic_problem_sha256",
        "fermionic_hamiltonian_sha256",
        "qubit_hamiltonian_sha256",
        "exact_scientific_result_sha256",
        "vqe_scientific_result_sha256",
        "scientific_outcome_sha256",
    )
    checks = {f"different_{field}": baseline.get(field) != mutation.get(field) for field in fields}
    checks.update(
        {
            "mutation_authorized": mutation.get("authorized") is True,
            "exact_energy_changed_more_than_1e_10": abs(
                baseline.get("exact_total_energy_hartree", 0.0)
                - mutation.get("exact_total_energy_hartree", 0.0)
            )
            > 1e-10,
        }
    )
    return checks


def evaluate_evidence_integrity(
    run_a: dict[str, Any], run_b: dict[str, Any], mutation: dict[str, Any]
) -> dict[str, bool]:
    del run_b
    own_a = _receipt_bundle(run_a)
    own_mutation = _receipt_bundle(mutation)
    own_failures = verify_receipt_identities(**own_a)
    mutation_failures = verify_receipt_identities(**own_mutation)
    cross_result_failures = verify_receipt_identities(
        own_a["receipt"],
        exact_result=own_mutation["exact_result"],
        vqe_result=own_mutation["vqe_result"],
        exact_result_pointer=own_mutation["exact_result_pointer"],
        vqe_result_pointer=own_mutation["vqe_result_pointer"],
        expected_outcome=own_a["expected_outcome"],
    )
    cross_hamiltonian_failures = verify_receipt_identities(
        own_mutation["receipt"],
        exact_result=own_a["exact_result"],
        vqe_result=own_a["vqe_result"],
        exact_result_pointer=own_a["exact_result_pointer"],
        vqe_result_pointer=own_a["vqe_result_pointer"],
        expected_outcome=own_mutation["expected_outcome"],
    )
    substituted_pointer = own_a["exact_result_pointer"].model_copy(
        update={"content_sha256": "f" * 64}
    )
    content_substitution = verify_receipt_identities(
        own_a["receipt"],
        exact_result=own_a["exact_result"],
        vqe_result=own_a["vqe_result"],
        exact_result_pointer=substituted_pointer,
        vqe_result_pointer=own_a["vqe_result_pointer"],
        expected_outcome=own_a["expected_outcome"],
    )
    forged = deepcopy(own_a["exact_result"])
    forged["scientific_result_sha256"] = "e" * 64
    scientific_substitution = verify_receipt_identities(
        own_a["receipt"],
        exact_result=forged,
        vqe_result=own_a["vqe_result"],
        exact_result_pointer=own_a["exact_result_pointer"],
        vqe_result_pointer=own_a["vqe_result_pointer"],
        expected_outcome=own_a["expected_outcome"],
    )
    return {
        "own_receipts_recompute": not own_failures and not mutation_failures,
        "cross_link_1p6_receipt_to_1p7_results_rejected": bool(cross_result_failures),
        "cross_link_1p7_receipt_to_1p6_hamiltonian_results_rejected": bool(
            cross_hamiltonian_failures
        ),
        "run_specific_content_substitution_rejected": bool(content_substitution),
        "scientific_identity_substitution_rejected": bool(scientific_substitution),
    }


def _receipt_bundle(summary: dict[str, Any]) -> dict[str, Any]:
    receipt_path = Path(summary["receipt_path"])
    receipt = QuantumPreflightReceipt.model_validate(_load_payload(receipt_path))
    pointers = {item.artifact_identifier: item for item in receipt.artifacts}
    exact_pointer = pointers["exact_result"]
    vqe_pointer = pointers["vqe_result"]
    return {
        "receipt": receipt,
        "exact_result": _load_payload(receipt_path.parent / "exact-result.json"),
        "vqe_result": _load_payload(receipt_path.parent / "vqe-result.json"),
        "exact_result_pointer": exact_pointer,
        "vqe_result_pointer": vqe_pointer,
        "expected_outcome": receipt.scientific_outcome,
    }


def _results_equivalent_except_duration(run_a: dict[str, Any], run_b: dict[str, Any]) -> bool:
    try:
        left = _receipt_bundle(run_a)
        right = _receipt_bundle(run_b)
    except (KeyError, OSError, ValueError):
        return False
    for name in ("exact_result", "vqe_result"):
        left_value = deepcopy(left[name])
        right_value = deepcopy(right[name])
        left_value["execution_result"].pop("duration_seconds", None)
        right_value["execution_result"].pop("duration_seconds", None)
        left_value.pop("execution_metadata", None)
        right_value.pop("execution_metadata", None)
        if left_value != right_value:
            return False
    return True


def _load_payload(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("payload"), dict):
        raise ValueError(f"Invalid artifact document: {path.name}")
    return value["payload"]


def _portable_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in summary.items()
        if key not in {"receipt_path", "run_id"}
    }


def _require_checks(checks: dict[str, bool], exit_code: int, label: str) -> None:
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise AcceptanceFailure(
            f"{label} failed: {', '.join(failed)}", exit_code=exit_code
        )


def _next_acceptance_directory(root: Path) -> Path:
    base = root / "quantum-preflight-acceptance"
    base.mkdir(parents=True, exist_ok=True)
    for number in range(1, 1_000_000):
        candidate = base / f"acceptance-{number:03d}"
        if not candidate.exists():
            return candidate
    raise AcceptanceFailure("No acceptance identifier is available.", exit_code=EXIT_OUTPUT)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run persisted trusted LiH repeat-and-mutation acceptance.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--lock-file", type=Path, default=Path("requirements/quantum-preflight.lock"))
    parser.add_argument("--image-identifier", default=os.environ.get("CGR_QUANTUM_IMAGE_ID"))
    parser.add_argument("--max-seconds", type=int)
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        summary = run_acceptance(
            manifest,
            result_root=args.result_root,
            lock_path=args.lock_file,
            image_identifier=args.image_identifier or "",
            maximum_seconds=args.max_seconds,
        )
    except QuantumManifestError as exc:
        return _print_failure(exc, EXIT_SPECIFICATION)
    except QuantumDependencyError as exc:
        return _print_failure(exc, EXIT_ENVIRONMENT)
    except QuantumTimeoutError as exc:
        return _print_failure(exc, EXIT_TIMEOUT)
    except QuantumVerificationError as exc:
        return _print_failure(exc, EXIT_VERIFICATION)
    except QuantumIntegrityError as exc:
        return _print_failure(exc, EXIT_INTEGRITY)
    except OSError as exc:
        return _print_failure(exc, EXIT_OUTPUT)
    except AcceptanceFailure as exc:
        return _print_failure(exc, exc.exit_code)
    except QuantumPreflightError as exc:
        return _print_failure(exc, getattr(exc, "exit_code", EXIT_EXECUTION))
    except Exception as exc:
        return _print_failure(exc, EXIT_EXECUTION)
    print(json.dumps(summary, sort_keys=True))
    print(f"acceptance_report={summary['acceptance_report_path']}", file=sys.stderr)
    return EXIT_SUCCESS


def _print_failure(exc: Exception, exit_code: int) -> int:
    print(
        json.dumps(
            {"acceptance_passed": False, "authorized": False, "error": str(exc), "exit_code": exit_code},
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
