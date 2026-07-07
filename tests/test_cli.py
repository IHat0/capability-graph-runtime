import json
from pathlib import Path

import pytest

from cgr.apps.cli.main import (
    benchmark_main,
    demo_main,
    main,
    model_demo_main,
    openai_demo_main,
)


def test_main_prints_echo_payload_as_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main()

    output = capsys.readouterr().out.strip()
    assert json.loads(output) == {"message": "Hello CGR!"}
    assert output == '{"message": "Hello CGR!"}'
    assert exit_code == 0


def test_model_demo_main_prints_pipeline_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = model_demo_main()

    output = json.loads(capsys.readouterr().out)
    assert output["prompt"] == "Build a tiny calculator."
    assert output["reasoning_output"]["model_id"] == "mock.reasoning_model"
    assert output["coding_output"]["model_id"] == "mock.coding_model"
    assert output["verified"] is True
    assert exit_code == 0


def test_demo_main_prints_end_to_end_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = demo_main()

    output = json.loads(capsys.readouterr().out)
    assert set(output) == {
        "model_pipeline",
        "calculator",
        "text_stats",
        "runtime_health",
    }
    assert output["model_pipeline"]["verified"] is True
    assert output["calculator"] == {
        "expression": "1 + 2 * 3",
        "result": 7,
    }
    assert output["text_stats"]["word_count"] == 8
    assert output["runtime_health"]["healthy"] is True
    assert output["runtime_health"]["plugin_count"] == 5
    assert exit_code == 0


def test_openai_demo_main_reports_missing_key_as_json(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    exit_code = openai_demo_main()

    assert json.loads(capsys.readouterr().out) == {
        "error": "OPENAI_API_KEY is not set."
    }
    assert exit_code == 1


def test_benchmark_main_prints_json_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = benchmark_main([])

    output = json.loads(capsys.readouterr().out)
    assert set(output) == {
        "suite_name",
        "total_tasks",
        "succeeded_tasks",
        "verified_tasks",
        "failed_tasks",
        "average_duration_ms",
        "results",
    }
    assert output["suite_name"] == "CGR Local Benchmark"
    assert output["total_tasks"] == 6
    assert output["succeeded_tasks"] == 6
    assert output["verified_tasks"] == 6
    assert output["failed_tasks"] == 0
    assert len(output["results"]) == 6
    assert exit_code == 0


def test_benchmark_main_writes_json_file(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "exports" / "local.json"

    assert benchmark_main(["--json-out", str(output_path)]) == 0

    assert json.loads(output_path.read_text(encoding="utf-8"))["total_tasks"] == 6
    assert json.loads(capsys.readouterr().out)["total_tasks"] == 6


def test_benchmark_main_writes_markdown_file(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "exports" / "local.md"

    assert benchmark_main(["--markdown-out", str(output_path)]) == 0

    assert output_path.read_text(encoding="utf-8").startswith(
        "# CGR Benchmark Report"
    )
    assert json.loads(capsys.readouterr().out)["total_tasks"] == 6


def test_benchmark_main_writes_both_files_and_stdout_remains_json(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    json_path = tmp_path / "local.json"
    markdown_path = tmp_path / "local.md"

    assert (
        benchmark_main(
            [
                "--json-out",
                str(json_path),
                "--markdown-out",
                str(markdown_path),
            ]
        )
        == 0
    )

    assert json_path.is_file()
    assert markdown_path.is_file()
    assert json.loads(capsys.readouterr().out)["suite_name"] == (
        "CGR Local Benchmark"
    )
