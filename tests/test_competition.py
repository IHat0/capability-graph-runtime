from typing import Any

import pytest
from pydantic import ValidationError

from cgr.kernel.competition import CompetitionEngine, CompetitionResult
from cgr.kernel.contracts import (
    Capability,
    CapabilityVersion,
    ExecutionContext,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from cgr.kernel.exceptions import CapabilityNotFoundError
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
        context=ExecutionContext(execution_id="competition-1"),
        payload={"message": "compete"},
    )


class IdentifiedEchoPlugin(EchoPlugin):
    """Successful Echo plugin with a configurable identifier."""

    def __init__(self, plugin_id: str) -> None:
        super().__init__()
        self._metadata = self.metadata.model_copy(update={"id": plugin_id})


class FailedResultPlugin(IdentifiedEchoPlugin):
    """Plugin that returns a structured failed result."""

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
    """Plugin that raises during execution."""

    def execute(
        self,
        request: ExecutionRequest[Any],
    ) -> ExecutionResult[Any]:
        raise RuntimeError("candidate exploded")


def test_competition_result_is_immutable() -> None:
    result = CompetitionResult(
        capability_id="echo",
        winner_plugin_id=None,
        attempted_plugin_ids=[],
        successful_plugin_ids=[],
        failed_plugin_ids=[],
        result=None,
    )

    with pytest.raises(ValidationError):
        result.winner_plugin_id = "changed"


def test_compete_raises_when_no_plugin_supports_capability() -> None:
    engine = CompetitionEngine(KernelRuntime())

    with pytest.raises(
        CapabilityNotFoundError,
        match="No plugin registered for capability 'echo'",
    ):
        engine.compete(make_request())


def test_compete_selects_single_echo_and_records_runtime_events() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(EchoPlugin())

    result = CompetitionEngine(runtime).compete(make_request())

    assert result.winner_plugin_id == "echo"
    assert result.attempted_plugin_ids == ["echo"]
    assert result.successful_plugin_ids == ["echo"]
    assert result.failed_plugin_ids == []
    assert result.result is not None
    assert result.result.output == {"message": "compete"}
    assert len(
        runtime.event_bus.history_by_type(EventType.EXECUTION_STARTED)
    ) == 1
    assert len(
        runtime.event_bus.history_by_type(EventType.EXECUTION_COMPLETED)
    ) == 1


def test_compete_records_failed_result_and_continues_to_success() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(FailedResultPlugin("failed"))
    runtime.register_plugin(IdentifiedEchoPlugin("success"))

    result = CompetitionEngine(runtime).compete(make_request())

    assert result.attempted_plugin_ids == ["failed", "success"]
    assert result.failed_plugin_ids == ["failed"]
    assert result.successful_plugin_ids == ["success"]
    assert result.winner_plugin_id == "success"


def test_compete_continues_after_plugin_raises() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(RaisingPlugin("raising"))
    runtime.register_plugin(IdentifiedEchoPlugin("success"))

    result = CompetitionEngine(runtime).compete(make_request())

    assert result.failed_plugin_ids == ["raising"]
    assert result.successful_plugin_ids == ["success"]
    assert result.winner_plugin_id == "success"
    assert len(
        runtime.event_bus.history_by_type(EventType.EXECUTION_FAILED)
    ) == 1


def test_compete_keeps_first_winner_when_multiple_plugins_succeed() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(IdentifiedEchoPlugin("first"))
    runtime.register_plugin(IdentifiedEchoPlugin("second"))

    result = CompetitionEngine(runtime).compete(make_request())

    assert result.winner_plugin_id == "first"
    assert result.successful_plugin_ids == ["first", "second"]
    assert result.attempted_plugin_ids == ["first", "second"]


def test_compete_returns_no_winner_when_all_candidates_fail() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(FailedResultPlugin("failed"))
    runtime.register_plugin(RaisingPlugin("raising"))

    result = CompetitionEngine(runtime).compete(make_request())

    assert result.winner_plugin_id is None
    assert result.result is None
    assert result.successful_plugin_ids == []
    assert result.failed_plugin_ids == ["failed", "raising"]
    assert result.attempted_plugin_ids == ["failed", "raising"]
