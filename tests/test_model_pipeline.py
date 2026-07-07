import pytest
from pydantic import ValidationError

from cgr.kernel.pipeline import ModelPipeline, ModelPipelineResult
from cgr.kernel.runtime import create_runtime
from cgr.shared.events import EventType


def test_model_pipeline_result_is_immutable_and_rejects_empty_prompt() -> None:
    result = ModelPipelineResult(prompt="prompt")

    with pytest.raises(ValidationError):
        result.verified = True
    with pytest.raises(ValidationError):
        ModelPipelineResult(prompt="")


def test_model_pipeline_runs_reasoning_coding_fusion_and_verification() -> None:
    runtime = create_runtime(include_mock_models=True)

    result = ModelPipeline(runtime).run("Build a tiny calculator.")

    assert isinstance(result, ModelPipelineResult)
    assert result.prompt == "Build a tiny calculator."
    assert result.reasoning_output is not None
    assert result.reasoning_output["model_id"] == "mock.reasoning_model"
    assert result.reasoning_output["text"] == (
        "Reasoned answer: Build a tiny calculator."
    )
    assert result.coding_output is not None
    assert result.coding_output["model_id"] == "mock.coding_model"
    assert result.coding_output["text"] == (
        "Code response: Build a tiny calculator."
    )
    assert result.fused_output is not None
    assert result.verified is True


def test_model_pipeline_rejects_empty_prompt() -> None:
    with pytest.raises(ValueError, match="Prompt must not be empty"):
        ModelPipeline(create_runtime(include_mock_models=True)).run("")


def test_model_pipeline_tolerates_missing_reasoning_plugin() -> None:
    runtime = create_runtime(include_mock_models=True)
    runtime.unregister_plugin("mock.reasoning_model")

    result = ModelPipeline(runtime).run("prompt")

    assert result.reasoning_output is None
    assert result.coding_output is not None
    assert result.fused_output is None
    assert result.verified is False


def test_model_pipeline_tolerates_missing_coding_plugin() -> None:
    runtime = create_runtime(include_mock_models=True)
    runtime.unregister_plugin("mock.coding_model")

    result = ModelPipeline(runtime).run("prompt")

    assert result.reasoning_output is not None
    assert result.coding_output is None
    assert result.fused_output is not None
    assert result.verified is False


def test_model_pipeline_emits_runtime_execution_events() -> None:
    runtime = create_runtime(include_mock_models=True)

    ModelPipeline(runtime).run("prompt")

    assert len(
        runtime.event_bus.history_by_type(EventType.EXECUTION_STARTED)
    ) == 3
    assert len(
        runtime.event_bus.history_by_type(EventType.EXECUTION_COMPLETED)
    ) == 3
