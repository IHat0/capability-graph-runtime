"""Small deterministic SWE-style task suite for local pipeline validation."""

from .swe_task import SWETask


def create_local_swe_tasks() -> list[SWETask]:
    """Return local tasks used to exercise all A/B execution modes."""
    return [
        SWETask(
            id="local.greeting",
            issue='Change the program so it prints "hello CGR".',
            files={"app.py": 'print("hello")\n'},
            expected_files={"app.py": 'print("hello CGR")\n'},
        ),
        SWETask(
            id="local.add",
            issue="Fix add so it returns a + b.",
            files={"math_utils.py": "def add(a, b):\n    return a - b\n"},
            expected_files={
                "math_utils.py": "def add(a, b):\n    return a + b\n"
            },
        ),
        SWETask(
            id="local.is_even",
            issue=(
                "Fix is_even so it returns True for even numbers and False "
                "for odd numbers."
            ),
            files={"numbers.py": "def is_even(n):\n    return n % 2 == 1\n"},
            expected_files={
                "numbers.py": "def is_even(n):\n    return n % 2 == 0\n"
            },
        ),
    ]
