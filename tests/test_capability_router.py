from typing import Any

import pytest

from cgr.kernel.contracts import (
    Capability,
    CapabilityVersion,
    ExecutionContext,
    ExecutionRequest,
    Plugin,
)
from cgr.kernel.exceptions import CapabilityNotFoundError
from cgr.kernel.registry import PluginRegistry
from cgr.kernel.router import CapabilityRouter
from cgr.kernel.runtime import KernelRuntime
from cgr.plugins.examples import EchoPlugin


def make_request(capability_id: str = "echo") -> ExecutionRequest[dict[str, str]]:
    return ExecutionRequest(
        capability=Capability(
            id=capability_id,
            name=capability_id.title(),
            version=CapabilityVersion(major=1, minor=0, patch=0),
        ),
        context=ExecutionContext(),
        payload={"message": "routed"},
    )


def test_router_selects_first_matching_plugin() -> None:
    registry = PluginRegistry()
    first = EchoPlugin()
    second = EchoPlugin()
    second._metadata = second.metadata.model_copy(update={"id": "echo-second"})
    registry.register(first)
    registry.register(second)

    selected = CapabilityRouter(registry).select_plugin(make_request())

    assert selected is first


def test_router_raises_when_no_plugin_supports_capability() -> None:
    router = CapabilityRouter(PluginRegistry())

    with pytest.raises(
        CapabilityNotFoundError,
        match="No plugin registered for capability 'missing'",
    ):
        router.select_plugin(make_request("missing"))


class RecordingRouter(CapabilityRouter):
    """Router test double that records selection and returns one plugin."""

    def __init__(self, plugin: Plugin[Any, Any]) -> None:
        super().__init__(PluginRegistry())
        self.plugin = plugin
        self.called = False

    def select_plugin(
        self,
        request: ExecutionRequest[Any],
    ) -> Plugin[Any, Any]:
        self.called = True
        return self.plugin


def test_runtime_execute_capability_uses_injected_router() -> None:
    registry = PluginRegistry()
    plugin = EchoPlugin()
    registry.register(plugin)
    router = RecordingRouter(plugin)
    runtime = KernelRuntime(registry=registry, router=router)

    result = runtime.execute_capability(make_request("not-advertised"))

    assert runtime.router is router
    assert router.called is True
    assert result.output == {"message": "routed"}
