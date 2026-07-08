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
