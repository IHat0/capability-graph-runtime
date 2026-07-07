"""Human-readable reporting for benchmark suite results."""

from typing import Any

from .benchmark_suite_result import BenchmarkSuiteResult


class BenchmarkReport:
    """Create deterministic summaries and Markdown benchmark reports."""

    def __init__(self, result: BenchmarkSuiteResult) -> None:
        self._result = result

    def summary_dict(self) -> dict[str, Any]:
        """Return public-facing benchmark summary statistics."""
        return {
            "suite_name": self._result.suite_name,
            "total_tasks": self._result.total_tasks,
            "succeeded_tasks": self._result.succeeded_tasks,
            "verified_tasks": self._result.verified_tasks,
            "failed_tasks": self._result.failed_tasks,
            "success_rate": self._result.success_rate,
            "verification_rate": self._result.verification_rate,
            "average_duration_ms": self._result.average_duration_ms,
        }

    def to_markdown(self) -> str:
        """Return a deterministic Markdown benchmark report."""
        result = self._result
        lines = [
            "# CGR Benchmark Report",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|---|---:|",
            f"| Suite | {self._escape(result.suite_name)} |",
            f"| Total tasks | {result.total_tasks} |",
            f"| Succeeded tasks | {result.succeeded_tasks} |",
            f"| Verified tasks | {result.verified_tasks} |",
            f"| Failed tasks | {result.failed_tasks} |",
            f"| Success rate | {result.success_rate:.2%} |",
            f"| Verification rate | {result.verification_rate:.2%} |",
            f"| Average duration | {result.average_duration_ms:.2f} ms |",
            "",
            "## Case Results",
            "",
            "| Task | Capability | Plugin | Succeeded | Verified | Duration |",
            "|---|---|---|---:|---:|---:|",
        ]
        for case in result.results:
            plugin_id = case.plugin_id if case.plugin_id is not None else "-"
            lines.append(
                "| "
                f"{self._escape(case.task_id)} | "
                f"{self._escape(case.capability_id)} | "
                f"{self._escape(plugin_id)} | "
                f"{str(case.succeeded).lower()} | "
                f"{str(case.verified).lower()} | "
                f"{case.duration_ms:.2f} ms |"
            )

        errors = [
            case
            for case in result.results
            if case.error_type is not None or case.error_message is not None
        ]
        if errors:
            lines.extend(
                [
                    "",
                    "## Errors",
                    "",
                    "| Task | Error |",
                    "|---|---|",
                ]
            )
            for case in errors:
                error = (
                    f"{case.error_type or '-'}: {case.error_message or '-'}"
                )
                lines.append(
                    f"| {self._escape(case.task_id)} | "
                    f"{self._escape(error)} |"
                )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _escape(value: str) -> str:
        """Escape text for a single Markdown table cell."""
        return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
