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
    build_repair_plan_prompt,
    build_repair_prompt,
    extract_forbidden_patterns_from_failed_code,
    patch_fingerprint,
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

class MultiModelCodingAgentPlugin(Plugin[Any, dict[str, Any]]):
    """Generate a patch using deterministic draft, critique, and repair stages."""

    def __init__(
        self,
        runtime: KernelRuntime,
        draft_capability_id: str = "model.code",
        critique_capability_id: str = "model.reason",
        plugin_id: str = "agent.multi_model_coding",
        max_repair_attempts: int = 3,
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
        candidates: list[tuple[str, CodingPatch, bool]] = [
            (
                "candidate_1",
                draft_patch,
                bool(draft_verification and draft_verification[0]),
            )
        ]
        verifier_messages = (
            draft_verification[1] if draft_verification is not None else []
        )
        repair_prompts: list[str] = []
        known_failures: list[tuple[str, dict[str, str], str]] = []
        forbidden_hints: list[str] = []
        repeated_rejections = 0
        repair_plan = ""
        if draft_verification is not None and draft_verification[0]:
            output = draft_patch.model_dump()
            output["_trace"] = self._trace(
                candidates,
                "candidate_1",
                verifier_messages,
                repair_prompts,
            )
            return ExecutionResult(
                context=request.context,
                status=ExecutionStatus.SUCCESS,
                output=output,
                duration_ms=draft_result.duration_ms + draft_format_duration,
            )
        if draft_verification is not None:
            draft_summary = summarize_python_test_failure(draft_verification[1])
            known_failures.append(
                ("candidate_1", draft_patch.files, draft_summary)
            )
            forbidden_hints.extend(
                extract_forbidden_patterns_from_failed_code(
                    draft_patch.files, draft_summary
                )
            )
            critique_prompt = build_repair_plan_prompt(
                task, draft_patch.files, draft_summary
            )
        else:
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
        repair_plan = critique
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
            repair_prompts.append(repair_prompt)
            repair_result = self._execute_model(
                self._draft_capability_id, repair_prompt
            )
            patch, format_duration = self._normalize_with_retry(
                self._model_text(repair_result.output), task
            )
            total_duration += repair_result.duration_ms + format_duration
            candidates.append(("repair_1", patch, False))
        else:
            patch = draft_patch
            patch_passed = draft_verification[0]
            current_patch = draft_patch
            current_messages = draft_verification[1]
            for attempt in range(1, self._max_repair_attempts + 1):
                variant_instruction = self._variant_instruction(task, attempt)
                repair_prompt = build_repair_prompt(
                    task,
                    draft_patch.files,
                    current_messages,
                    critique,
                    previous_repair_files=(
                        current_patch.files if attempt > 1 else None
                    ),
                    stronger_retry=attempt > 1,
                    known_failures=known_failures,
                    forbidden_pattern_hints=forbidden_hints,
                    repair_plan=repair_plan,
                    variant_instruction=variant_instruction,
                )
                repair_prompts.append(repair_prompt)
                repair_result = self._execute_model(
                    self._draft_capability_id, repair_prompt
                )
                repaired_patch, format_duration = self._normalize_with_retry(
                    self._model_text(repair_result.output), task
                )
                total_duration += repair_result.duration_ms + format_duration
                candidate_id = f"repair_{attempt}"
                known_fingerprints = {
                    tuple(sorted(files.items())) for _, files, _ in known_failures
                }
                if patch_fingerprint(repaired_patch) in known_fingerprints:
                    repeated_rejections += 1
                    rejection = "Rejected repeated known-failing implementation."
                    verifier_messages.append(rejection)
                    candidates.append((candidate_id, repaired_patch, False))
                    known_failures.append(
                        (candidate_id, repaired_patch.files, rejection)
                    )
                    current_patch = repaired_patch
                    current_messages = [rejection]
                    continue
                repaired_verification = verify_patch(task, repaired_patch)
                repaired_passed = bool(
                    repaired_verification and repaired_verification[0]
                )
                candidates.append(
                    (candidate_id, repaired_patch, repaired_passed)
                )
                if repaired_verification is not None:
                    verifier_messages.extend(repaired_verification[1])
                patch = select_patch(
                    patch,
                    patch_passed,
                    repaired_patch,
                    repaired_passed,
                )
                patch_passed = patch_passed or repaired_passed
                if repaired_passed:
                    current_patch = repaired_patch
                    current_messages = (
                        repaired_verification[1]
                        if repaired_verification is not None
                        else current_messages
                    )
                    continue
                failure_summary = summarize_python_test_failure(
                    repaired_verification[1]
                    if repaired_verification is not None
                    else []
                )
                known_failures.append(
                    (candidate_id, repaired_patch.files, failure_summary)
                )
                for hint in extract_forbidden_patterns_from_failed_code(
                    repaired_patch.files, failure_summary
                ):
                    if hint not in forbidden_hints:
                        forbidden_hints.append(hint)
                current_patch = repaired_patch
                current_messages = (
                    repaired_verification[1]
                    if repaired_verification is not None
                    else current_messages
                )
        selected_id = next(
            candidate_id
            for candidate_id, candidate, _ in candidates
            if candidate is patch
        )
        output = patch.model_dump()
        output["_trace"] = self._trace(
            candidates,
            selected_id,
            verifier_messages,
            repair_prompts,
            known_failures,
            repeated_rejections,
            forbidden_hints,
            repair_plan,
        )
        return ExecutionResult(
            context=request.context,
            status=ExecutionStatus.SUCCESS,
            output=output,
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
    def _trace(
        candidates: list[tuple[str, CodingPatch, bool]],
        selected_id: str,
        verifier_messages: list[str],
        repair_prompts: list[str],
        known_failures: list[tuple[str, dict[str, str], str]] | None = None,
        repeated_rejections: int = 0,
        forbidden_hints: list[str] | None = None,
        repair_plan: str = "",
    ) -> dict[str, Any]:
        return {
            "attempts_count": len(candidates),
            "candidates_count": len(candidates),
            "repair_attempts_count": len(repair_prompts),
            "selected_candidate_id": selected_id,
            "verifier_messages_preview": "\n".join(verifier_messages)[-1000:],
            "repair_prompt_preview": (
                repair_prompts[0][:1000] if repair_prompts else None
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
            "known_failing_candidate_ids": [
                candidate_id
                for candidate_id, _, _ in (known_failures or [])
            ],
            "repeated_candidate_rejections": repeated_rejections,
            "forbidden_pattern_hints": forbidden_hints or [],
            "repair_plan_preview": repair_plan[:1000] if repair_plan else None,
            "repair_variant_count": len(repair_prompts),
        }

    @staticmethod
    def _variant_instruction(task: CodingTask, attempt: int) -> str:
        if attempt == 1:
            return "Repair variant 1: make the smallest semantic patch. "
        context = (
            task.issue + "\n" + "\n".join(task.test_files.values())
        ).lower()
        merge_context = any(
            marker in context
            for marker in ("merge", "counts", "overlapping", "summed", "dictionary", "mapping")
        )
        if attempt == 2 and merge_context:
            return (
                "Repair variant 2: use an explicit for-loop over the second input "
                "when combining mappings. "
            )
        return (
            f"Repair variant {attempt}: derive the implementation directly from "
            "each test assertion and use a different algorithm. "
        )

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
