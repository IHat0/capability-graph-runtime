"""Small deterministic SWE-style task suite for local pipeline validation."""

from cgr.kernel.coding import CodeTestCase

from .swe_task import SWETask


def create_local_swe_tasks() -> list[SWETask]:
    """Return local tasks used to exercise all A/B execution modes."""
    return [
        SWETask(
            id="local.greeting",
            issue='Change the program so it prints "hello CGR".',
            files={"app.py": 'print("hello")\n'},
            expected_files={"app.py": 'print("hello CGR")\n'},
            test_files={
                "test_task.py": (
                    "import subprocess, sys\n"
                    "result = subprocess.run([sys.executable, 'app.py'], "
                    "capture_output=True, text=True)\n"
                    "assert result.stdout == 'hello CGR\\n'\n"
                )
            },
            test_commands=[
                CodeTestCase(
                    name="run_greeting_test", command=["python", "test_task.py"]
                )
            ],
        ),
        SWETask(
            id="local.add",
            issue="Fix add so it returns a + b.",
            files={"math_utils.py": "def add(a, b):\n    return a - b\n"},
            expected_files={
                "math_utils.py": "def add(a, b):\n    return a + b\n"
            },
            test_files={
                "test_task.py": (
                    "from math_utils import add\n"
                    "assert add(1, 2) == 3\n"
                    "assert add(-5, 5) == 0\n"
                    "assert add(10, -3) == 7\n"
                )
            },
            test_commands=[
                CodeTestCase(name="run_add_test", command=["python", "test_task.py"])
            ],
        ),
        SWETask(
            id="local.is_even",
            issue=(
                "Fix is_even so it returns True for even numbers and False "
                "for odd numbers."
            ),
            files={
                "number_utils.py": "def is_even(n):\n    return n % 2 == 1\n"
            },
            expected_files={
                "number_utils.py": "def is_even(n):\n    return n % 2 == 0\n"
            },
            test_files={
                "test_task.py": (
                    "from number_utils import is_even\n"
                    "assert is_even(2) is True\n"
                    "assert is_even(3) is False\n"
                    "assert is_even(0) is True\n"
                    "assert is_even(-4) is True\n"
                )
            },
            test_commands=[
                CodeTestCase(
                    name="run_is_even_test", command=["python", "test_task.py"]
                )
            ],
        ),
    ]
