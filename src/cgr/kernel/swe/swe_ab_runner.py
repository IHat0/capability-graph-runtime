"""Baseline versus CGR coding-agent evaluation runner."""

import time
from typing import Any

from cgr.kernel.coding import (
    CodingPatch,
    CodingPatchNormalizationError,
    CodingPatchNormalizer,
    CodingTask,
    PythonTestRunner,
    build_format_retry_prompt,
    build_patch_prompt,
)
from cgr.kernel.contracts import ExecutionContext, ExecutionRequest, ExecutionStatus
from cgr.kernel.model import ModelMessage, ModelRequest, ModelRole
from cgr.kernel.runtime import KernelRuntime

from .swe_case_result import SWECaseResult, SWEMode
from .swe_eval_result import SWEEvalResult
from .swe_task import SWETask


class SWEABRunner:
    """Compare a direct provider baseline with single- and multi-agent modes."""

    def __init__(self, runtime: KernelRuntime) -> None:
        self._runtime = runtime

    def run_suite(
        self,
        suite_name: str,
        tasks: list[SWETask],
        baseline_plugin_id: str,
        single_agent_plugin_id: str,
        multi_agent_plugin_id: str,
        debug_trace: bool = False,
    ) -> SWEEvalResult:
        modes: list[tuple[SWEMode, str]] = [
            ("baseline", baseline_plugin_id),
            ("cgr_single", single_agent_plugin_id),
            ("cgr_multi", multi_agent_plugin_id),
        ]
        results = [
            self._run_case(task, mode, plugin_id, debug_trace)
            for task in tasks
            for mode, plugin_id in modes
        ]
        pass_rates: dict[str, float] = {
            mode: self._pass_rate(results, mode, len(tasks))
            for mode, _ in modes
        }
        deltas = {
            "cgr_single_minus_baseline": (
                pass_rates["cgr_single"] - pass_rates["baseline"]
            ),
            "cgr_multi_minus_baseline": (
                pass_rates["cgr_multi"] - pass_rates["baseline"]
            ),
        }
        return SWEEvalResult(
            suite_name=suite_name,
            total_tasks=len(tasks),
            pass_rates=pass_rates,
            deltas=deltas,
            results=results,
        )

    def _run_case(
        self,
        task: SWETask,
        mode: SWEMode,
        plugin_id: str,
        debug_trace: bool = False,
    ) -> SWECaseResult:
        started = time.perf_counter()
        try:
            trace: dict[str, Any] | None = None
            if mode == "baseline":
                patch = self._run_baseline(task, plugin_id)
            else:
                patch, trace = self._run_agent(
                    task, plugin_id, debug_trace=debug_trace
                )
            passed = patch.files == task.expected_files
            if task.scoring_test_files and task.scoring_test_commands:
                passed, _ = PythonTestRunner().run(
                    patch.files,
                    task.scoring_test_files,
                    task.scoring_test_commands,
                )
            return SWECaseResult(
                task_id=task.id,
                mode=mode,
                plugin_id=plugin_id,
                passed=passed,
                files=patch.files,
                elapsed_seconds=time.perf_counter() - started,
                **(self._trace_fields(trace) if debug_trace else {}),
            )
        except Exception as exc:
            return SWECaseResult(
                task_id=task.id,
                mode=mode,
                plugin_id=plugin_id,
                passed=False,
                error_type=type(exc).__name__,
                error_message=str(exc),
                raw_output_preview=getattr(exc, "raw_output_preview", None),
                elapsed_seconds=time.perf_counter() - started,
            )

    def _run_baseline(self, task: SWETask, plugin_id: str) -> CodingPatch:
        plugin = self._runtime.registry.get(plugin_id)
        coding_task = CodingTask(issue=task.issue, files=task.files)
        result = self._runtime.execute(
            plugin_id,
            ExecutionRequest[ModelRequest](
                capability=plugin.metadata.capabilities[0],
                context=ExecutionContext(),
                payload=ModelRequest(
                    messages=[
                        ModelMessage(
                            role=ModelRole.USER,
                            content=build_patch_prompt(coding_task),
                        )
                    ]
                ),
            ),
        )
        if result.status != ExecutionStatus.SUCCESS:
            raise RuntimeError(result.error or "Baseline model execution failed.")
        text = self._model_text(result.output)
        normalizer = CodingPatchNormalizer()
        try:
            return normalizer.normalize(text, set(task.files))
        except CodingPatchNormalizationError:
            retry = self._runtime.execute(
                plugin_id,
                ExecutionRequest[ModelRequest](
                    capability=plugin.metadata.capabilities[0],
                    context=ExecutionContext(),
                    payload=ModelRequest(
                        messages=[
                            ModelMessage(
                                role=ModelRole.USER,
                                content=build_format_retry_prompt(text),
                            )
                        ]
                    ),
                ),
            )
            if retry.status != ExecutionStatus.SUCCESS:
                raise RuntimeError(
                    retry.error or "Baseline format retry execution failed."
                )
            return normalizer.normalize(
                self._model_text(retry.output), set(task.files)
            )

    def _run_agent(
        self, task: SWETask, plugin_id: str, debug_trace: bool = False
    ) -> tuple[CodingPatch, dict[str, Any] | None]:
        plugin = self._runtime.registry.get(plugin_id)
        result = self._runtime.execute(
            plugin_id,
            ExecutionRequest[dict[str, Any]](
                capability=plugin.metadata.capabilities[0],
                context=ExecutionContext(),
                payload={
                    "issue": task.issue,
                    "files": task.files,
                    "test_files": task.prompt_test_files,
                    "test_commands": task.prompt_test_commands,
                    "metadata": {"debug_trace": str(debug_trace).lower()},
                },
            ),
        )
        if result.status != ExecutionStatus.SUCCESS:
            raise RuntimeError(result.error or "Coding agent execution failed.")
        output = result.output
        patch = CodingPatch.model_validate(output)
        trace = output.get("_trace") if isinstance(output, dict) else None
        return patch, trace if isinstance(trace, dict) else None

    @staticmethod
    def _trace_fields(trace: dict[str, Any] | None) -> dict[str, Any]:
        if trace is None:
            return {}
        return {
            "attempts_count": trace.get("attempts_count"),
            "candidates_count": trace.get("candidates_count"),
            "repair_attempts_count": trace.get("repair_attempts_count"),
            "selected_candidate_id": trace.get("selected_candidate_id"),
            "verifier_messages_preview": trace.get(
                "verifier_messages_preview"
            ),
            "repair_prompt_preview": trace.get("repair_prompt_preview"),
            "candidate_scores": trace.get("candidate_scores"),
            "candidate_file_previews": trace.get("candidate_file_previews"),
            "known_failing_candidate_ids": trace.get(
                "known_failing_candidate_ids"
            ),
            "repeated_candidate_rejections": trace.get(
                "repeated_candidate_rejections"
            ),
            "forbidden_pattern_hints": trace.get("forbidden_pattern_hints"),
            "repair_plan_preview": trace.get("repair_plan_preview"),
            "repair_variant_count": trace.get("repair_variant_count"),
            "test_assertion_checklist": trace.get("test_assertion_checklist"),
            "latest_failure_preview_by_candidate": trace.get(
                "latest_failure_preview_by_candidate"
            ),
            "repair_prompt_previews_by_attempt": trace.get(
                "repair_prompt_previews_by_attempt"
            ),
            "test_io_examples": trace.get("test_io_examples"),
            "failed_required_examples": trace.get("failed_required_examples"),
            "repair_variant_names": trace.get("repair_variant_names"),
            "example_coverage_missing_by_candidate": trace.get(
                "example_coverage_missing_by_candidate"
            ),
            "failed_required_examples_by_attempt": trace.get(
                "failed_required_examples_by_attempt"
            ),
            "truthy_examples": trace.get("truthy_examples"),
            "falsy_examples": trace.get("falsy_examples"),
            "single_fallback_used": trace.get("single_fallback_used"),
            "single_fallback_candidate_id": trace.get(
                "single_fallback_candidate_id"
            ),
            "single_fallback_score": trace.get("single_fallback_score"),
            "multi_monotonic_guard_applied": trace.get(
                "multi_monotonic_guard_applied"
            ),
            "all_candidate_scores_before_selection": trace.get(
                "all_candidate_scores_before_selection"
            ),
            "final_selection_reason": trace.get("final_selection_reason"),
        }

    @staticmethod
    def _model_text(output: Any) -> str:
        if not isinstance(output, dict) or not isinstance(output.get("text"), str):
            raise RuntimeError("Model response did not contain text.")
        return output["text"]

    @staticmethod
    def _pass_rate(
        results: list[SWECaseResult], mode: SWEMode, total_tasks: int
    ) -> float:
        if total_tasks == 0:
            return 0.0
        return sum(result.passed for result in results if result.mode == mode) / total_tasks
