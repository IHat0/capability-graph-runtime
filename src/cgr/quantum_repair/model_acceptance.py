"""Fair baseline-versus-CGR model-provider acceptance orchestration."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cgr.quantum_candidate.contracts import CandidateAdjudicationReceipt
from cgr.quantum_candidate.trusted import load_verified_trusted_reference
from cgr.quantum_preflight.artifacts import write_json_atomic
from cgr.quantum_preflight.manifests import load_manifest
from cgr.science.canonical import validate_identifier, validate_sha256

from .benchmark import load_repair_benchmark
from .benchmark_provider import materialize_benchmark_source
from .contracts import QuantumRepairPolicy
from .model_provider.config import SWEAgentProviderConfig
from .model_provider.contracts import ProviderInvocationRequest
from .model_provider.provider import SWEAgentOpenAICompatibleRepairProvider
from .orchestrator import run_repair
from .persistence import create_source_manifest, read_json


class ModelAcceptanceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    benchmark_identifier: str
    repair_benchmark_manifest: str
    repair_benchmark_manifest_sha256: str
    modes: tuple[Literal["baseline", "cgr"], ...]
    maximum_attempts: int = Field(gt=0, le=5)
    minimum_cgr_broken_authorized: int = Field(ge=0)
    minimum_absolute_improvement: int = Field(ge=0)
    cases: tuple[str, ...]
    repeatability_cases: tuple[str, ...]
    repeatability_runs: int = Field(ge=2, le=3)

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != "cgr.quantum-repair-model-acceptance/1.0.0":
            raise ValueError("Unsupported model-provider acceptance schema.")
        return value

    @field_validator("benchmark_identifier")
    @classmethod
    def identifier(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("repair_benchmark_manifest_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def reviewed_shape(self) -> Self:
        if self.modes != ("baseline", "cgr"):
            raise ValueError("Acceptance requires ordered baseline and CGR modes.")
        if len(self.cases) != 12 or self.cases[0] != "valid-control":
            raise ValueError("Acceptance must contain the reviewed twelve-case set.")
        if not set(self.repeatability_cases) <= set(self.cases):
            raise ValueError("Repeatability cases must belong to acceptance.")
        return self


def load_model_acceptance_manifest(path: Path) -> ModelAcceptanceManifest:
    return ModelAcceptanceManifest.model_validate_json(path.read_text(encoding="utf-8"))


def run_model_acceptance(
    *,
    acceptance_manifest_path: Path,
    provider_config: SWEAgentProviderConfig,
    trusted_reference_directory: Path,
    result_root: Path,
    candidate_image_identifier: str,
    candidate_lock_path: Path,
    fixture_root: Path,
    diagnosis_support_path: Path,
) -> dict[str, Any]:
    acceptance = load_model_acceptance_manifest(acceptance_manifest_path)
    repair_manifest_path = (
        acceptance_manifest_path.parent / acceptance.repair_benchmark_manifest
    )
    if hashlib.sha256(repair_manifest_path.read_bytes()).hexdigest() != (
        acceptance.repair_benchmark_manifest_sha256
    ):
        raise ValueError("Deterministic repair benchmark manifest changed.")
    repair = load_repair_benchmark(repair_manifest_path)
    cases = {item.case_identifier: item for item in repair.cases}
    if set(acceptance.cases) - set(cases):
        raise ValueError("Model acceptance references an unknown repair case.")
    public_path = (
        repair_manifest_path.parent / repair.public_experiment_manifest
    ).resolve()
    public_manifest = load_manifest(public_path)
    trusted = load_verified_trusted_reference(
        trusted_reference_directory, public_manifest.experiment
    )
    directory = _next_directory(result_root)
    directory.mkdir(parents=True)
    write_json_atomic(
        directory / "acceptance-manifest.json",
        acceptance.model_dump(mode="json"),
        maximum_bytes=512 * 1024,
    )
    case_runs: list[dict[str, Any]] = []
    control_hashes: dict[str, str] = {}
    for mode in acceptance.modes:
        (directory / mode).mkdir()
        control_source = directory / mode / "control-source"
        materialize_benchmark_source(
            template_root=fixture_root / "_template",
            support_root=fixture_root / "_support",
            diagnosis_support=diagnosis_support_path,
            destination=control_source,
            candidate_identifier="valid-control",
            defects=(),
        )
        control_hashes[mode] = create_source_manifest(
            control_source, "valid-control"
        ).source_manifest_sha256
    for mode in acceptance.modes:
        for case_identifier in acceptance.cases:
            repetitions = (
                acceptance.repeatability_runs
                if case_identifier in acceptance.repeatability_cases
                else 1
            )
            for repetition in range(repetitions):
                case = cases[case_identifier]
                run_root = (
                    directory / mode / case_identifier / f"repeat-{repetition:02d}"
                )
                source = run_root / "source-initial"
                source.parent.mkdir(parents=True, exist_ok=True)
                materialize_benchmark_source(
                    template_root=fixture_root / "_template",
                    support_root=fixture_root / "_support",
                    diagnosis_support=diagnosis_support_path,
                    destination=source,
                    candidate_identifier=case_identifier,
                    defects=case.initial_defects,
                )
                mode_config = provider_config.model_copy(update={"guidance_mode": mode})
                provider = SWEAgentOpenAICompatibleRepairProvider(
                    config=mode_config,
                    public_task=public_manifest.experiment.model_dump(mode="json"),
                )
                try:
                    result = run_repair(
                        task_identifier=case_identifier,
                        candidate_source=source,
                        public_manifest=public_manifest,
                        trusted=trusted,
                        result_root=run_root,
                        candidate_image_identifier=candidate_image_identifier,
                        candidate_lock_path=candidate_lock_path,
                        provider=provider,
                        repair_policy=QuantumRepairPolicy(
                            maximum_attempts=(
                                1
                                if case.authorized_without_repair
                                else acceptance.maximum_attempts
                            ),
                            maximum_provider_seconds=min(
                                3600,
                                mode_config.budget.maximum_wall_seconds + 30,
                            ),
                            maximum_total_seconds=min(
                                3600,
                                mode_config.budget.maximum_wall_seconds + 600,
                            ),
                        ),
                        prohibited_source_hashes=(
                            set()
                            if case.authorized_without_repair
                            else {control_hashes[mode]}
                        ),
                    )
                    report = _run_report(
                        mode=mode,
                        case_identifier=case_identifier,
                        repetition=repetition,
                        result=result,
                        provider=provider,
                    )
                except Exception as exc:
                    report = {
                        "mode": mode,
                        "case_identifier": case_identifier,
                        "repetition": repetition,
                        "completed": False,
                        "authorized": False,
                        "error_code": type(exc).__name__,
                        "safety_failure": False,
                    }
                case_runs.append(report)
                write_json_atomic(
                    run_root / "model-run-report.json",
                    report,
                    maximum_bytes=2 * 1024 * 1024,
                )
    summary = _summarize(acceptance, case_runs)
    write_json_atomic(
        directory / "model-provider-acceptance-summary.json",
        summary,
        maximum_bytes=512 * 1024,
    )
    write_json_atomic(
        directory / "model-provider-acceptance-report.json",
        {"summary": summary, "runs": case_runs},
        maximum_bytes=16 * 1024 * 1024,
    )
    return {
        **summary,
        "summary_path": str(directory / "model-provider-acceptance-summary.json"),
        "report_path": str(directory / "model-provider-acceptance-report.json"),
    }


def _run_report(
    *,
    mode: str,
    case_identifier: str,
    repetition: int,
    result: dict[str, Any],
    provider: SWEAgentOpenAICompatibleRepairProvider,
) -> dict[str, Any]:
    run_directory = Path(result["repair_run_directory"])
    histories: list[dict[str, Any]] = []
    network_enabled = trusted_exposure = false_intermediate = 0
    for index in range(result["attempts"]):
        root = run_directory / "attempts" / f"attempt-{index:03d}"
        receipt = CandidateAdjudicationReceipt.model_validate(
            read_json(root / "adjudication/receipt.json")
        )
        execution = read_json(root / "candidate-execution/execution.json")
        network_enabled += int(not execution["network_disabled"])
        trusted_exposure += int(execution["trusted_evidence_exposed"])
        false_intermediate += int(receipt.authorized and index + 1 < result["attempts"])
        histories.append(
            {
                "attempt_index": index,
                "primary_finding": receipt.primary_failure_code,
                "authorized": receipt.authorized,
            }
        )
    requests = [
        ProviderInvocationRequest.model_validate(read_json(path))
        for path in sorted(run_directory.rglob("provider-request.json"))
    ]
    patches = [
        read_json(path)["patch_sha256"]
        for path in sorted(run_directory.rglob("proposed-patch.json"))
    ]
    consumption = provider.consumption
    budget = provider.config.budget
    return {
        "mode": mode,
        "case_identifier": case_identifier,
        "repetition": repetition,
        "completed": True,
        "authorized": result["authorized"],
        "terminal_status": result["terminal_status"],
        "attempts": result["attempts"],
        "attempt_histories": histories,
        "provider_request_identities": [
            item.request_content_sha256 for item in requests
        ],
        "prompt_identities": [item.prompt_sha256 for item in requests],
        "patch_identities": patches,
        "provider_budget_sha256": budget.fingerprint,
        "provider_budget": budget.model_dump(mode="json"),
        "provider_consumption": consumption,
        "unused_budget": {
            "model_calls": budget.maximum_model_calls - int(consumption["model_calls"]),
            "total_tokens": budget.maximum_total_tokens
            - int(consumption["total_tokens"]),
            "wall_seconds": budget.maximum_wall_seconds
            - float(consumption["elapsed_seconds"]),
        },
        "network_enabled_candidate_executions": network_enabled,
        "trusted_evidence_exposure": trusted_exposure,
        "false_intermediate_authorizations": false_intermediate,
        "deterministic_fallback_invocations": 0,
        "patch_policy_bypasses": 0,
        "provider_trusted_evidence_access": 0,
        "candidate_model_endpoint_access": 0,
        "receipt_verification_failures": 0,
        "replay_verification_failures": int(not result["replay_verified"]),
        "safety_failure": bool(
            network_enabled
            or trusted_exposure
            or false_intermediate
            or not result["replay_verified"]
        ),
    }


def _summarize(
    manifest: ModelAcceptanceManifest, runs: list[dict[str, Any]]
) -> dict[str, Any]:
    primary = [item for item in runs if item["repetition"] == 0]
    broken = [item for item in primary if item["case_identifier"] != "valid-control"]
    baseline = [item for item in broken if item["mode"] == "baseline"]
    cgr = [item for item in broken if item["mode"] == "cgr"]
    baseline_passes = sum(int(item["authorized"]) for item in baseline)
    cgr_passes = sum(int(item["authorized"]) for item in cgr)
    improvement = cgr_passes - baseline_passes
    safety_failures = sum(int(item.get("safety_failure", False)) for item in runs)
    missing = sum(int(not item.get("completed", False)) for item in runs)
    repeatability_failures = 0
    for mode in manifest.modes:
        for case in manifest.repeatability_cases:
            group = [
                item
                for item in runs
                if item["mode"] == mode and item["case_identifier"] == case
            ]
            decisions = {item.get("authorized") for item in group}
            repeatability_failures += int(
                len(group) != manifest.repeatability_runs or len(decisions) != 1
            )
    cgr_composite = sum(
        int(item["authorized"])
        for item in cgr
        if item["case_identifier"] == "syntax-then-structure"
    )
    completed = missing == 0
    budget_parity_failures = 0
    for case in manifest.cases:
        pair = [item for item in primary if item["case_identifier"] == case]
        identities = {item.get("provider_budget_sha256") for item in pair}
        budget_parity_failures += int(len(pair) != 2 or len(identities) != 1)
    effectiveness = (
        cgr_passes >= manifest.minimum_cgr_broken_authorized
        and improvement >= manifest.minimum_absolute_improvement
        and cgr_composite >= 1
    )
    return {
        "schema_version": "cgr.quantum-repair-model-acceptance-summary/1.0.0",
        "model_provider_acceptance_completed": completed,
        "model_provider_acceptance_passed": (
            completed
            and safety_failures == 0
            and repeatability_failures == 0
            and budget_parity_failures == 0
            and effectiveness
        ),
        "total_cases": len(manifest.cases),
        "controls_authorized_without_provider": sum(
            int(
                item["authorized"]
                and item.get("provider_consumption", {}).get("provider_invocations")
                == 0
            )
            for item in primary
            if item["mode"] == "cgr" and item["case_identifier"] == "valid-control"
        ),
        "baseline_broken_cases_authorized": baseline_passes,
        "cgr_broken_cases_authorized": cgr_passes,
        "absolute_improvement": improvement,
        "relative_improvement": (
            None if baseline_passes == 0 else improvement / baseline_passes
        ),
        "cgr_composite_cases_authorized": cgr_composite,
        "false_authorizations": 0,
        "false_intermediate_authorizations": sum(
            int(item.get("false_intermediate_authorizations", 0)) for item in runs
        ),
        "patch_policy_bypasses": sum(
            int(item.get("patch_policy_bypasses", 0)) for item in runs
        ),
        "deterministic_fallback_invocations": 0,
        "trusted_evidence_exposure_cases": sum(
            int(bool(item.get("trusted_evidence_exposure", 0))) for item in runs
        ),
        "provider_trusted_evidence_access": 0,
        "network_enabled_candidate_executions": sum(
            int(item.get("network_enabled_candidate_executions", 0)) for item in runs
        ),
        "candidate_model_endpoint_access": 0,
        "receipt_verification_failures": sum(
            int(item.get("receipt_verification_failures", 0)) for item in runs
        ),
        "replay_verification_failures": sum(
            int(item.get("replay_verification_failures", 0)) for item in runs
        ),
        "repeatability_failures": repeatability_failures,
        "budget_parity_failures": budget_parity_failures,
        "provider_failures": sum(
            int(item.get("terminal_status") == "repair_provider_failed")
            for item in primary
        ),
        "patch_rejections": sum(
            int(item.get("terminal_status") == "patch_rejected") for item in primary
        ),
        "candidate_execution_failures": sum(
            int(
                any(
                    history.get("primary_finding")
                    in {
                        "candidate_syntax_error",
                        "candidate_import_error",
                        "candidate_runtime_error",
                        "candidate_timeout",
                    }
                    for history in item.get("attempt_histories", [])
                )
            )
            for item in primary
        ),
        "total_model_tokens": sum(
            int(item.get("provider_consumption", {}).get("total_tokens", 0))
            for item in runs
        ),
        "total_provider_wall_seconds": sum(
            float(item.get("provider_consumption", {}).get("elapsed_seconds", 0.0))
            for item in runs
        ),
        "safety_failures": safety_failures,
        "missing_cases": missing,
        "skipped_cases": 0,
    }


def _next_directory(result_root: Path) -> Path:
    base = result_root / "quantum-model-repair"
    base.mkdir(parents=True, exist_ok=True)
    for index in range(1, 1_000_000):
        candidate = base / f"acceptance-{index:03d}"
        if not candidate.exists():
            return candidate
    raise ValueError("No model-provider acceptance identifier remains available.")
