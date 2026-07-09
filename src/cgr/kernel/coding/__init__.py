"""Coding-agent contracts and parsing utilities."""

from .code_test_case import CodeTestCase
from .coding_patch import CodingPatch
from .coding_patch_normalizer import (
    CodingPatchNormalizationError,
    CodingPatchNormalizer,
)
from .coding_prompt import (
    build_format_retry_prompt,
    build_patch_prompt,
    build_repair_plan_prompt,
    build_repair_prompt,
)
from .coding_task import CodingTask
from .json_patch_parser import JsonPatchParser
from .patch_verification import (
    apply_patch_to_task_files,
    check_bool_before_string_normalization,
    check_dict_list_contract_shape,
    check_duplicate_suffix_format,
    extract_forbidden_patterns_from_failed_code,
    extract_literal_format_hints,
    extract_repo_contract_repair_hints,
    extract_structural_repair_hints,
    patch_fingerprint,
    select_patch,
    verify_patch,
)
from .python_test_runner import (
    PythonTestRunner,
    extract_syntax_error_summary,
    safe_hidden_failure_summary,
    summarize_python_test_failure,
)
from .task_contract import extract_task_contract_checklist
from .test_assertion_checklist import extract_test_assertion_checklist
from .test_io_examples import (
    classify_boolean_contract_examples,
    check_example_literal_coverage,
    classify_boolean_string_examples,
    extract_test_io_examples,
    infer_failed_test_io_examples,
)

__all__ = [
    "CodeTestCase",
    "CodingPatch",
    "CodingPatchNormalizationError",
    "CodingPatchNormalizer",
    "CodingTask",
    "JsonPatchParser",
    "PythonTestRunner",
    "build_patch_prompt",
    "build_format_retry_prompt",
    "build_repair_prompt",
    "build_repair_plan_prompt",
    "select_patch",
    "apply_patch_to_task_files",
    "check_bool_before_string_normalization",
    "check_dict_list_contract_shape",
    "check_duplicate_suffix_format",
    "extract_forbidden_patterns_from_failed_code",
    "extract_literal_format_hints",
    "extract_repo_contract_repair_hints",
    "extract_structural_repair_hints",
    "extract_test_assertion_checklist",
    "extract_test_io_examples",
    "infer_failed_test_io_examples",
    "check_example_literal_coverage",
    "classify_boolean_contract_examples",
    "classify_boolean_string_examples",
    "patch_fingerprint",
    "verify_patch",
    "summarize_python_test_failure",
    "extract_syntax_error_summary",
    "safe_hidden_failure_summary",
    "extract_task_contract_checklist",
]
