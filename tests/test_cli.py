import json

import pytest

from cgr.apps.cli.main import demo_main, main, model_demo_main


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
