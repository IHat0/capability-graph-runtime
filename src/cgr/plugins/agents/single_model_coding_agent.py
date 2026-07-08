"""Single-model coding agent plugin."""

from typing import Any

from cgr.kernel.coding import (
    CodingTask,
    CodingPatch,
    CodingPatchNormalizationError,
    CodingPatchNormalizer,
    build_format_retry_prompt,
    build_patch_prompt,
    build_repair_prompt,
    extract_test_assertion_checklist,
    extract_test_io_examples,
    infer_failed_test_io_examples,
    select_patch,
    summarize_python_test_failure,
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


class SingleModelCodingAgentPlugin(Plugin[Any, dict[str, Any]]):
    """Use one routed model capability to produce a structured coding patch."""

    def __init__(
        self,
        runtime: KernelRuntime,
        model_capability_id: str = "model.code",
        plugin_id: str = "agent.single_model_coding",
    ) -> None:
        self._runtime = runtime
        self._model_capability_id = model_capability_id
        self._state = PluginState.DISCOVERED
        tags = ["agent", "coding", "patch"]
        self._metadata = PluginMetadata(
            id=plugin_id,
            name="Single Model Coding Agent",
            version="1.0.0",
            author="CGR",
            description="Generates a coding patch using one model capability.",
            capabilities=[
                Capability(
                    id="coding.patch",
                    name="Coding Patch",
                    description="Generate patched files for a coding issue.",
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
        test_assertion_checklist = extract_test_assertion_checklist(task.test_files)
        test_io_examples = extract_test_io_examples(task.test_files)
        model_result = self._execute_model(build_patch_prompt(task))
        patch, format_duration = self._normalize_with_retry(
            self._model_text(model_result.output), task
        )
        verification = verify_patch(task, patch)
        duration_ms = model_result.duration_ms + format_duration
        candidates: list[tuple[str, CodingPatch, bool]] = [
            ("candidate_1", patch, bool(verification and verification[0]))
        ]
        repair_prompt: str | None = None
        if verification is not None and not verification[0]:
            repair_prompt = build_repair_prompt(
                task, patch.files, verification[1]
            )
            repair_result = self._execute_model(repair_prompt)
            duration_ms += repair_result.duration_ms
            repaired, retry_duration = self._normalize_with_retry(
                self._model_text(repair_result.output), task
            )
            duration_ms += retry_duration
            repaired_verification = verify_patch(task, repaired)
            repaired_passed = (
                repaired_verification[0]
                if repaired_verification is not None
                else False
            )
            candidates.append(("repair_1", repaired, repaired_passed))
            patch = select_patch(patch, False, repaired, repaired_passed)
        selected_id = next(
            candidate_id
            for candidate_id, candidate, _ in candidates
            if candidate is patch
        )
        output = patch.model_dump()
        output["_trace"] = self._trace(
            candidates,
            selected_id,
            verification[1] if verification is not None else [],
            repair_prompt,
            test_assertion_checklist,
            (
                {"candidate_1": summarize_python_test_failure(verification[1])}
                if verification is not None and not verification[0]
                else {}
            ),
            test_io_examples,
            (
                infer_failed_test_io_examples(
                    test_io_examples,
                    summarize_python_test_failure(verification[1]),
                )
                if verification is not None and not verification[0]
                else []
            ),
        )
        return ExecutionResult(
            context=request.context,
            status=ExecutionStatus.SUCCESS,
            output=output,
            duration_ms=duration_ms,
        )

    def _execute_model(self, prompt: str) -> ExecutionResult[Any]:
        capability = Capability(
            id=self._model_capability_id,
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
            retry = self._execute_model(build_format_retry_prompt(text))
            patch = normalizer.normalize(
                self._model_text(retry.output), allowed_filenames
            )
            return patch, retry.duration_ms

    @staticmethod
    def _trace(
        candidates: list[tuple[str, CodingPatch, bool]],
        selected_id: str,
        verifier_messages: list[str],
        repair_prompt: str | None,
        test_assertion_checklist: list[str],
        latest_failures: dict[str, str],
        test_io_examples: list[str],
        failed_required_examples: list[str],
    ) -> dict[str, Any]:
        return {
            "attempts_count": len(candidates),
            "candidates_count": len(candidates),
            "repair_attempts_count": sum(
                candidate_id.startswith("repair_")
                for candidate_id, _, _ in candidates
            ),
            "selected_candidate_id": selected_id,
            "verifier_messages_preview": "\n".join(verifier_messages)[-1000:],
            "repair_prompt_preview": (
                repair_prompt[:1000] if repair_prompt is not None else None
            ),
            "candidate_scores": {
                candidate_id: 1.0 if passed else 0.0
                for candidate_id, _, passed in candidates
            },
            "candidate_file_previews": {
                candidate_id: {
                    filename: content[:1000]
                    for filename, content in candidate.files.items()
                }
                for candidate_id, candidate, _ in candidates
            },
            "test_assertion_checklist": test_assertion_checklist,
            "latest_failure_preview_by_candidate": {
                candidate_id: failure[:1000]
                for candidate_id, failure in latest_failures.items()
            },
            "repair_prompt_previews_by_attempt": (
                {"repair_1": repair_prompt[:1000]}
                if repair_prompt is not None
                else {}
            ),
            "test_io_examples": test_io_examples,
            "failed_required_examples": failed_required_examples,
            "repair_variant_names": (
                ["single-model repair"] if repair_prompt is not None else []
            ),
        }

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
