from cgr.kernel.contracts import (
    Capability,
    CapabilityVersion,
    ExecutionContext,
    ExecutionRequest,
    ExecutionStatus,
    HealthStatus,
    PluginState,
)
from cgr.plugins.examples import EchoPlugin


def test_echo_plugin_initialize_execute_shutdown_lifecycle() -> None:
    plugin = EchoPlugin()
    context = ExecutionContext(execution_id="echo-execution")
    request = ExecutionRequest[dict[str, str]](
        capability=Capability(
            id="echo",
            name="Echo",
            version=CapabilityVersion(major=1, minor=0, patch=0),
        ),
        context=context,
        payload={"message": "Hello CGR!"},
    )

    assert plugin.state == PluginState.DISCOVERED
    assert plugin.health == HealthStatus.HEALTHY
    assert plugin.metadata.capabilities == [
        Capability(
            id="echo",
            name="Echo",
            description="Echo capability",
            version=CapabilityVersion(major=1, minor=0, patch=0),
            tags=["example", "test"],
        )
    ]

    plugin.initialize()
    assert plugin.state == PluginState.RUNNING

    result = plugin.execute(request)
    assert result.status == ExecutionStatus.SUCCESS
    assert result.output == {"message": "Hello CGR!"}
    assert result.context is context
    assert result.execution_id == "echo-execution"
    assert result.succeeded
    assert not result.failed

    plugin.shutdown()
    assert plugin.state == PluginState.STOPPED
