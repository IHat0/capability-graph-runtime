import subprocess
import sys

import pytest
from pydantic import ValidationError

from cgr.kernel.coding import CodeTestCase, PythonTestRunner


def test_code_test_case_is_immutable_and_validates_required_fields() -> None:
    test_case = CodeTestCase(name="test", command=["python", "test.py"])

    with pytest.raises(ValidationError):
        test_case.name = "changed"
    with pytest.raises(ValidationError):
        CodeTestCase(name="", command=["python"])
    with pytest.raises(ValidationError):
        CodeTestCase(name="test", command=[])


def test_python_test_runner_passes_functionally_correct_multiple_files() -> None:
    passed, messages = PythonTestRunner().run(
        {
            "package/__init__.py": "",
            "package/math_utils.py": (
                "def add(a: float, b: float) -> float:\n"
                "    \"\"\"Return the sum.\"\"\"\n"
                "    return a + b\n"
            ),
        },
        {
            "test_task.py": (
                "from package.math_utils import add\n"
                "assert add(1, 2) == 3\n"
                "print('functional pass')\n"
            )
        },
        [CodeTestCase(name="functional", command=[sys.executable, "test_task.py"])],
    )

    assert passed is True
    assert any("exit code 0" in message for message in messages)
    assert any("functional pass" in message for message in messages)


def test_python_test_runner_reports_failure_and_stderr() -> None:
    passed, messages = PythonTestRunner().run(
        {"value.py": "VALUE = 1\n"},
        {
            "test_task.py": (
                "import sys\nfrom value import VALUE\n"
                "print('diagnostic', file=sys.stderr)\nassert VALUE == 2\n"
            )
        },
        [CodeTestCase(name="failure", command=[sys.executable, "test_task.py"])],
    )

    assert passed is False
    assert any("exit code 1" in message for message in messages)
    assert any("diagnostic" in message for message in messages)


def test_python_test_runner_reports_timeout() -> None:
    passed, messages = PythonTestRunner().run(
        {},
        {"test_task.py": "import time\ntime.sleep(2)\n"},
        [CodeTestCase(name="timeout", command=[sys.executable, "test_task.py"])],
        timeout_seconds=0.01,
    )

    assert passed is False
    assert any("timed out" in message for message in messages)


def test_python_test_runner_explicitly_disables_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.update(kwargs)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    passed, _ = PythonTestRunner().run(
        {}, {}, [CodeTestCase(name="safe", command=["python", "test.py"])]
    )

    assert passed is True
    assert observed["shell"] is False
