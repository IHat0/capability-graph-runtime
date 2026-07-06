import pytest
from pydantic import ValidationError

from cgr.kernel.contracts import (
    ExecutionContext,
    ExecutionRequest,
    ExecutionStatus,
)
from cgr.kernel.learning import (
    ExecutionObservation,
    LearningMemory,
    PluginPerformance,
)
from cgr.kernel.runtime import create_runtime
from cgr.shared.events import Event, EventType


def make_observation(
    execution_id: str,
    plugin_id: str = "echo",
    capability_id: str = "echo",
    status: ExecutionStatus = ExecutionStatus.SUCCESS,
    duration_ms: float = 0.0,
) -> ExecutionObservation:
    return ExecutionObservation(
        execution_id=execution_id,
        capability_id=capability_id,
        plugin_id=plugin_id,
        status=status,
        duration_ms=duration_ms,
    )


def make_performance(success_rate: float = 1.0) -> PluginPerformance:
    return PluginPerformance(
        capability_id="echo",
        plugin_id="echo",
        total_executions=1,
        successful_executions=1,
        failed_executions=0,
        average_duration_ms=1.0,
        success_rate=success_rate,
    )


def test_execution_observation_is_immutable_and_rejects_negative_duration() -> None:
    observation = make_observation("execution-1")

    with pytest.raises(ValidationError):
        observation.plugin_id = "changed"
    with pytest.raises(ValidationError):
        make_observation("execution-2", duration_ms=-1.0)


@pytest.mark.parametrize("success_rate", [-0.1, 1.1])
def test_plugin_performance_validates_success_rate(success_rate: float) -> None:
    with pytest.raises(ValidationError):
        make_performance(success_rate)


def test_plugin_performance_is_immutable() -> None:
    performance = make_performance()

    with pytest.raises(ValidationError):
        performance.total_executions = 2


def test_record_stores_observation_and_observations_returns_copy() -> None:
    memory = LearningMemory()
    observation = make_observation("execution-1")
    memory.record(observation)

    observations = memory.observations()
    observations.clear()

    assert memory.observations() == [observation]


def test_observations_for_capability_filters_records() -> None:
    memory = LearningMemory()
    echo = make_observation("echo-1")
    reasoning = make_observation("reasoning-1", capability_id="reasoning")
    memory.record(echo)
    memory.record(reasoning)

    assert memory.observations_for_capability("echo") == [echo]


def test_performance_for_computes_execution_statistics() -> None:
    memory = LearningMemory()
    memory.record(make_observation("success", duration_ms=10.0))
    memory.record(
        make_observation(
            "failure",
            status=ExecutionStatus.FAILED,
            duration_ms=30.0,
        )
    )

    performance = memory.performance_for("echo", "echo")

    assert performance.total_executions == 2
    assert performance.successful_executions == 1
    assert performance.failed_executions == 1
    assert performance.average_duration_ms == 20.0
    assert performance.success_rate == 0.5


def test_performance_for_returns_zeroed_performance_without_observations() -> None:
    performance = LearningMemory().performance_for("echo", "missing")

    assert performance == PluginPerformance(
        capability_id="echo",
        plugin_id="missing",
        total_executions=0,
        successful_executions=0,
        failed_executions=0,
        average_duration_ms=0.0,
        success_rate=0.0,
    )


def test_rank_plugins_orders_by_success_rate_descending() -> None:
    memory = LearningMemory()
    memory.record(make_observation("one", plugin_id="reliable"))
    memory.record(make_observation("two", plugin_id="mixed"))
    memory.record(
        make_observation(
            "three",
            plugin_id="mixed",
            status=ExecutionStatus.FAILED,
        )
    )

    assert [item.plugin_id for item in memory.rank_plugins("echo")] == [
        "reliable",
        "mixed",
    ]


def test_rank_plugins_breaks_success_tie_by_lower_duration() -> None:
    memory = LearningMemory()
    memory.record(make_observation("slow", plugin_id="slow", duration_ms=20.0))
    memory.record(make_observation("fast", plugin_id="fast", duration_ms=5.0))

    assert [item.plugin_id for item in memory.rank_plugins("echo")] == [
        "fast",
        "slow",
    ]


def test_rank_plugins_breaks_duration_tie_by_plugin_id() -> None:
    memory = LearningMemory()
    memory.record(make_observation("beta", plugin_id="beta", duration_ms=5.0))
    memory.record(make_observation("alpha", plugin_id="alpha", duration_ms=5.0))

    assert [item.plugin_id for item in memory.rank_plugins("echo")] == [
        "alpha",
        "beta",
    ]


def test_consume_event_records_completed_execution() -> None:
    memory = LearningMemory()
    memory.consume_event(
        Event(
            type=EventType.EXECUTION_COMPLETED,
            source="test",
            execution_id="completed-1",
            payload={
                "plugin_id": "echo",
                "capability_id": "echo",
                "status": "success",
                "duration_ms": 12.5,
            },
        )
    )

    assert memory.observations() == [
        make_observation("completed-1", duration_ms=12.5)
    ]


def test_consume_event_records_failed_execution() -> None:
    memory = LearningMemory()
    memory.consume_event(
        Event(
            type=EventType.EXECUTION_FAILED,
            source="test",
            execution_id="failed-1",
            payload={
                "plugin_id": "echo",
                "capability_id": "echo",
                "error_type": "RuntimeError",
                "error_message": "exploded",
            },
        )
    )

    observation = memory.observations()[0]
    assert observation.status == ExecutionStatus.FAILED
    assert observation.duration_ms == 0.0
    assert observation.error_type == "RuntimeError"
    assert observation.error_message == "exploded"


def test_consume_event_ignores_non_execution_event() -> None:
    memory = LearningMemory()

    memory.consume_event(
        Event(type=EventType.PLUGIN_REGISTERED, source="test")
    )

    assert memory.observations() == []


def test_learning_memory_consumes_runtime_execution_events() -> None:
    runtime = create_runtime(include_examples=True)
    memory = LearningMemory()
    runtime.event_bus.subscribe(
        EventType.EXECUTION_COMPLETED,
        memory.consume_event,
    )
    runtime.event_bus.subscribe(
        EventType.EXECUTION_FAILED,
        memory.consume_event,
    )
    capability = runtime.registry.get("echo").metadata.capabilities[0]
    request = ExecutionRequest[dict[str, str]](
        capability=capability,
        context=ExecutionContext(execution_id="integration-1"),
        payload={"message": "learn this"},
    )

    runtime.execute_capability(request)

    observation = memory.observations()[0]
    assert observation.execution_id == "integration-1"
    assert observation.status == ExecutionStatus.SUCCESS
    ranking = memory.rank_plugins("echo")
    assert len(ranking) == 1
    assert ranking[0].plugin_id == "echo"
    assert ranking[0].success_rate == 1.0
