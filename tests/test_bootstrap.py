from cgr.kernel.contracts import (
    ExecutionContext,
    ExecutionRequest,
    PluginState,
)
from cgr.kernel.runtime import KernelRuntime, create_runtime
from cgr.plugins.examples import EchoPlugin
from cgr.shared.events import EventType


def test_create_runtime_returns_empty_kernel_runtime_by_default() -> None:
    runtime = create_runtime()

    assert isinstance(runtime, KernelRuntime)
    assert runtime.registry.plugin_ids() == []


def test_create_runtime_with_examples_registers_running_echo_plugin() -> None:
    runtime = create_runtime(include_examples=True)

    plugin = runtime.registry.get("echo")
    assert isinstance(plugin, EchoPlugin)
    assert plugin.state == PluginState.RUNNING
    assert runtime.registry.plugin_ids() == ["echo"]


def test_bootstrap_echo_executes_by_capability() -> None:
    runtime = create_runtime(include_examples=True)
    capability = runtime.registry.get("echo").metadata.capabilities[0]
    request = ExecutionRequest[dict[str, str]](
        capability=capability,
        context=ExecutionContext(),
        payload={"message": "Hello from bootstrap!"},
    )

    result = runtime.execute_capability(request)

    assert result.output == {"message": "Hello from bootstrap!"}


def test_bootstrap_with_examples_emits_plugin_registered_event() -> None:
    runtime = create_runtime(include_examples=True)

    events = runtime.event_bus.history_by_type(EventType.PLUGIN_REGISTERED)
    assert len(events) == 1
    assert events[0].payload == {
        "plugin_id": "echo",
        "plugin_name": "Echo Plugin",
        "plugin_version": "1.0.0",
    }
