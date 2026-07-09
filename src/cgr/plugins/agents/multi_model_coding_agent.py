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
    check_bool_before_string_normalization,
    check_dict_list_contract_shape,
    check_duplicate_suffix_format,
    check_example_literal_coverage,
    classify_boolean_contract_examples,
    classify_boolean_string_examples,
    extract_forbidden_patterns_from_failed_code,
    extract_literal_format_hints,
    extract_repo_contract_repair_hints,
    extract_structural_repair_hints,
    extract_syntax_error_summary,
    extract_task_contract_checklist,
    extract_test_assertion_checklist,
    extract_test_io_examples,
    infer_failed_test_io_examples,
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

from .single_model_coding_agent import (
    SingleModelCodingAgentPlugin,
    _merge_boolean_examples,
)


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
        test_assertion_checklist = extract_test_assertion_checklist(task.test_files)
        task_contract_checklist = extract_task_contract_checklist(task.issue)
        repo_contract_hints = extract_repo_contract_repair_hints(
            task_contract_checklist
        )
        test_io_examples = extract_test_io_examples(task.test_files)
        truthy_examples, falsy_examples = _merge_boolean_examples(
            classify_boolean_string_examples(test_io_examples),
            classify_boolean_contract_examples(task_contract_checklist),
        )
        parser_contract_detected = bool(truthy_examples or falsy_examples)
        draft_result = self._execute_model(
            self._draft_capability_id, build_patch_prompt(task)
        )
        draft = self._model_text(draft_result.output)
        draft_patch, draft_format_duration = self._normalize_with_retry(draft, task)
        draft_bool_guard = check_bool_before_string_normalization(
            draft_patch.files, task_contract_checklist
        )
        draft_shape_guard = check_dict_list_contract_shape(
            draft_patch.files, task_contract_checklist
        )
        draft_verification = (
            (False, [draft_bool_guard])
            if draft_bool_guard is not None
            else (False, [draft_shape_guard])
            if draft_shape_guard is not None
            else verify_patch(task, draft_patch)
        )
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
        expected_got_hints: list[str] = []
        literal_format_hints: list[str] = []
        repo_semantic_repair_variants: list[str] = []
        repeated_rejections = 0
        repair_plan = ""
        latest_failures: dict[str, str] = {}
        failed_required_examples: list[str] = []
        repair_variant_names: list[str] = []
        coverage_missing_by_candidate: dict[str, list[str]] = {}
        single_fallback_used = False
        single_fallback_candidate_id: str | None = None
        single_fallback_score: float | None = None
        monotonic_guard_applied = False
        final_selection_reason = "Selected the best verified multi-model candidate."
        bool_guard_applied = draft_bool_guard is not None
        rejected_candidates_before_tests: list[str] = (
            ["candidate_1"]
            if draft_bool_guard is not None or draft_shape_guard is not None
            else []
        )
        if draft_verification is not None and draft_verification[0]:
            final_verification = verify_patch(task, draft_patch)
            output = draft_patch.model_dump()
            output["_trace"] = self._trace(
                candidates,
                "candidate_1",
                verifier_messages,
                repair_prompts,
                test_assertion_checklist=test_assertion_checklist,
                test_io_examples=test_io_examples,
                truthy_examples=truthy_examples,
                falsy_examples=falsy_examples,
                task_contract_checklist=task_contract_checklist,
                final_selection_reason=(
                    "Initial multi-model candidate passed verification."
                ),
                parser_contract_detected=parser_contract_detected,
                bool_guard_applied=bool_guard_applied,
                rejected_candidates_before_tests=rejected_candidates_before_tests,
                final_exact_passed=(
                    final_verification[0]
                    if final_verification is not None
                    else None
                ),
                final_exact_summary=(
                    "\n".join(final_verification[1])[-1000:]
                    if final_verification is not None
                    else "No executable verification contract was provided."
                ),
                allowed_files_to_edit=task.allowed_files_to_edit,
                repo_contract_hints=repo_contract_hints,
                literal_format_hints=literal_format_hints,
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
            latest_failures["candidate_1"] = draft_summary
            failed_required_examples.extend(
                infer_failed_test_io_examples(test_io_examples, draft_summary)
            )
            for hint in extract_structural_repair_hints(draft_summary):
                if hint not in expected_got_hints:
                    expected_got_hints.append(hint)
            for hint in extract_literal_format_hints(draft_summary):
                if hint not in literal_format_hints:
                    literal_format_hints.append(hint)
            forbidden_hints.extend(
                extract_forbidden_patterns_from_failed_code(
                    draft_patch.files,
                    draft_summary,
                    test_assertion_checklist,
                    test_io_examples,
                    task_contract_checklist,
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
                variant_name, variant_instruction = self._variant_instruction(
                    task,
                    attempt,
                    test_io_examples,
                    truthy_examples,
                    falsy_examples,
                    literal_format_hints,
                )
                if extract_syntax_error_summary(current_messages):
                    variant_name = "syntax-focused repair"
                    variant_instruction = (
                        "Repair variant: produce complete, parseable full files "
                        "first. Close any unterminated string literal, fix malformed "
                        "indentation, and do not preserve syntax typos. Then satisfy "
                        "the tests. "
                    )
                repair_variant_names.append(variant_name)
                if variant_name in {
                    "duplicate-name suffix repair",
                    "recursive precedence merge",
                    "formula/order-of-operations repair",
                    "stateful clock simulation repair",
                    "data-shape contract repair",
                    "literal duplicate suffix implementation",
                }:
                    repo_semantic_repair_variants.append(variant_name)
                repair_prompt = build_repair_prompt(
                    task,
                    current_patch.files,
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
                    failed_required_examples=failed_required_examples,
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
                    latest_failures[candidate_id] = rejection
                    current_patch = repaired_patch
                    current_messages = [rejection]
                    continue
                repair_bool_guard = check_bool_before_string_normalization(
                    repaired_patch.files, task_contract_checklist
                )
                repair_shape_guard = check_dict_list_contract_shape(
                    repaired_patch.files, task_contract_checklist
                )
                repair_suffix_guard = check_duplicate_suffix_format(
                    repaired_patch.files, literal_format_hints
                )
                if repair_bool_guard is not None:
                    bool_guard_applied = True
                    rejected_candidates_before_tests.append(candidate_id)
                    verifier_messages.append(repair_bool_guard)
                    candidates.append((candidate_id, repaired_patch, False))
                    known_failures.append(
                        (candidate_id, repaired_patch.files, repair_bool_guard)
                    )
                    latest_failures[candidate_id] = repair_bool_guard
                    current_patch = repaired_patch
                    current_messages = [repair_bool_guard]
                    continue
                if repair_shape_guard is not None:
                    rejected_candidates_before_tests.append(candidate_id)
                    verifier_messages.append(repair_shape_guard)
                    candidates.append((candidate_id, repaired_patch, False))
                    known_failures.append(
                        (candidate_id, repaired_patch.files, repair_shape_guard)
                    )
                    latest_failures[candidate_id] = repair_shape_guard
                    current_patch = repaired_patch
                    current_messages = [repair_shape_guard]
                    continue
                if repair_suffix_guard is not None:
                    rejected_candidates_before_tests.append(candidate_id)
                    verifier_messages.append(repair_suffix_guard)
                    candidates.append((candidate_id, repaired_patch, False))
                    known_failures.append(
                        (candidate_id, repaired_patch.files, repair_suffix_guard)
                    )
                    latest_failures[candidate_id] = repair_suffix_guard
                    current_patch = repaired_patch
                    current_messages = [repair_suffix_guard]
                    continue
                coverage_missing = check_example_literal_coverage(
                    repaired_patch.files, test_io_examples
                )
                if coverage_missing:
                    rejected_candidates_before_tests.append(candidate_id)
                    coverage_missing_by_candidate[candidate_id] = coverage_missing
                    rejection = (
                        "Rejected candidate before tests; missing required example "
                        f"coverage: {', '.join(coverage_missing)}"
                    )
                    verifier_messages.append(rejection)
                    candidates.append((candidate_id, repaired_patch, False))
                    known_failures.append(
                        (candidate_id, repaired_patch.files, rejection)
                    )
                    latest_failures[candidate_id] = rejection
                    for example in coverage_missing:
                        if example not in failed_required_examples:
                            failed_required_examples.append(example)
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
                latest_failures[candidate_id] = failure_summary
                for example in infer_failed_test_io_examples(
                    test_io_examples, failure_summary
                ):
                    if example not in failed_required_examples:
                        failed_required_examples.append(example)
                for hint in extract_structural_repair_hints(failure_summary):
                    if hint not in expected_got_hints:
                        expected_got_hints.append(hint)
                for hint in extract_literal_format_hints(failure_summary):
                    if hint not in literal_format_hints:
                        literal_format_hints.append(hint)
                for hint in extract_forbidden_patterns_from_failed_code(
                    repaired_patch.files,
                    failure_summary,
                    test_assertion_checklist,
                    test_io_examples,
                    task_contract_checklist,
                ):
                    if hint not in forbidden_hints:
                        forbidden_hints.append(hint)
                current_patch = repaired_patch
                current_messages = (
                    repaired_verification[1]
                    if repaired_verification is not None
                    else current_messages
                )
            if not patch_passed:
                monotonic_guard_applied = True
                single_fallback_candidate_id = "single_fallback_candidate"
                try:
                    fallback_patch, fallback_duration = self._run_single_fallback(
                        task, request.context
                    )
                    total_duration += fallback_duration
                    fallback_verification = verify_patch(task, fallback_patch)
                    fallback_passed = bool(
                        fallback_verification and fallback_verification[0]
                    )
                    single_fallback_score = 1.0 if fallback_passed else 0.0
                    candidates.append(
                        (
                            single_fallback_candidate_id,
                            fallback_patch,
                            fallback_passed,
                        )
                    )
                    if fallback_verification is not None:
                        verifier_messages.extend(fallback_verification[1])
                    if fallback_passed:
                        patch = fallback_patch
                        patch_passed = True
                        single_fallback_used = True
                        final_selection_reason = (
                            "Selected verified single-path fallback via the "
                            "multi monotonic guard."
                        )
                    else:
                        patch = select_patch(
                            patch,
                            patch_passed,
                            fallback_patch,
                            fallback_passed,
                        )
                        final_selection_reason = (
                            "No multi-model or single-path fallback candidate "
                            "passed verification."
                        )
                except Exception as exc:
                    single_fallback_score = 0.0
                    verifier_messages.append(
                        f"Single-path monotonic fallback failed: {type(exc).__name__}: "
                        f"{exc}"
                    )
                    final_selection_reason = (
                        "No multi-model candidate passed and the single-path "
                        "fallback raised an error."
                    )
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
            final_selection_reason = (
                f"{final_selection_reason} Final selected candidate failed "
                "exact-file verification."
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
            test_assertion_checklist,
            latest_failures,
            test_io_examples,
            failed_required_examples,
            repair_variant_names,
            coverage_missing_by_candidate,
            truthy_examples,
            falsy_examples,
            single_fallback_used,
            single_fallback_candidate_id,
            single_fallback_score,
            monotonic_guard_applied,
            final_selection_reason,
            task_contract_checklist,
            parser_contract_detected,
            bool_guard_applied,
            rejected_candidates_before_tests,
            final_exact_passed,
            final_exact_summary,
            task.allowed_files_to_edit,
            repo_contract_hints,
            expected_got_hints,
            repo_semantic_repair_variants,
            literal_format_hints,
        )
        return ExecutionResult(
            context=request.context,
            status=ExecutionStatus.SUCCESS,
            output=output,
            duration_ms=total_duration,
        )

    def _run_single_fallback(
        self, task: CodingTask, context: ExecutionContext
    ) -> tuple[CodingPatch, float]:
        fallback = SingleModelCodingAgentPlugin(
            self._runtime,
            model_capability_id=self._draft_capability_id,
            plugin_id=f"{self.metadata.id}.single_fallback",
        )
        result = fallback.execute(
            ExecutionRequest[CodingTask](
                capability=fallback.metadata.capabilities[0],
                context=context,
                payload=task,
            )
        )
        if result.status != ExecutionStatus.SUCCESS:
            raise RuntimeError(result.error or "Single-path fallback failed.")
        return CodingPatch.model_validate(result.output), result.duration_ms

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
        allowed_filenames = set(task.allowed_files_to_edit or task.files)
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
        test_assertion_checklist: list[str] | None = None,
        latest_failures: dict[str, str] | None = None,
        test_io_examples: list[str] | None = None,
        failed_required_examples: list[str] | None = None,
        repair_variant_names: list[str] | None = None,
        coverage_missing_by_candidate: dict[str, list[str]] | None = None,
        truthy_examples: list[str] | None = None,
        falsy_examples: list[str] | None = None,
        single_fallback_used: bool = False,
        single_fallback_candidate_id: str | None = None,
        single_fallback_score: float | None = None,
        monotonic_guard_applied: bool = False,
        final_selection_reason: str = "No final selection was made.",
        task_contract_checklist: list[str] | None = None,
        parser_contract_detected: bool = False,
        bool_guard_applied: bool = False,
        rejected_candidates_before_tests: list[str] | None = None,
        final_exact_passed: bool | None = None,
        final_exact_summary: str = "",
        allowed_files_to_edit: list[str] | None = None,
        repo_contract_hints: list[str] | None = None,
        expected_got_hints: list[str] | None = None,
        repo_semantic_repair_variants: list[str] | None = None,
        literal_format_hints: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "attempts_count": len(candidates),
            "candidates_count": len(candidates),
            "repair_attempts_count": len(repair_prompts),
            "selected_candidate_id": selected_id,
            "verifier_messages_preview": "\n".join(verifier_messages)[-1000:],
            "repair_prompt_preview": (
                MultiModelCodingAgentPlugin._repair_prompt_preview(
                    repair_prompts[0]
                )
                if repair_prompts
                else None
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
            "test_assertion_checklist": test_assertion_checklist or [],
            "latest_failure_preview_by_candidate": {
                candidate_id: failure[:1000]
                for candidate_id, failure in (latest_failures or {}).items()
            },
            "repair_prompt_previews_by_attempt": {
                f"repair_{index}": prompt[:1000]
                for index, prompt in enumerate(repair_prompts, 1)
            },
            "test_io_examples": test_io_examples or [],
            "failed_required_examples": failed_required_examples or [],
            "repair_variant_names": repair_variant_names or [],
            "example_coverage_missing_by_candidate": (
                coverage_missing_by_candidate or {}
            ),
            "failed_required_examples_by_attempt": {
                candidate_id: infer_failed_test_io_examples(
                    test_io_examples or [], failure
                )
                for candidate_id, failure in (latest_failures or {}).items()
            },
            "truthy_examples": truthy_examples or [],
            "falsy_examples": falsy_examples or [],
            "single_fallback_used": single_fallback_used,
            "single_fallback_candidate_id": single_fallback_candidate_id,
            "single_fallback_score": single_fallback_score,
            "multi_monotonic_guard_applied": monotonic_guard_applied,
            "all_candidate_scores_before_selection": {
                candidate_id: 1.0 if passed else 0.0
                for candidate_id, _, passed in candidates
            },
            "final_selection_reason": final_selection_reason,
            "task_contract_checklist": task_contract_checklist or [],
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
            "rejected_candidates_before_tests": (
                rejected_candidates_before_tests or []
            ),
            "final_exact_verification_passed": final_exact_passed,
            "final_exact_verification_summary": final_exact_summary,
            "allowed_files_to_edit": allowed_files_to_edit or [],
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
            "repo_semantic_repair_variants": repo_semantic_repair_variants or [],
            "repo_contract_hints": repo_contract_hints or [],
            "expected_got_hints": expected_got_hints or [],
            "literal_format_hints": literal_format_hints or [],
        }

    @staticmethod
    def _repair_prompt_preview(prompt: str) -> str:
        marker = "Latest failure diagnostic:"
        diagnostic_index = prompt.find(marker)
        if diagnostic_index < 0 or diagnostic_index < 500:
            return prompt[:1000]
        return (prompt[:500] + "\n...\n" + prompt[diagnostic_index:])[:1000]

    @staticmethod
    def _variant_instruction(
        task: CodingTask,
        attempt: int,
        test_io_examples: list[str],
        truthy_examples: list[str],
        falsy_examples: list[str],
        literal_format_hints: list[str] | None = None,
    ) -> tuple[str, str]:
        if attempt == 1:
            return (
                "minimal semantic patch",
                "Repair variant 1: make the smallest semantic patch. ",
            )
        context = (
            task.issue + "\n" + "\n".join(task.test_files.values())
        ).lower()
        merge_context = any(
            marker in context
            for marker in ("merge", "counts", "overlapping", "summed", "dictionary", "mapping")
        )
        parser_context = any(
            marker in context for marker in ("parse", "normalize", "accepted value")
        )
        data_shape_context = any(
            marker in context
            for marker in (
                "dict values",
                "dictionary values",
                "list of values",
                "one-item lists",
                "maps to a list",
                "wrong nesting",
            )
        )
        duplicate_context = any(
            marker in context
            for marker in ("duplicate", "deduplicate", "slug", "suffix", "heading")
        )
        recursive_context = any(
            marker in context
            for marker in (
                "config",
                "precedence",
                "nested",
                "recursive",
                "none does not override",
                "later sources override",
            )
        )
        formula_context = any(
            marker in context
            for marker in ("discount", "tax", "subtotal", "round final")
        )
        stateful_clock_context = any(
            marker in context
            for marker in ("clock", "refill", "capacity", "consume", "token")
        )
        if attempt == 2 and duplicate_context:
            suffix_instruction = (
                " Use the unsuffixed base slug for the first occurrence. For "
                "duplicates, compute candidate = f'{base}-{count}'. Do not mutate "
                "the base slug inside the loop. Do not do slug += str(count). Keep "
                "base_slug separate from candidate_slug."
                if literal_format_hints
                else ""
            )
            return "duplicate-name suffix repair", (
                "Use the unsuffixed base value for the first occurrence. Add -1, "
                "-2, etc. only for later duplicates. Track seen base values, choose "
                "the output value before incrementing the count, and ignore headings "
                "inside fenced code blocks when the tests require it. "
                f"{suffix_instruction} "
            )
        if attempt == 2 and recursive_context:
            return "recursive precedence merge", (
                "Implement a pure recursive merge. Copy dictionaries instead of "
                "mutating inputs. Apply sources in precedence order. Later sources "
                "override earlier sources, nested dictionaries merge recursively, "
                "and None values should not override existing values unless the "
                "task explicitly allows it. "
            )
        if attempt == 2 and formula_context:
            return "formula/order-of-operations repair", (
                "Focus on the arithmetic contract. Compute subtotal without "
                "mutating inputs. Apply discount before tax, tax after discount, "
                "and round only the final result. "
            )
        if attempt == 2 and stateful_clock_context:
            return "stateful clock simulation repair", (
                "Use the injected clock as the only time source. Track last refill "
                "time. Refill by elapsed time multiplied by rate, cap tokens at "
                "capacity, and perform refill before consume checks. "
            )
        if attempt == 2 and data_shape_context:
            return "data-shape contract repair", (
                "Focus only on matching the data shape required by expected "
                "outputs. If expected dictionary values are lists, initialize "
                "and return lists consistently. Do not special-case only the "
                "visible input; implement the general rule. "
            )
        if attempt == 2 and merge_context:
            return "loop-based implementation", (
                "Repair variant 2: use an explicit for-loop over the second input "
                "when combining mappings. "
            )
        if attempt >= 3 and duplicate_context and literal_format_hints:
            return "literal duplicate suffix implementation", (
                "Repair variant: literal duplicate suffix implementation. "
                "Implement the duplicate suffix algorithm exactly and generically:\n"
                "counts = {}\n"
                "for each item:\n"
                "    base = normalize(item)\n"
                "    n = counts.get(base, 0)\n"
                "    if n == 0:\n"
                "        output = base\n"
                "    else:\n"
                "        output = f\"{base}-{n}\"\n"
                "    counts[base] = n + 1\n"
                "Use the unsuffixed base slug for the first occurrence. For "
                "duplicates, compute candidate = f'{base}-{count}'. Do not mutate "
                "the base slug inside the loop. Do not do slug += str(count). Keep "
                "base_slug separate from candidate_slug. For markdown-like TOC "
                "tasks, preserve heading parsing and fenced-code ignoring while "
                "using this duplicate suffix algorithm. "
            )
        if attempt >= 3 and (test_io_examples or truthy_examples or falsy_examples):
            parser_instruction = (
                " Build explicit accepted-value sets from the examples."
                if parser_context
                else ""
            )
            boolean_examples = ""
            if truthy_examples or falsy_examples:
                boolean_examples = (
                    " Your code must contain all string examples from the contract, "
                    "normalized to lowercase where appropriate:\n"
                    f"truthy examples: {', '.join(truthy_examples)}\n"
                    f"falsy examples: {', '.join(falsy_examples)}."
                )
            name = (
                "contract-table parser implementation"
                if parser_context and (truthy_examples or falsy_examples)
                else "test-example-driven implementation"
            )
            return name, (
                "Implement directly from the task contract and Required "
                "input/output examples using explicit accepted-value sets. "
                "Handle bool inputs before any string operation. Normalize "
                "non-bool values with str(value).strip().lower(). Include every "
                "truthy value named in the contract. Include every falsy value "
                "named in the contract. Raise ValueError for invalid values. A "
                "table/set-based implementation is acceptable. Include every "
                "truthy/falsy or expected value shown in the examples."
                f"{parser_instruction}{boolean_examples} "
            )
        return f"test-derived variant {attempt}", (
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
