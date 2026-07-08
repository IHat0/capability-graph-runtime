import json
from pathlib import Path

import pytest

from cgr.apps.cli.main import (
    benchmark_main,
    boost_local_main,
    coding_ab_local_main,
    coding_ab_hard_main,
    coding_ab_real_main,
    demo_main,
    main,
    model_demo_main,
    openai_benchmark_main,
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


def test_openai_benchmark_main_reports_missing_key_without_runtime(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def fail_create_runtime(**_: bool) -> None:
        raise AssertionError("runtime must not be created without an API key")

    monkeypatch.setattr("cgr.apps.cli.main.create_runtime", fail_create_runtime)

    exit_code = openai_benchmark_main([])

    assert json.loads(capsys.readouterr().out) == {
        "error": "OPENAI_API_KEY is not set."
    }
    assert exit_code == 1


def test_openai_benchmark_main_parses_export_args_before_missing_key(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    json_path = tmp_path / "openai.json"
    markdown_path = tmp_path / "openai.md"

    exit_code = openai_benchmark_main(
        [
            "--json-out",
            str(json_path),
            "--markdown-out",
            str(markdown_path),
        ]
    )

    assert json.loads(capsys.readouterr().out) == {
        "error": "OPENAI_API_KEY is not set."
    }
    assert not json_path.exists()
    assert not markdown_path.exists()
    assert exit_code == 1


def test_coding_ab_local_main_prints_valid_evaluation_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = coding_ab_local_main()

    output = json.loads(capsys.readouterr().out)
    assert output["suite_name"] == "local_coding_ab"
    assert output["total_tasks"] == 3
    assert output["pass_rates"] == {
        "baseline": pytest.approx(1 / 3),
        "cgr_single": pytest.approx(2 / 3),
        "cgr_multi": 1.0,
    }
    assert output["deltas"] == {
        "cgr_single_minus_baseline": pytest.approx(1 / 3),
        "cgr_multi_minus_baseline": pytest.approx(2 / 3),
    }
    passed_by_mode = {
        mode: sum(
            result["passed"]
            for result in output["results"]
            if result["mode"] == mode
        )
        for mode in ("baseline", "cgr_single", "cgr_multi")
    }
    assert passed_by_mode == {"baseline": 1, "cgr_single": 2, "cgr_multi": 3}
    assert output["deltas"]["cgr_single_minus_baseline"] > 0
    assert output["deltas"]["cgr_multi_minus_baseline"] > 0
    assert output["pass_rates"]["cgr_multi"] > output["pass_rates"]["cgr_single"]
    assert exit_code == 0


def test_coding_ab_real_main_reports_missing_environment_as_json(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for prefix in ("CGR_DRAFT", "CGR_CRITIC"):
        for suffix in ("API_KEY", "MODEL", "BASE_URL", "PROVIDER_NAME"):
            monkeypatch.delenv(f"{prefix}_{suffix}", raising=False)

    exit_code = coding_ab_real_main()

    assert json.loads(capsys.readouterr().out) == {
        "error": "CGR_DRAFT_API_KEY is not set."
    }
    assert exit_code == 1


def test_coding_ab_hard_main_reports_missing_environment_as_json(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for prefix in ("CGR_DRAFT", "CGR_CRITIC"):
        for suffix in ("API_KEY", "MODEL", "BASE_URL", "PROVIDER_NAME"):
            monkeypatch.delenv(f"{prefix}_{suffix}", raising=False)

    exit_code = coding_ab_hard_main([])

    assert json.loads(capsys.readouterr().out) == {
        "error": "CGR_DRAFT_API_KEY is not set."
    }
    assert exit_code == 1


def test_boost_local_main_prints_improved_scores(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = boost_local_main()

    output = json.loads(capsys.readouterr().out)
    assert output["suite_name"] == "local_booster"
    assert output["total_tasks"] == 3
    assert output["baseline_average_score"] < output[
        "boosted_single_average_score"
    ]
    assert output["boosted_single_average_score"] < output[
        "boosted_multi_average_score"
    ]
    assert exit_code == 0


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
