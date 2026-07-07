"""SWE-style coding evaluation contracts and runner."""

from .local_swe_suite import create_local_swe_tasks
from .swe_ab_runner import SWEABRunner
from .swe_case_result import SWECaseResult
from .swe_eval_result import SWEEvalResult
from .swe_task import SWETask

__all__ = [
    "SWEABRunner",
    "SWECaseResult",
    "SWEEvalResult",
    "SWETask",
    "create_local_swe_tasks",
]
