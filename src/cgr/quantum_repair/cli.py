"""Stable repair, benchmark, and replay CLIs with categorized exit status."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from cgr.quantum_candidate.trusted import load_verified_trusted_reference
from cgr.quantum_preflight.manifests import load_manifest

from .benchmark import run_repair_benchmark
from .benchmark_provider import ReviewedBenchmarkRepairProvider
from .contracts import QuantumRepairPolicy
from .orchestrator import resume_repair_run, run_repair
from .providers import RepairProvider
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
    parser.add_argument(
        "--provider",
        choices=("reviewed-benchmark", "sweagent-openai-compatible"),
        default="reviewed-benchmark",
    )
    parser.add_argument("--provider-config", type=Path)
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
            repair_policy = None
            provider: RepairProvider
            if args.provider == "reviewed-benchmark":
                provider = ReviewedBenchmarkRepairProvider(
                    manifest.experiment.model_dump(mode="json")
                )
            else:
                from .model_provider import (
                    SWEAgentOpenAICompatibleRepairProvider,
                    load_provider_config,
                )

                config = load_provider_config(args.provider_config)
                provider = SWEAgentOpenAICompatibleRepairProvider(
                    config=config,
                    public_task=manifest.experiment.model_dump(mode="json"),
                )
                repair_policy = QuantumRepairPolicy(
                    maximum_provider_seconds=min(
                        3600,
                        config.budget.maximum_wall_seconds + 30,
                    ),
                    maximum_total_seconds=min(
                        3600, config.budget.maximum_wall_seconds + 600
                    ),
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
                repair_policy=repair_policy,
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


def provider_check_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the optional SWE-agent/OpenAI-compatible provider."
    )
    parser.add_argument(
        "--provider", choices=("sweagent-openai-compatible",), required=True
    )
    parser.add_argument("--provider-config", type=Path)
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        from .model_provider.agent import TOOL_DOCKER_ARGS, verify_pristine_sweagent
        from .model_provider.config import load_provider_config
        from .model_provider.endpoint import verify_model_endpoint

        config = load_provider_config(args.provider_config)
        api_key = config.api_key()
        endpoint = verify_model_endpoint(
            base_url=config.base_url,
            requested_model=config.model_identifier,
            api_key=api_key,
            request_timeout_seconds=config.request_timeout_seconds,
            sampling=config.sampling,
            budget=config.budget,
        )
        agent = verify_pristine_sweagent(config)
        docker = shutil.which("docker")
        git = shutil.which("git")
        if docker is None or git is None:
            raise ValueError("Provider requires Docker and Git executables.")
        image = subprocess.run(
            [docker, "image", "inspect", config.tool_container_image],
            capture_output=True,
            text=True,
            check=False,
        )
        if image.returncode:
            raise ValueError("Configured agent tool image is unavailable locally.")
        args.evidence_root.mkdir(parents=True, exist_ok=True)
        probe = args.evidence_root / ".cgr-provider-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        result = {
            "provider_healthy": True,
            "model_endpoint_descriptor_sha256": endpoint.descriptor_sha256,
            "observed_model_identifier": endpoint.observed_model_identifier,
            "observed_context_length": endpoint.observed_context_length,
            "agent_descriptor_sha256": agent.descriptor_sha256,
            "sweagent_commit": agent.pristine_source_commit,
            "sweagent_clean": agent.source_tree_clean,
            "tool_network_disabled": "--network=none" in TOOL_DOCKER_ARGS,
            "docker_socket_forwarded": False,
            "credential_forwarding": False,
        }
    except Exception as exc:
        return _error(EXIT_PROVIDER, exc)
    print(json.dumps(result, sort_keys=True))
    return 0


def model_acceptance_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run baseline-versus-CGR SWE-agent model acceptance."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--provider-config", type=Path)
    parser.add_argument("--trusted-reference", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--candidate-image", required=True)
    parser.add_argument("--candidate-lock", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--diagnosis-support", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        from .model_acceptance import run_model_acceptance
        from .model_provider.config import load_provider_config

        result = run_model_acceptance(
            acceptance_manifest_path=args.manifest,
            provider_config=load_provider_config(args.provider_config),
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
    return 0 if result["model_provider_acceptance_passed"] else EXIT_BENCHMARK


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
        return repair_main(arguments)
    command, *remaining = arguments
    dispatch = {
        "repair": repair_main,
        "benchmark": benchmark_main,
        "verify": verify_main,
        "provider-check": provider_check_main,
        "model-acceptance": model_acceptance_main,
    }
    selected = dispatch.get(command)
    if selected is None:
        return repair_main(arguments)
    return selected(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
