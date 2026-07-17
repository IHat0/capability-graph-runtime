"""Stable repair, benchmark, and replay CLIs with categorized exit status."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cgr.quantum_candidate.trusted import load_verified_trusted_reference
from cgr.quantum_preflight.manifests import load_manifest

from .benchmark import run_repair_benchmark
from .benchmark_provider import ReviewedBenchmarkRepairProvider
from .orchestrator import resume_repair_run, run_repair
from .replay import verify_repair_run

EXIT_INPUT = 2
EXIT_TRUSTED = 3
EXIT_CANDIDATE = 4
EXIT_ADJUDICATION = 5
EXIT_DIRECTIVE = 6
EXIT_PROVIDER = 7
EXIT_PATCH = 8
EXIT_ATTEMPTS = 9
EXIT_TIME = 10
EXIT_PERSISTENCE = 11
EXIT_BENCHMARK = 12


def repair_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repair a hostile quantum candidate.")
    parser.add_argument("--candidate-source", type=Path)
    parser.add_argument("--public-experiment", type=Path)
    parser.add_argument("--trusted-reference", type=Path)
    parser.add_argument("--result-root", type=Path)
    parser.add_argument("--candidate-image")
    parser.add_argument(
        "--candidate-lock",
        type=Path,
        default=Path("requirements/quantum-preflight.lock"),
    )
    parser.add_argument("--task-identifier", default="quantum-repair-candidate")
    parser.add_argument("--provider", choices=("reviewed-benchmark",))
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.resume is not None:
            result = resume_repair_run(args.resume)
        else:
            required = (
                args.candidate_source,
                args.public_experiment,
                args.trusted_reference,
                args.result_root,
                args.candidate_image,
                args.provider,
            )
            if any(item is None for item in required):
                parser.error(
                    "new repair runs require source, experiment, trusted reference, result root, image, and provider"
                )
            manifest = load_manifest(args.public_experiment)
            trusted = load_verified_trusted_reference(
                args.trusted_reference, manifest.experiment
            )
            provider = ReviewedBenchmarkRepairProvider(
                manifest.experiment.model_dump(mode="json")
            )
            result = run_repair(
                task_identifier=args.task_identifier,
                candidate_source=args.candidate_source,
                public_manifest=manifest,
                trusted=trusted,
                result_root=args.result_root,
                candidate_image_identifier=args.candidate_image,
                candidate_lock_path=args.candidate_lock,
                provider=provider,
            )
    except FileNotFoundError as exc:
        return _error(EXIT_INPUT, exc)
    except Exception as exc:
        return _error(_classify_exception(exc), exc)
    print(json.dumps(result, sort_keys=True))
    if result.get("authorized") is True:
        return 0
    raw_status = result.get("terminal_status")
    status = raw_status if isinstance(raw_status, str) else ""
    return {
        "repair_provider_failed": EXIT_PROVIDER,
        "patch_rejected": EXIT_PATCH,
        "attempt_budget_exhausted": EXIT_ATTEMPTS,
        "time_budget_exhausted": EXIT_TIME,
    }.get(status, EXIT_PERSISTENCE)


def benchmark_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the 30-case quantum repair benchmark."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--diagnosis-manifest", type=Path, required=True)
    parser.add_argument("--trusted-reference", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--candidate-image", required=True)
    parser.add_argument("--candidate-lock", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--diagnosis-support", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_repair_benchmark(
            benchmark_manifest_path=args.manifest,
            diagnosis_manifest_path=args.diagnosis_manifest,
            trusted_reference_directory=args.trusted_reference,
            result_root=args.result_root,
            candidate_image_identifier=args.candidate_image,
            candidate_lock_path=args.candidate_lock,
            fixture_root=args.fixture_root,
            diagnosis_support_path=args.diagnosis_support,
        )
    except Exception as exc:
        return _error(EXIT_BENCHMARK, exc)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["repair_benchmark_passed"] else EXIT_BENCHMARK


def verify_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a repair run without executing code."
    )
    parser.add_argument("repair_run", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify_repair_run(args.repair_run)
    except Exception as exc:
        return _error(EXIT_PERSISTENCE, exc)
    print(json.dumps(result, sort_keys=True))
    return 0


def _classify_exception(exc: Exception) -> int:
    text = str(exc).lower()
    if "trusted" in text:
        return EXIT_TRUSTED
    if "provider" in text:
        return EXIT_PROVIDER
    if "patch" in text:
        return EXIT_PATCH
    if "directive" in text:
        return EXIT_DIRECTIVE
    if "adjudicat" in text:
        return EXIT_ADJUDICATION
    if "candidate" in text or "docker" in text:
        return EXIT_CANDIDATE
    return EXIT_PERSISTENCE


def _error(code: int, exc: Exception) -> int:
    print(
        json.dumps(
            {"authorized": False, "exit_code": code, "error": str(exc)}, sort_keys=True
        ),
        file=sys.stderr,
    )
    return code


def main(argv: list[str] | None = None) -> int:
    """Dispatch module execution while preserving the three stable entry points."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        print(
            "usage: python -m cgr.quantum_repair.cli {repair,benchmark,verify} ...",
            file=sys.stderr,
        )
        return EXIT_INPUT
    command, *remaining = arguments
    dispatch = {
        "repair": repair_main,
        "benchmark": benchmark_main,
        "verify": verify_main,
    }
    selected = dispatch.get(command)
    if selected is None:
        print(f"unknown quantum repair command: {command}", file=sys.stderr)
        return EXIT_INPUT
    return selected(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
