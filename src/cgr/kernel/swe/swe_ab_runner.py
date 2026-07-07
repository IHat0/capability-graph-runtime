"""Baseline versus CGR coding-agent evaluation runner."""

from typing import Any

from cgr.kernel.coding import CodingPatch, CodingTask, JsonPatchParser, build_patch_prompt
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
    ) -> SWEEvalResult:
        modes: list[tuple[SWEMode, str]] = [
            ("baseline", baseline_plugin_id),
            ("cgr_single", single_agent_plugin_id),
            ("cgr_multi", multi_agent_plugin_id),
        ]
        results = [
            self._run_case(task, mode, plugin_id)
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
        self, task: SWETask, mode: SWEMode, plugin_id: str
    ) -> SWECaseResult:
        try:
            patch = (
                self._run_baseline(task, plugin_id)
                if mode == "baseline"
                else self._run_agent(task, plugin_id)
            )
            return SWECaseResult(
                task_id=task.id,
                mode=mode,
                plugin_id=plugin_id,
                passed=patch.files == task.expected_files,
                files=patch.files,
            )
        except Exception as exc:
            return SWECaseResult(
                task_id=task.id,
                mode=mode,
                plugin_id=plugin_id,
                passed=False,
                error_type=type(exc).__name__,
                error_message=str(exc),
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
        return JsonPatchParser().parse(self._model_text(result.output))

    def _run_agent(self, task: SWETask, plugin_id: str) -> CodingPatch:
        plugin = self._runtime.registry.get(plugin_id)
        result = self._runtime.execute(
            plugin_id,
            ExecutionRequest[dict[str, Any]](
                capability=plugin.metadata.capabilities[0],
                context=ExecutionContext(),
                payload={"issue": task.issue, "files": task.files},
            ),
        )
        if result.status != ExecutionStatus.SUCCESS:
            raise RuntimeError(result.error or "Coding agent execution failed.")
        return CodingPatch.model_validate(result.output)

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
