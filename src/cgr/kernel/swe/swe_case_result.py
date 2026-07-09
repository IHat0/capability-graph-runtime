"""One mode/task result from an SWE A/B evaluation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


SWEMode = Literal["baseline", "cgr_single", "cgr_multi"]


class SWECaseResult(BaseModel):
    """Immutable normalized result for one evaluated mode and task."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    mode: SWEMode
    plugin_id: str
    passed: bool
    files: dict[str, str] | None = None
    error_type: str | None = None
    error_message: str | None = None
    raw_output_preview: str | None = None
    attempts_count: int | None = None
    candidates_count: int | None = None
    repair_attempts_count: int | None = None
    selected_candidate_id: str | None = None
    verifier_messages_preview: str | None = None
    repair_prompt_preview: str | None = None
    candidate_scores: dict[str, float] | None = None
    candidate_file_previews: dict[str, dict[str, str]] | None = None
    known_failing_candidate_ids: list[str] | None = None
    repeated_candidate_rejections: int | None = None
    forbidden_pattern_hints: list[str] | None = None
    repair_plan_preview: str | None = None
    repair_variant_count: int | None = None
    test_assertion_checklist: list[str] | None = None
    latest_failure_preview_by_candidate: dict[str, str] | None = None
    repair_prompt_previews_by_attempt: dict[str, str] | None = None
    test_io_examples: list[str] | None = None
    failed_required_examples: list[str] | None = None
    repair_variant_names: list[str] | None = None
    example_coverage_missing_by_candidate: dict[str, list[str]] | None = None
    failed_required_examples_by_attempt: dict[str, list[str]] | None = None
    truthy_examples: list[str] | None = None
    falsy_examples: list[str] | None = None
    single_fallback_used: bool | None = None
    single_fallback_candidate_id: str | None = None
    single_fallback_score: float | None = None
    multi_monotonic_guard_applied: bool | None = None
    all_candidate_scores_before_selection: dict[str, float] | None = None
    final_selection_reason: str | None = None
    elapsed_seconds: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None
    task_contract_checklist: list[str] | None = None
    visible_failure_summary: str | None = None
    hidden_failure_summary_safe: str | None = None
    syntax_error_summary: str | None = None
    hidden_source_included: bool | None = None
    parser_contract_detected: bool | None = None
    bool_before_string_guard_applied: bool | None = None
    rejected_candidates_before_tests: list[str] | None = None
    final_exact_verification_passed: bool | None = None
    final_exact_verification_summary: str | None = None
    allowed_files_to_edit: list[str] | None = None
    changed_files: list[str] | None = None
    disallowed_file_edits: list[str] | None = None
    repo_test_command_summaries: list[str] | None = None
    final_exact_repo_verification_passed: bool | None = None
    final_exact_repo_verification_summary: str | None = None
    baseline_fallback_used: bool | None = None
    baseline_fallback_score: float | None = None
    baseline_fallback_candidate_id: str | None = None
    baseline_fallback_final_exact_repo_verification_passed: bool | None = None
    repo_semantic_repair_variants: list[str] | None = None
    repo_contract_hints: list[str] | None = None
    expected_got_hints: list[str] | None = None
    literal_format_hints: list[str] | None = None
    placeholder_filename_remapped: bool | None = None
    placeholder_filename_original: str | None = None
    placeholder_filename_target: str | None = None
    router_param_rejection_hints: list[str] | None = None
