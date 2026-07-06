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
    HealthStatus,
    Plugin,
)
from cgr.kernel.exceptions import CapabilityNotFoundError
from cgr.kernel.learning import ExecutionObservation, LearningMemory
from cgr.kernel.registry import PluginRegistry
from cgr.kernel.router import (
    CapabilityRouter,
    PluginSelector,
    RouteCandidate,
    RouteDecision,
    RouteStrategy,
)
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
    assert decision.candidate_scores == {}


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


class CapturingSelector(PluginSelector):
    """Selector test double retaining enriched candidates."""

    def __init__(self) -> None:
        self.candidates: list[RouteCandidate] = []

    def select(
        self,
        candidates: list[RouteCandidate],
        strategy: RouteStrategy,
    ) -> RouteCandidate:
        self.candidates = list(candidates)
        return super().select(candidates, strategy)


class OutputEchoPlugin(EchoPlugin):
    """Echo capability plugin returning its configured identifier."""

    def __init__(self, plugin_id: str) -> None:
        super().__init__()
        self._metadata = self.metadata.model_copy(update={"id": plugin_id})

    def execute(
        self,
        request: ExecutionRequest[Any],
    ) -> ExecutionResult[Any]:
        return ExecutionResult(
            context=request.context,
            status=ExecutionStatus.SUCCESS,
            output={"plugin_id": self.metadata.id},
        )


def record_observation(
    memory: LearningMemory,
    execution_id: str,
    plugin_id: str,
    status: ExecutionStatus,
    duration_ms: float,
) -> None:
    memory.record(
        ExecutionObservation(
            execution_id=execution_id,
            capability_id="echo",
            plugin_id=plugin_id,
            status=status,
            duration_ms=duration_ms,
        )
    )


def test_router_enriches_candidates_from_learning_memory() -> None:
    registry = PluginRegistry()
    registry.register(OutputEchoPlugin("observed"))
    memory = LearningMemory()
    record_observation(
        memory,
        "observation-1",
        "observed",
        ExecutionStatus.SUCCESS,
        12.5,
    )
    selector = CapturingSelector()
    router = CapabilityRouter(
        registry,
        selector=selector,
        learning_memory=memory,
    )

    router.route(make_request())

    candidate = selector.candidates[0]
    assert candidate.success_rate == 1.0
    assert candidate.average_duration_ms == 12.5
    assert candidate.total_executions == 1


def test_memory_best_selects_better_plugin_and_explains_scores() -> None:
    registry = PluginRegistry()
    registry.register(OutputEchoPlugin("plugin-a"))
    registry.register(OutputEchoPlugin("plugin-b"))
    memory = LearningMemory()
    record_observation(
        memory,
        "a-success",
        "plugin-a",
        ExecutionStatus.SUCCESS,
        5.0,
    )
    record_observation(
        memory,
        "a-failure",
        "plugin-a",
        ExecutionStatus.FAILED,
        5.0,
    )
    record_observation(
        memory,
        "b-success",
        "plugin-b",
        ExecutionStatus.SUCCESS,
        10.0,
    )
    router = CapabilityRouter(
        registry,
        strategy=RouteStrategy.MEMORY_BEST,
        learning_memory=memory,
    )

    decision = router.route(make_request())

    assert decision.selected_plugin_id == "plugin-b"
    assert decision.strategy == RouteStrategy.MEMORY_BEST
    assert decision.reason == (
        "Selected plugin with best observed performance for requested capability."
    )
    assert decision.candidate_scores == {
        "plugin-a": (
            "success_rate=0.5, average_duration_ms=5.0, total_executions=2"
        ),
        "plugin-b": (
            "success_rate=1.0, average_duration_ms=10.0, total_executions=1"
        ),
    }


def test_memory_best_without_learning_memory_uses_first_full_tie() -> None:
    registry = PluginRegistry()
    first = OutputEchoPlugin("first")
    registry.register(first)
    registry.register(OutputEchoPlugin("second"))

    decision = CapabilityRouter(
        registry,
        strategy=RouteStrategy.MEMORY_BEST,
    ).route(make_request())

    assert decision.selected_plugin_id == "first"
    assert decision.candidate_scores["first"] == (
        "success_rate=None, average_duration_ms=None, total_executions=None"
    )


def test_highest_priority_router_behavior_remains_first_on_tie() -> None:
    registry = PluginRegistry()
    registry.register(OutputEchoPlugin("first"))
    registry.register(OutputEchoPlugin("second"))

    decision = CapabilityRouter(
        registry,
        strategy=RouteStrategy.HIGHEST_PRIORITY,
    ).route(make_request())

    assert decision.selected_plugin_id == "first"
    assert decision.reason == (
        "Selected highest priority plugin for requested capability."
    )


def test_runtime_executes_plugin_selected_by_memory_best_router() -> None:
    registry = PluginRegistry()
    memory = LearningMemory()
    router = CapabilityRouter(
        registry,
        strategy=RouteStrategy.MEMORY_BEST,
        learning_memory=memory,
    )
    runtime = KernelRuntime(registry=registry, router=router)
    runtime.register_plugin(OutputEchoPlugin("plugin-a"))
    runtime.register_plugin(OutputEchoPlugin("plugin-b"))
    record_observation(
        memory,
        "a-failure",
        "plugin-a",
        ExecutionStatus.FAILED,
        1.0,
    )
    record_observation(
        memory,
        "b-success",
        "plugin-b",
        ExecutionStatus.SUCCESS,
        10.0,
    )

    result = runtime.execute_capability(make_request())

    assert result.output == {"plugin_id": "plugin-b"}


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
