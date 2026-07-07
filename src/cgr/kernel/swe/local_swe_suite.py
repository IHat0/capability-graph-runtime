"""Small deterministic SWE-style task suite for local pipeline validation."""

from .swe_task import SWETask


def create_local_swe_tasks() -> list[SWETask]:
    """Return local tasks used to exercise all A/B execution modes."""
    return [
        SWETask(
            id="local.greeting",
            issue="Change the greeting from hello to hello CGR.",
            files={"app.py": 'print("hello")\n'},
            expected_files={"app.py": 'print("hello CGR")\n'},
        )
    ]
