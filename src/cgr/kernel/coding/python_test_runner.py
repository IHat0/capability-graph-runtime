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
                        f"{test_case.name}: timed out after {timeout_seconds:.2f}s."
                    )
                    messages.extend(self._output_messages(exc.stdout, exc.stderr))
                    return False, messages
                messages.append(
                    f"{test_case.name}: exit code {completed.returncode} "
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
    def _output_messages(
        stdout: str | bytes | None, stderr: str | bytes | None
    ) -> list[str]:
        messages: list[str] = []
        for label, output in (("stdout", stdout), ("stderr", stderr)):
            if not output:
                continue
            text = output.decode(errors="replace") if isinstance(output, bytes) else output
            messages.append(f"{label}: {text.strip()[:1000]}")
        return messages
