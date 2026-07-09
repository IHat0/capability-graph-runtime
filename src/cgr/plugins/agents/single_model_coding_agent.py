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
    check_bool_before_string_normalization,
    check_example_literal_coverage,
    classify_boolean_contract_examples,
    classify_boolean_string_examples,
    extract_forbidden_patterns_from_failed_code,
    extract_syntax_error_summary,
    extract_task_contract_checklist,
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
        task_contract_checklist = extract_task_contract_checklist(task.issue)
        test_io_examples = extract_test_io_examples(task.test_files)
        truthy_examples, falsy_examples = _merge_boolean_examples(
            classify_boolean_string_examples(test_io_examples),
            classify_boolean_contract_examples(task_contract_checklist),
        )
        parser_contract_detected = bool(truthy_examples or falsy_examples)
        model_result = self._execute_model(build_patch_prompt(task))
        patch, format_duration = self._normalize_with_retry(
            self._model_text(model_result.output), task
        )
        bool_guard = check_bool_before_string_normalization(
            patch.files, task_contract_checklist
        )
        verification = (
            (False, [bool_guard])
            if bool_guard is not None
            else verify_patch(task, patch)
        )
        duration_ms = model_result.duration_ms + format_duration
        candidates: list[tuple[str, CodingPatch, bool]] = [
            ("candidate_1", patch, bool(verification and verification[0]))
        ]
        repair_prompt: str | None = None
        verifier_messages = verification[1] if verification is not None else []
        coverage_missing_by_candidate: dict[str, list[str]] = {}
        forbidden_hints: list[str] = []
        if verification is not None and not verification[0]:
            failure_summary = summarize_python_test_failure(verification[1])
            forbidden_hints = extract_forbidden_patterns_from_failed_code(
                patch.files,
                failure_summary,
                test_assertion_checklist,
                test_io_examples,
                task_contract_checklist,
            )
            repair_prompt = build_repair_prompt(
                task,
                patch.files,
                verification[1],
                forbidden_pattern_hints=forbidden_hints,
            )
            repair_result = self._execute_model(repair_prompt)
            duration_ms += repair_result.duration_ms
            repaired, retry_duration = self._normalize_with_retry(
                self._model_text(repair_result.output), task
            )
            duration_ms += retry_duration
            repair_bool_guard = check_bool_before_string_normalization(
                repaired.files, task_contract_checklist
            )
            coverage_missing = check_example_literal_coverage(
                repaired.files, test_io_examples
            )
            if repair_bool_guard is not None:
                verifier_messages.append(repair_bool_guard)
                repaired_passed = False
            elif coverage_missing:
                coverage_missing_by_candidate["repair_1"] = coverage_missing
                verifier_messages.append(
                    "Rejected candidate before tests; missing required example "
                    f"coverage: {', '.join(coverage_missing)}"
                )
                repaired_passed = False
            else:
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
        final_verification = verify_patch(task, patch)
        final_exact_passed = (
            final_verification[0] if final_verification is not None else None
        )
        final_exact_summary = (
            "\n".join(final_verification[1])[-1000:]
            if final_verification is not None
            else "No executable verification contract was provided."
        )
        if final_verification is not None and not final_verification[0]:
            verifier_messages.append(
                "Final selected candidate failed exact-file verification."
            )
        output = patch.model_dump()
        output["_trace"] = self._trace(
            candidates,
            selected_id,
            verifier_messages,
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
            coverage_missing_by_candidate,
            truthy_examples,
            falsy_examples,
            task_contract_checklist,
            forbidden_hints,
            parser_contract_detected,
            bool_guard is not None,
            final_exact_passed,
            final_exact_summary,
            task.allowed_files_to_edit,
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
        allowed_filenames = set(task.allowed_files_to_edit or task.files)
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
        coverage_missing_by_candidate: dict[str, list[str]],
        truthy_examples: list[str],
        falsy_examples: list[str],
        task_contract_checklist: list[str],
        forbidden_hints: list[str],
        parser_contract_detected: bool,
        bool_guard_applied: bool,
        final_exact_passed: bool | None,
        final_exact_summary: str,
        allowed_files_to_edit: list[str],
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
            "example_coverage_missing_by_candidate": (
                coverage_missing_by_candidate
            ),
            "failed_required_examples_by_attempt": {
                candidate_id: infer_failed_test_io_examples(
                    test_io_examples, failure
                )
                for candidate_id, failure in latest_failures.items()
            },
            "truthy_examples": truthy_examples,
            "falsy_examples": falsy_examples,
            "task_contract_checklist": task_contract_checklist,
            "forbidden_pattern_hints": forbidden_hints,
            "visible_failure_summary": summarize_python_test_failure(
                [
                    message
                    for message in verifier_messages
                    if not message.startswith("Hidden scoring also failed.")
                ]
            ),
            "hidden_failure_summary_safe": "\n".join(
                message
                for message in verifier_messages
                if message.startswith("Hidden scoring also failed.")
            )[:2000]
            or None,
            "syntax_error_summary": extract_syntax_error_summary(
                verifier_messages
            ),
            "hidden_source_included": False,
            "parser_contract_detected": parser_contract_detected,
            "bool_before_string_guard_applied": bool_guard_applied,
            "rejected_candidates_before_tests": [
                candidate_id
                for candidate_id, _, passed in candidates
                if not passed
            ],
            "final_exact_verification_passed": final_exact_passed,
            "final_exact_verification_summary": final_exact_summary,
            "allowed_files_to_edit": allowed_files_to_edit,
            "changed_files": sorted(
                {
                    filename
                    for _, candidate, _ in candidates
                    for filename in candidate.files
                }
            ),
            "disallowed_file_edits": sorted(
                {
                    filename
                    for _, candidate, _ in candidates
                    for filename in candidate.files
                    if allowed_files_to_edit and filename not in allowed_files_to_edit
                }
            ),
            "repo_test_command_summaries": [
                message
                for message in verifier_messages
                if "exit code" in message
            ][-10:],
            "final_exact_repo_verification_passed": final_exact_passed,
            "final_exact_repo_verification_summary": final_exact_summary,
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


def _merge_boolean_examples(
    visible_examples: tuple[list[str], list[str]],
    contract_examples: tuple[list[str], list[str]],
) -> tuple[list[str], list[str]]:
    truthy, falsy = visible_examples
    contract_truthy, contract_falsy = contract_examples
    return (
        _unique([*truthy, *contract_truthy]),
        _unique([*falsy, *contract_falsy]),
    )


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
