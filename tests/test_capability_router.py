from typing import Any

import pytest
from pydantic import ValidationError

from cgr.kernel.contracts import (
    Capability,
    CapabilityVersion,
    ExecutionContext,
    ExecutionRequest,
    HealthStatus,
    Plugin,
)
from cgr.kernel.exceptions import CapabilityNotFoundError
from cgr.kernel.registry import PluginRegistry
from cgr.kernel.router import CapabilityRouter, RouteDecision, RouteStrategy
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


def test_router_returns_structured_route_decision() -> None:
    registry = PluginRegistry()
    first = EchoPlugin()
    second = EchoPlugin()
    second._metadata = second.metadata.model_copy(update={"id": "echo-second"})
    registry.register(first)
    registry.register(second)

    decision = CapabilityRouter(registry).route(make_request())

    assert isinstance(decision, RouteDecision)
    assert decision.capability_id == "echo"
    assert decision.selected_plugin_id == "echo"
    assert decision.candidate_plugin_ids == ["echo", "echo-second"]
    assert decision.strategy == RouteStrategy.FIRST_MATCH
    assert decision.reason == (
        "Selected first available plugin for requested capability."
    )


def test_route_decision_is_immutable() -> None:
    registry = PluginRegistry()
    registry.register(EchoPlugin())
    decision = CapabilityRouter(registry).route(make_request())

    with pytest.raises(ValidationError):
        decision.selected_plugin_id = "changed"


def test_router_route_raises_when_no_plugin_supports_capability() -> None:
    router = CapabilityRouter(PluginRegistry())

    with pytest.raises(
        CapabilityNotFoundError,
        match="No plugin registered for capability 'missing'",
    ):
        router.route(make_request("missing"))


class UnavailableEchoPlugin(EchoPlugin):
    """Echo plugin that is unavailable for routing."""

    @property
    def health(self) -> HealthStatus:
        return HealthStatus.UNAVAILABLE


def test_router_filters_unavailable_plugins() -> None:
    registry = PluginRegistry()
    unavailable = UnavailableEchoPlugin()
    available = EchoPlugin()
    available._metadata = available.metadata.model_copy(
        update={"id": "echo-available"}
    )
    registry.register(unavailable)
    registry.register(available)

    decision = CapabilityRouter(registry).route(make_request())

    assert decision.selected_plugin_id == "echo-available"
    assert decision.candidate_plugin_ids == ["echo-available"]


def test_router_raises_when_all_matching_plugins_are_unavailable() -> None:
    registry = PluginRegistry()
    registry.register(UnavailableEchoPlugin())

    with pytest.raises(
        CapabilityNotFoundError,
        match="No available plugin registered for capability 'echo'",
    ):
        CapabilityRouter(registry).route(make_request())


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
