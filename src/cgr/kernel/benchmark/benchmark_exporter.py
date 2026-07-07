"""File export for benchmark suite results."""

import json
from pathlib import Path

from .benchmark_report import BenchmarkReport
from .benchmark_suite_result import BenchmarkSuiteResult


class BenchmarkExporter:
    """Write machine-readable and Markdown benchmark artifacts."""

    def write_json(
        self,
        result: BenchmarkSuiteResult,
        path: str | Path,
    ) -> Path:
        """Write formatted benchmark JSON and return its path."""
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )
        return output_path

    def write_markdown(
        self,
        result: BenchmarkSuiteResult,
        path: str | Path,
    ) -> Path:
        """Write a Markdown benchmark report and return its path."""
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            BenchmarkReport(result).to_markdown(),
            encoding="utf-8",
        )
        return output_path
