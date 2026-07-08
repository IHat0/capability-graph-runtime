"""Temporary-directory Python test execution for local MVP verification."""

import subprocess
import tempfile
from pathlib import Path

from .code_test_case import CodeTestCase


class PythonTestRunner:
    """Run explicit test commands against generated files in a temporary tree."""

    def run(
        self,
        files: dict[str, str],
        test_files: dict[str, str],
        commands: list[CodeTestCase],
        timeout_seconds: float = 10.0,
    ) -> tuple[bool, list[str]]:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        messages: list[str] = []
        with tempfile.TemporaryDirectory(prefix="cgr-code-test-") as directory:
            root = Path(directory).resolve()
            self._write_files(root, files)
            syntax_messages = self._compile_generated_python(files)
            if syntax_messages:
                return False, syntax_messages
            self._write_files(root, test_files)
            for test_case in commands:
                try:
                    completed = subprocess.run(
                        test_case.command,
                        cwd=root,
                        capture_output=True,
                        text=True,
                        timeout=timeout_seconds,
                        check=False,
                        shell=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    messages.append(
                        f"{test_case.name}: command {test_case.command!r}; timed out "
                        f"after {timeout_seconds:.2f}s."
                    )
                    messages.extend(self._output_messages(exc.stdout, exc.stderr))
                    return False, messages
                messages.append(
                    f"{test_case.name}: command {test_case.command!r}; "
                    f"exit code {completed.returncode} "
                    f"(expected {test_case.expected_exit_code})."
                )
                messages.extend(
                    self._output_messages(completed.stdout, completed.stderr)
                )
                if completed.returncode != test_case.expected_exit_code:
                    return False, messages
        return True, messages

    @staticmethod
    def _write_files(root: Path, files: dict[str, str]) -> None:
        for relative_name, content in files.items():
            path = (root / relative_name).resolve()
            if not path.is_relative_to(root) or path == root:
                raise ValueError(f"File path escapes test directory: {relative_name}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    @staticmethod
    def _compile_generated_python(files: dict[str, str]) -> list[str]:
        messages: list[str] = []
        for relative_name, content in files.items():
            if not relative_name.endswith(".py"):
                continue
            try:
                compile(content, relative_name, "exec")
            except (SyntaxError, IndentationError, TabError) as exc:
                error_type = type(exc).__name__
                line_number = exc.lineno or 0
                line_text = (exc.text or "").strip()
                messages.append(
                    "compile_generated_python: command ['compile', "
                    f"{relative_name!r}]; exit code 1 (expected 0)."
                )
                messages.append(
                    f"stderr: {error_type}: {exc.msg} in {relative_name}, "
                    f"line {line_number}\nOffending line: {line_text}"
                )
                break
        return messages

    @staticmethod
    def _output_messages(
        stdout: str | bytes | None, stderr: str | bytes | None
    ) -> list[str]:
        messages: list[str] = []
        for label, output, limit in (
            ("stdout", stdout, 2000),
            ("stderr", stderr, 4000),
        ):
            if not output:
                continue
            text = output.decode(errors="replace") if isinstance(output, bytes) else output
            messages.append(f"{label}: {text.strip()[-limit:]}")
        return messages


def summarize_python_test_failure(messages: list[str]) -> str:
    """Extract assertion and traceback signals plus the final diagnostic lines."""
    lines = [line.strip() for message in messages for line in message.splitlines()]
    non_empty = [line for line in lines if line]
    priority_markers = (
        "expected",
        "got",
        "must be summed",
        "not overwritten",
        "must not be mutated",
        "AssertionError",
        "assert ",
    )
    secondary_markers = (
        "E       ",
        "Traceback",
        "Expected",
        "==",
        "SyntaxError",
        "IndentationError",
        "TabError",
        "NameError",
    )
    selected = [
        line
        for line in non_empty
        if "AssertionError:" in line and "expected" in line and "got" in line
    ]
    selected.extend(
        line
        for line in non_empty
        if "expected" in line and "got" in line
    )
    selected.extend(
        line
        for marker in priority_markers
        for line in non_empty
        if marker in line
    )
    selected.extend(
        line
        for marker in secondary_markers
        for line in non_empty
        if marker in line
    )
    selected.extend(non_empty[-20:])
    deduplicated = list(dict.fromkeys(selected))
    return "\n".join(deduplicated)


def extract_syntax_error_summary(messages: list[str]) -> str | None:
    """Return filename/line/offending-code context for parse and obvious typo errors."""
    lines = [line.rstrip() for message in messages for line in message.splitlines()]
    error_types = ("SyntaxError", "IndentationError", "TabError", "NameError")
    error_indexes = [
        index
        for index, line in enumerate(lines)
        if any(error_type in line for error_type in error_types)
    ]
    if not error_indexes:
        return None
    index = error_indexes[-1]
    start = max(0, index - 5)
    context = [line.strip() for line in lines[start : index + 1] if line.strip()]
    return "\n".join(context)[-2000:]


def safe_hidden_failure_summary(messages: list[str]) -> str:
    """Keep actionable exception/assertion text without exposing hidden source."""
    lines = [line.strip() for message in messages for line in message.splitlines()]
    safe_markers = (
        "AssertionError:",
        "SyntaxError:",
        "IndentationError:",
        "TabError:",
        "NameError:",
        "TypeError:",
        "ValueError:",
        "expected",
        "got",
    )
    selected = [line for line in lines if any(marker in line for marker in safe_markers)]
    return "\n".join(dict.fromkeys(selected))[-2000:] or "Hidden test command failed."
