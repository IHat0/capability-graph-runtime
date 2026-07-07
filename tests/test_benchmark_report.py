import json
from pathlib import Path

from cgr.kernel.benchmark import (
    BenchmarkCaseResult,
    BenchmarkExporter,
    BenchmarkReport,
    BenchmarkSuiteResult,
)


def _suite_result(*, with_error: bool = False) -> BenchmarkSuiteResult:
    cases = [
        BenchmarkCaseResult(
            task_id="calculator.simple_arithmetic",
            capability_id="calculator.evaluate",
            plugin_id=None,
            succeeded=True,
            verified=True,
            duration_ms=1.234,
            output={"result": 7},
        )
    ]
    if with_error:
        cases.append(
            BenchmarkCaseResult(
                task_id="calculator.invalid",
                capability_id="calculator.evaluate",
                plugin_id="builtin.calculator",
                succeeded=False,
                verified=False,
                duration_ms=0.5,
                error_type="ValueError",
                error_message="Invalid expression.",
            )
        )
    total = len(cases)
    return BenchmarkSuiteResult(
        suite_name="local",
        total_tasks=total,
        succeeded_tasks=1,
        verified_tasks=1,
        failed_tasks=total - 1,
        average_duration_ms=sum(case.duration_ms for case in cases) / total,
        results=cases,
    )


def test_summary_dict_includes_expected_statistics() -> None:
    summary = BenchmarkReport(_suite_result()).summary_dict()

    assert set(summary) == {
        "suite_name",
        "total_tasks",
        "succeeded_tasks",
        "verified_tasks",
        "failed_tasks",
        "success_rate",
        "verification_rate",
        "average_duration_ms",
    }
    assert summary["success_rate"] == 1.0
    assert summary["verification_rate"] == 1.0


def test_markdown_contains_report_tables_and_formats_values() -> None:
    markdown = BenchmarkReport(_suite_result()).to_markdown()

    assert markdown.startswith("# CGR Benchmark Report")
    assert "## Summary" in markdown
    assert "## Case Results" in markdown
    assert "| Success rate | 100.00% |" in markdown
    assert "| Verification rate | 100.00% |" in markdown
    assert "| calculator.simple_arithmetic | calculator.evaluate | - |" in markdown
    assert "1.23 ms" in markdown


def test_markdown_excludes_errors_section_without_errors() -> None:
    assert "## Errors" not in BenchmarkReport(_suite_result()).to_markdown()


def test_markdown_includes_errors_section_when_errors_exist() -> None:
    markdown = BenchmarkReport(_suite_result(with_error=True)).to_markdown()

    assert "## Errors" in markdown
    assert "ValueError: Invalid expression." in markdown


def test_write_json_creates_parent_directories_and_returns_path(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "results.json"

    returned = BenchmarkExporter().write_json(_suite_result(), path)

    assert returned == path
    assert json.loads(path.read_text(encoding="utf-8"))["suite_name"] == "local"


def test_write_markdown_creates_parent_directories_and_returns_path(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "results.md"

    returned = BenchmarkExporter().write_markdown(_suite_result(), path)

    assert returned == path
    assert path.read_text(encoding="utf-8").startswith(
        "# CGR Benchmark Report"
    )
