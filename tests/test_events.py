from typing import Any

import pytest
from pydantic import ValidationError

from cgr.kernel.contracts import (
    Capability,
    CapabilityVersion,
    ExecutionContext,
    ExecutionRequest,
    ExecutionResult,
    HealthStatus,
    Plugin,
    PluginMetadata,
    PluginState,
)
from cgr.kernel.registry import PluginRegistry
from cgr.kernel.exceptions import (
    CapabilityNotFoundError,
    PluginExecutionError,
    PluginNotFoundError,
)
from cgr.kernel.runtime import KernelRuntime
from cgr.plugins.examples import EchoPlugin
from cgr.shared.events import Event, EventBus, EventType


@pytest.fixture
def capability() -> Capability:
    return Capability(
        id="echo",
        name="Echo",
        version=CapabilityVersion(major=1, minor=0, patch=0),
    )


def make_event(event_type: EventType = EventType.EXECUTION_STARTED) -> Event:
    return Event(type=event_type, source="test")


def test_event_defaults_and_immutability() -> None:
    event = make_event()

    assert event.id
    assert event.timestamp is not None
    assert event.payload == {}

    with pytest.raises(ValidationError):
        event.source = "changed"


def test_event_payload_defaults_are_independent() -> None:
    first = make_event()
    second = make_event()

    assert first.payload is not second.payload


def test_subscribe_and_publish_calls_handler() -> None:
    bus = EventBus()
    received: list[Event] = []
    event = make_event()
    bus.subscribe(EventType.EXECUTION_STARTED, received.append)

    bus.publish(event)

    assert received == [event]


def test_multiple_handlers_are_called_in_subscription_order() -> None:
    bus = EventBus()
    calls: list[str] = []

    def first(event: Event) -> None:
        calls.append("first")

    def second(event: Event) -> None:
        calls.append("second")

    bus.subscribe(EventType.EXECUTION_STARTED, first)
    bus.subscribe(EventType.EXECUTION_STARTED, second)

    bus.publish(make_event())

    assert calls == ["first", "second"]


def test_unsubscribe_removes_handler() -> None:
    bus = EventBus()
    received: list[Event] = []
    bus.subscribe(EventType.EXECUTION_STARTED, received.append)

    bus.unsubscribe(EventType.EXECUTION_STARTED, received.append)
    bus.unsubscribe(EventType.EXECUTION_STARTED, received.append)
    bus.publish(make_event())

    assert received == []


def test_history_returns_a_copy_of_published_events() -> None:
    bus = EventBus()
    event = make_event()
    bus.publish(event)

    history = bus.history()
    history.clear()

    assert bus.history() == [event]


def test_history_by_type_filters_events() -> None:
    bus = EventBus()
    started = make_event(EventType.EXECUTION_STARTED)
    completed = make_event(EventType.EXECUTION_COMPLETED)
    bus.publish(started)
    bus.publish(completed)

    assert bus.history_by_type(EventType.EXECUTION_STARTED) == [started]


def test_clear_removes_history_but_keeps_subscribers() -> None:
    bus = EventBus()
    received: list[Event] = []
    bus.subscribe(EventType.EXECUTION_STARTED, received.append)
    bus.publish(make_event())

    bus.clear()
    next_event = make_event()
    bus.publish(next_event)

    assert bus.history() == [next_event]
    assert received == [received[0], next_event]


def test_runtime_publishes_started_and_completed_events(
    capability: Capability,
) -> None:
    registry = PluginRegistry()
    registry.register(EchoPlugin())
    event_bus = EventBus()
    runtime = KernelRuntime(registry=registry, event_bus=event_bus)
    request = ExecutionRequest[dict[str, str]](
        capability=capability,
        context=ExecutionContext(
            execution_id="execution-1",
            correlation_id="correlation-1",
        ),
        payload={"message": "Hello CGR!"},
    )

    result = runtime.execute("echo", request)

    assert result.output == {"message": "Hello CGR!"}
    started, completed = event_bus.history()
    assert started.type == EventType.EXECUTION_STARTED
    assert started.execution_id == "execution-1"
    assert started.correlation_id == "correlation-1"
    assert started.payload == {
        "plugin_id": "echo",
        "capability_id": "echo",
    }
    assert completed.type == EventType.EXECUTION_COMPLETED
    assert completed.execution_id == "execution-1"
    assert completed.payload == {
        "plugin_id": "echo",
        "capability_id": "echo",
        "status": "success",
    }


