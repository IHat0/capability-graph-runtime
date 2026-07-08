"""Draft, critique, and repair coding agent plugin."""

import json
from typing import Any

from cgr.kernel.coding import (
    CodingTask,
    CodingPatch,
    CodingPatchNormalizationError,
    CodingPatchNormalizer,
    build_format_retry_prompt,
    build_patch_prompt,
    build_repair_prompt,
    select_patch,
    verify_patch,
)
from cgr.kernel.contracts import (
    Capability,
    CapabilityVersion,
    ExecutionContext,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    HealthStatus,
    Plugin,
    PluginMetadata,
    PluginState,
)
from cgr.kernel.model import ModelMessage, ModelRequest, ModelRole
from cgr.kernel.runtime import KernelRuntime

class MultiModelCodingAgentPlugin(Plugin[Any, dict[str, Any]]):
    """Generate a patch using deterministic draft, critique, and repair stages."""

    def __init__(
        self,
        runtime: KernelRuntime,
        draft_capability_id: str = "model.code",
        critique_capability_id: str = "model.reason",
        plugin_id: str = "agent.multi_model_coding",
        max_repair_attempts: int = 2,
    ) -> None:
        if max_repair_attempts < 1:
            raise ValueError("max_repair_attempts must be positive.")
        self._runtime = runtime
        self._draft_capability_id = draft_capability_id
        self._critique_capability_id = critique_capability_id
        self._max_repair_attempts = max_repair_attempts
        self._state = PluginState.DISCOVERED
        tags = ["agent", "coding", "multi-model", "patch"]
        self._metadata = PluginMetadata(
            id=plugin_id,
            name="Multi-Model Coding Agent",
            version="1.0.0",
            author="CGR",
            description="Generates a coding patch using draft, critique, and repair.",
            capabilities=[
                Capability(
                    id="coding.patch.multi",
                    name="Multi-Model Coding Patch",
                    description=(
                        "Generate patched files using draft, critique, and repair."
                    ),
                    version=CapabilityVersion(major=1, minor=0, patch=0),
                    tags=tags,
                )
            ],
            tags=tags,
        )

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata

    @property
    def state(self) -> PluginState:
        return self._state

    @property
    def health(self) -> HealthStatus:
        return (
            HealthStatus.HEALTHY
            if self._state == PluginState.RUNNING
            else HealthStatus.DEGRADED
        )

    def initialize(self) -> None:
        self._state = PluginState.RUNNING

    def shutdown(self) -> None:
        self._state = PluginState.STOPPED

    def execute(self, request: ExecutionRequest[Any]) -> ExecutionResult[dict[str, Any]]:
        task = self._parse_task(request.payload)
        draft_result = self._execute_model(
            self._draft_capability_id, build_patch_prompt(task)
        )
        draft = self._model_text(draft_result.output)
        draft_patch, draft_format_duration = self._normalize_with_retry(draft, task)
        draft_verification = verify_patch(task, draft_patch)
        if draft_verification is not None and draft_verification[0]:
            return ExecutionResult(
                context=request.context,
                status=ExecutionStatus.SUCCESS,
                output=draft_patch.model_dump(),
                duration_ms=draft_result.duration_ms + draft_format_duration,
            )
        critique_prompt = (
            "Critique the proposed coding patch. Identify mistakes and suggest "
            "specific corrections. Preserve the public API, function signatures, "
            "and return types. Do not suggest extra return values.\n"
            f"Issue:\n{task.issue}\nOriginal files:\n"
            f"{json.dumps(task.files, indent=2)}\nDraft patch:\n{draft}"
        )
        critique_result = self._execute_model(
            self._critique_capability_id, critique_prompt
        )
        critique = self._model_text(critique_result.output)
        total_duration = (
            draft_result.duration_ms
            + draft_format_duration
            + critique_result.duration_ms
        )
        if draft_verification is None:
            repair_prompt = build_patch_prompt(
                task,
                f"Draft patch:\n{draft}\nCritique:\n{critique}\nRepair the draft.",
            )
            repair_result = self._execute_model(
                self._draft_capability_id, repair_prompt
            )
            patch, format_duration = self._normalize_with_retry(
                self._model_text(repair_result.output), task
            )
            total_duration += repair_result.duration_ms + format_duration
        else:
            patch = draft_patch
            patch_passed = draft_verification[0]
            current_patch = draft_patch
            current_messages = draft_verification[1]
            for attempt in range(1, self._max_repair_attempts + 1):
                repair_prompt = build_repair_prompt(
                    task,
                    draft_patch.files,
                    current_messages,
                    critique,
                    previous_repair_files=(
                        current_patch.files if attempt > 1 else None
                    ),
                    stronger_retry=attempt > 1,
                )
                repair_result = self._execute_model(
                    self._draft_capability_id, repair_prompt
                )
                repaired_patch, format_duration = self._normalize_with_retry(
                    self._model_text(repair_result.output), task
                )
                total_duration += repair_result.duration_ms + format_duration
                repaired_verification = verify_patch(task, repaired_patch)
                repaired_passed = bool(
                    repaired_verification and repaired_verification[0]
                )
                patch = select_patch(
                    patch,
                    patch_passed,
                    repaired_patch,
                    repaired_passed,
                )
                patch_passed = patch_passed or repaired_passed
                if repaired_passed:
                    break
                current_patch = repaired_patch
                current_messages = (
                    repaired_verification[1]
                    if repaired_verification is not None
                    else current_messages
                )
        return ExecutionResult(
            context=request.context,
            status=ExecutionStatus.SUCCESS,
            output=patch.model_dump(),
            duration_ms=total_duration,
        )

    def _execute_model(self, capability_id: str, prompt: str) -> ExecutionResult[Any]:
        capability = Capability(
            id=capability_id,
            name="Coding Agent Model",
            description="Model capability used by a coding agent.",
            version=CapabilityVersion(major=1, minor=0, patch=0),
        )
        result = self._runtime.execute_capability(
            ExecutionRequest[ModelRequest](
                capability=capability,
                context=ExecutionContext(),
                payload=ModelRequest(
                    messages=[ModelMessage(role=ModelRole.USER, content=prompt)]
                ),
            )
        )
        if result.status != ExecutionStatus.SUCCESS:
            raise RuntimeError(result.error or "Coding model execution failed.")
        return result

    def _normalize_with_retry(
        self, text: str, task: CodingTask
    ) -> tuple[CodingPatch, float]:
        normalizer = CodingPatchNormalizer()
        allowed_filenames = set(task.files)
        try:
            return normalizer.normalize(text, allowed_filenames), 0.0
        except CodingPatchNormalizationError:
            retry = self._execute_model(
                self._draft_capability_id, build_format_retry_prompt(text)
            )
            patch = normalizer.normalize(
                self._model_text(retry.output), allowed_filenames
            )
            return patch, retry.duration_ms

    @staticmethod
    def _parse_task(payload: Any) -> CodingTask:
        if isinstance(payload, CodingTask):
            return payload
        if isinstance(payload, dict):
            return CodingTask.model_validate(payload)
        raise ValueError("Coding payload must be a CodingTask or dictionary.")

    @staticmethod
    def _model_text(output: Any) -> str:
        if not isinstance(output, dict) or not isinstance(output.get("text"), str):
            raise RuntimeError("Model response did not contain text.")
        return output["text"]
