import pytest
from pydantic import ValidationError

from cgr.kernel.contracts import HealthStatus, PluginState
from cgr.kernel.runtime import KernelRuntime
from cgr.plugins.examples import EchoPlugin


class UnavailablePlugin(EchoPlugin):
    """Echo plugin reporting unavailable health for snapshot testing."""

    @property
    def health(self) -> HealthStatus:
        return HealthStatus.UNAVAILABLE


def test_empty_runtime_health_snapshot() -> None:
    snapshot = KernelRuntime().health_snapshot()

    assert snapshot.healthy is True
    assert snapshot.plugin_count == 0
    assert snapshot.plugins == []


def test_runtime_health_snapshot_includes_echo_plugin() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(EchoPlugin())
    events_before_snapshot = runtime.event_bus.history()

    snapshot = runtime.health_snapshot()

    assert snapshot.healthy is True
    assert snapshot.plugin_count == 1
    plugin = snapshot.plugins[0]
    assert plugin.plugin_id == "echo"
    assert plugin.plugin_name == "Echo Plugin"
    assert plugin.plugin_version == "1.0.0"
    assert plugin.state == PluginState.RUNNING
    assert plugin.health == HealthStatus.HEALTHY
    assert plugin.capabilities == ["echo"]
    assert runtime.event_bus.history() == events_before_snapshot


def test_runtime_health_snapshot_is_immutable() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(EchoPlugin())
    snapshot = runtime.health_snapshot()

    with pytest.raises(ValidationError):
        snapshot.healthy = False

    with pytest.raises(ValidationError):
        snapshot.plugins[0].plugin_id = "changed"


def test_runtime_is_unhealthy_if_any_plugin_is_unavailable() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(UnavailablePlugin())

    snapshot = runtime.health_snapshot()

    assert snapshot.healthy is False
    assert snapshot.plugins[0].health == HealthStatus.UNAVAILABLE