class FailingPlugin(Plugin[Any, Any]):
    """Test plugin that fails every execution."""

    def __init__(self, capability: Capability) -> None:
        self._metadata = PluginMetadata(
            id="failing",
            name="Failing Plugin",
            version="1.0.0",
            capabilities=[capability],
        )

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata

    @property
    def state(self) -> PluginState:
        return PluginState.RUNNING

    @property
    def health(self) -> HealthStatus:
        return HealthStatus.HEALTHY

    def initialize(self) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def execute(
        self,
        request: ExecutionRequest[Any],
    ) -> ExecutionResult[Any]:
        raise RuntimeError("plugin exploded")


def test_runtime_publishes_failure_and_reraises_original_exception(
    capability: Capability,
) -> None:
    registry = PluginRegistry()
    registry.register(FailingPlugin(capability))
    runtime = KernelRuntime(registry=registry)
    request = ExecutionRequest[dict[str, str]](
        capability=capability,
        context=ExecutionContext(execution_id="execution-failed"),
        payload={"message": "Hello CGR!"},
    )

    with pytest.raises(RuntimeError, match="plugin exploded") as raised:
        runtime.execute("failing", request)

    assert str(raised.value) == "plugin exploded"
    assert not isinstance(raised.value, PluginExecutionError)
    started, failed = runtime.event_bus.history()
    assert started.type == EventType.EXECUTION_STARTED
    assert failed.type == EventType.EXECUTION_FAILED
    assert failed.execution_id == "execution-failed"
    assert failed.payload == {
        "plugin_id": "failing",
        "capability_id": "echo",
        "error_type": "RuntimeError",
        "error_message": "plugin exploded",
    }


def test_execute_capability_uses_first_match_and_publishes_events(
    capability: Capability,
) -> None:
    registry = PluginRegistry()
    registry.register(EchoPlugin())
    registry.register(FailingPlugin(capability))
    runtime = KernelRuntime(registry=registry)
    request = ExecutionRequest[dict[str, str]](
        capability=capability,
        context=ExecutionContext(execution_id="capability-execution"),
        payload={"message": "capability result"},
    )

    result = runtime.execute_capability(request)

    assert result.output == {"message": "capability result"}
    started, completed = runtime.event_bus.history()
    assert started.type == EventType.EXECUTION_STARTED
    assert started.payload["plugin_id"] == "echo"
    assert started.payload["capability_id"] == "echo"
    assert started.execution_id == "capability-execution"
    assert completed.type == EventType.EXECUTION_COMPLETED
    assert completed.payload == {
        "plugin_id": "echo",
        "capability_id": "echo",
        "status": "success",
    }


def test_execute_capability_without_match_raises_without_events(
    capability: Capability,
) -> None:
    runtime = KernelRuntime()
    request = ExecutionRequest[dict[str, str]](
        capability=capability,
        context=ExecutionContext(),
        payload={},
    )

    with pytest.raises(
        CapabilityNotFoundError,
        match="No plugin registered for capability 'echo'",
    ):
        runtime.execute_capability(request)

    assert runtime.event_bus.history() == []


def test_execute_missing_plugin_raises_explicit_error_and_publishes_failure(
    capability: Capability,
) -> None:
    runtime = KernelRuntime()
    request = ExecutionRequest[dict[str, str]](
        capability=capability,
        context=ExecutionContext(execution_id="missing-plugin"),
        payload={},
    )

    with pytest.raises(
        PluginNotFoundError,
        match="Plugin 'missing' is not registered.",
    ):
        runtime.execute("missing", request)

    started, failed = runtime.event_bus.history()
    assert started.type == EventType.EXECUTION_STARTED
    assert failed.type == EventType.EXECUTION_FAILED
    assert failed.payload["error_type"] == "PluginNotFoundError"


def test_execute_capability_publishes_failure_and_reraises(
    capability: Capability,
) -> None:
    registry = PluginRegistry()
    registry.register(FailingPlugin(capability))
    runtime = KernelRuntime(registry=registry)
    request = ExecutionRequest[dict[str, str]](
        capability=capability,
        context=ExecutionContext(execution_id="capability-failed"),
        payload={},
    )

    with pytest.raises(RuntimeError, match="plugin exploded"):
        runtime.execute_capability(request)

    started, failed = runtime.event_bus.history()
    assert started.type == EventType.EXECUTION_STARTED
    assert failed.type == EventType.EXECUTION_FAILED
    assert failed.execution_id == "capability-failed"
    assert failed.payload == {
        "plugin_id": "failing",
        "capability_id": "echo",
        "error_type": "RuntimeError",
        "error_message": "plugin exploded",
    }
