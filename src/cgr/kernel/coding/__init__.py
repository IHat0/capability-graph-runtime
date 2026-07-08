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
    extract_forbidden_patterns_from_failed_code,
    patch_fingerprint,
    select_patch,
    verify_patch,
)
from .python_test_runner import PythonTestRunner, summarize_python_test_failure
from .test_assertion_checklist import extract_test_assertion_checklist
from .test_io_examples import (
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
    "extract_forbidden_patterns_from_failed_code",
    "extract_test_assertion_checklist",
    "extract_test_io_examples",
    "infer_failed_test_io_examples",
    "patch_fingerprint",
    "verify_patch",
    "summarize_python_test_failure",
]
