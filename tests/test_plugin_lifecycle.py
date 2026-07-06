import pytest

from cgr.kernel.contracts import PluginState
from cgr.kernel.exceptions import PluginAlreadyRegisteredError
from cgr.kernel.runtime import KernelRuntime
from cgr.plugins.examples import EchoPlugin
from cgr.shared.events import EventType


def test_register_plugin_initializes_registers_and_publishes_event() -> None:
    runtime = KernelRuntime()
    plugin = EchoPlugin()

    runtime.register_plugin(plugin)

    assert plugin.state == PluginState.RUNNING
    assert runtime.registry.get("echo") is plugin
    event = runtime.event_bus.history()[0]
    assert event.type == EventType.PLUGIN_REGISTERED
    assert event.source == "kernel.runtime"
    assert event.correlation_id is None
    assert event.execution_id is None
    assert event.payload == {
        "plugin_id": "echo",
        "plugin_name": "Echo Plugin",
        "plugin_version": "1.0.0",
    }


def test_unregister_plugin_shuts_down_removes_and_publishes_event() -> None:
    runtime = KernelRuntime()
    plugin = EchoPlugin()
    runtime.register_plugin(plugin)

    runtime.unregister_plugin("echo")

    assert plugin.state == PluginState.STOPPED
    assert "echo" not in runtime.registry
    event = runtime.event_bus.history()[-1]
    assert event.type == EventType.PLUGIN_UNREGISTERED
    assert event.payload == {
        "plugin_id": "echo",
        "plugin_name": "Echo Plugin",
        "plugin_version": "1.0.0",
    }


def test_unregister_unknown_plugin_does_nothing() -> None:
    runtime = KernelRuntime()

    runtime.unregister_plugin("missing")

    assert runtime.registry.plugin_ids() == []
    assert runtime.event_bus.history() == []


def test_shutdown_unregisters_all_plugins_and_is_idempotent() -> None:
    runtime = KernelRuntime()
    first = EchoPlugin()
    second = EchoPlugin()
    second._metadata = second.metadata.model_copy(update={"id": "echo-second"})
    runtime.register_plugin(first)
    runtime.register_plugin(second)

    runtime.shutdown()
    runtime.shutdown()

    assert first.state == PluginState.STOPPED
    assert second.state == PluginState.STOPPED
    assert runtime.registry.plugin_ids() == []
    unregistered = runtime.event_bus.history_by_type(
        EventType.PLUGIN_UNREGISTERED
    )
    assert [event.payload["plugin_id"] for event in unregistered] == [
        "echo",
        "echo-second",
    ]


def test_duplicate_registration_shuts_down_second_plugin() -> None:
    runtime = KernelRuntime()
    first = EchoPlugin()
    second = EchoPlugin()
    runtime.register_plugin(first)

    with pytest.raises(PluginAlreadyRegisteredError, match="already registered"):
        runtime.register_plugin(second)

    assert first.state == PluginState.RUNNING
    assert second.state == PluginState.STOPPED
    assert runtime.registry.get("echo") is first
    assert len(
        runtime.event_bus.history_by_type(EventType.PLUGIN_REGISTERED)
    ) == 1
