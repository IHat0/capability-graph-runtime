from typing import Any

import pytest
from pydantic import ValidationError

from cgr.kernel.contracts import (
    Capability,
    CapabilityVersion,
    ExecutionContext,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from cgr.kernel.exceptions import CapabilityNotFoundError
from cgr.kernel.fusion import FusionEngine, FusionResult, FusionStrategy
from cgr.kernel.runtime import KernelRuntime
from cgr.plugins.examples import EchoPlugin
from cgr.shared.events import EventType


def make_request() -> ExecutionRequest[dict[str, str]]:
    return ExecutionRequest(
        capability=Capability(
            id="echo",
            name="Echo",
            version=CapabilityVersion(major=1, minor=0, patch=0),
        ),
        context=ExecutionContext(execution_id="fusion-1"),
        payload={"message": "fuse"},
    )


class IdentifiedEchoPlugin(EchoPlugin):
    """Echo plugin with a configurable identifier."""

    def __init__(self, plugin_id: str) -> None:
        super().__init__()
        self._metadata = self.metadata.model_copy(update={"id": plugin_id})


class OutputPlugin(IdentifiedEchoPlugin):
    """Successful plugin returning a configured output."""

    def __init__(self, plugin_id: str, output: Any) -> None:
        super().__init__(plugin_id)
        self._output = output

    def execute(
        self,
        request: ExecutionRequest[Any],
    ) -> ExecutionResult[Any]:
        return ExecutionResult(
            context=request.context,
            status=ExecutionStatus.SUCCESS,
            output=self._output,
        )


class FailedResultPlugin(IdentifiedEchoPlugin):
    """Plugin returning a structured failed result."""

    def execute(
        self,
        request: ExecutionRequest[Any],
    ) -> ExecutionResult[Any]:
        return ExecutionResult(
            context=request.context,
            status=ExecutionStatus.FAILED,
            output=None,
            error="failed result",
        )


class RaisingPlugin(IdentifiedEchoPlugin):
    """Plugin raising during execution."""

    def execute(
        self,
        request: ExecutionRequest[Any],
    ) -> ExecutionResult[Any]:
        raise RuntimeError("fusion candidate exploded")


def test_fusion_strategy_values() -> None:
    assert [strategy.value for strategy in FusionStrategy] == [
        "first_success",
        "collect_all",
    ]


def test_fusion_result_is_immutable() -> None:
    result = FusionResult(
        capability_id="echo",
        strategy=FusionStrategy.COLLECT_ALL,
        attempted_plugin_ids=[],
        successful_plugin_ids=[],
        failed_plugin_ids=[],
        fused_output=[],
    )

    with pytest.raises(ValidationError):
        result.strategy = FusionStrategy.FIRST_SUCCESS


def test_fuse_raises_when_no_plugin_supports_capability() -> None:
    engine = FusionEngine(KernelRuntime())

    with pytest.raises(
        CapabilityNotFoundError,
        match="No plugin registered for capability 'echo'",
    ):
        engine.fuse(make_request())


@pytest.mark.parametrize(
    ("strategy", "expected_output"),
    [
        (FusionStrategy.COLLECT_ALL, [{"message": "fuse"}]),
        (FusionStrategy.FIRST_SUCCESS, {"message": "fuse"}),
    ],
)
def test_fuse_single_echo_uses_requested_strategy(
    strategy: FusionStrategy,
    expected_output: Any,
) -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(EchoPlugin())

    result = FusionEngine(runtime).fuse(make_request(), strategy)

    assert result.fused_output == expected_output
    assert result.attempted_plugin_ids == ["echo"]
    assert result.successful_plugin_ids == ["echo"]
    assert result.failed_plugin_ids == []


def test_collect_all_returns_outputs_in_registry_order_and_emits_events() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(OutputPlugin("first", "first output"))
    runtime.register_plugin(OutputPlugin("second", "second output"))

    result = FusionEngine(runtime).fuse(make_request())

    assert result.fused_output == ["first output", "second output"]
    assert result.attempted_plugin_ids == ["first", "second"]
    assert result.successful_plugin_ids == ["first", "second"]
    assert len(
        runtime.event_bus.history_by_type(EventType.EXECUTION_STARTED)
    ) == 2
    assert len(
        runtime.event_bus.history_by_type(EventType.EXECUTION_COMPLETED)
    ) == 2


def test_first_success_returns_first_output_but_executes_all_plugins() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(OutputPlugin("first", "first output"))
    runtime.register_plugin(OutputPlugin("second", "second output"))

    result = FusionEngine(runtime).fuse(
        make_request(),
        FusionStrategy.FIRST_SUCCESS,
    )

    assert result.fused_output == "first output"
    assert result.attempted_plugin_ids == ["first", "second"]
    assert result.successful_plugin_ids == ["first", "second"]


def test_fuse_records_failed_result_and_continues_after_exception() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(FailedResultPlugin("failed"))
    runtime.register_plugin(RaisingPlugin("raising"))
    runtime.register_plugin(OutputPlugin("success", "success output"))

    result = FusionEngine(runtime).fuse(make_request())

    assert result.attempted_plugin_ids == ["failed", "raising", "success"]
    assert result.failed_plugin_ids == ["failed", "raising"]
    assert result.successful_plugin_ids == ["success"]
    assert result.fused_output == ["success output"]
    assert len(
        runtime.event_bus.history_by_type(EventType.EXECUTION_FAILED)
    ) == 1


@pytest.mark.parametrize(
    ("strategy", "expected_output"),
    [
        (FusionStrategy.COLLECT_ALL, []),
        (FusionStrategy.FIRST_SUCCESS, None),
    ],
)
def test_fuse_without_success_returns_strategy_empty_value(
    strategy: FusionStrategy,
    expected_output: Any,
) -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(FailedResultPlugin("failed"))
    runtime.register_plugin(RaisingPlugin("raising"))

    result = FusionEngine(runtime).fuse(make_request(), strategy)

    assert result.fused_output == expected_output
    assert result.successful_plugin_ids == []
    assert result.failed_plugin_ids == ["failed", "raising"]
    assert result.attempted_plugin_ids == ["failed", "raising"]
