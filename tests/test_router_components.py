import pytest
from pydantic import ValidationError

from cgr.kernel.contracts import (
    Capability,
    CapabilityVersion,
    ExecutionContext,
    ExecutionRequest,
)
from cgr.kernel.router import (
    CapabilityClassifier,
    PluginSelector,
    RouteCandidate,
    RouteStrategy,
)


def make_request(capability_id: str) -> ExecutionRequest[dict[str, str]]:
    return ExecutionRequest(
        capability=Capability(
            id=capability_id,
            name=capability_id.title(),
            version=CapabilityVersion(major=1, minor=0, patch=0),
        ),
        context=ExecutionContext(),
        payload={},
    )


def make_candidate(
    plugin_id: str,
    priority: int = 0,
) -> RouteCandidate:
    return RouteCandidate(
        plugin_id=plugin_id,
        plugin_name=f"Plugin {plugin_id}",
        plugin_version="1.0.0",
        capability_id="echo",
        priority=priority,
    )


def test_route_strategy_values() -> None:
    assert [strategy.value for strategy in RouteStrategy] == [
        "first_match",
        "highest_priority",
    ]


def test_route_candidate_is_immutable_and_serializes_fields() -> None:
    candidate = make_candidate("one", priority=3)

    assert candidate.model_dump() == {
        "plugin_id": "one",
        "plugin_name": "Plugin one",
        "plugin_version": "1.0.0",
        "capability_id": "echo",
        "priority": 3,
        "healthy": True,
    }
    with pytest.raises(ValidationError):
        candidate.priority = 4


def test_classifier_returns_request_capability_id() -> None:
    classified = CapabilityClassifier().classify(make_request("reasoning"))

    assert classified == "reasoning"


def test_selector_first_match_returns_first_candidate() -> None:
    first = make_candidate("first")
    second = make_candidate("second", priority=10)

    selected = PluginSelector().select(
        [first, second],
        RouteStrategy.FIRST_MATCH,
    )

    assert selected is first


def test_selector_highest_priority_returns_highest_candidate() -> None:
    low = make_candidate("low", priority=1)
    high = make_candidate("high", priority=5)

    selected = PluginSelector().select(
        [low, high],
        RouteStrategy.HIGHEST_PRIORITY,
    )

    assert selected is high


def test_selector_highest_priority_preserves_order_on_tie() -> None:
    first = make_candidate("first", priority=5)
    second = make_candidate("second", priority=5)

    selected = PluginSelector().select(
        [first, second],
        RouteStrategy.HIGHEST_PRIORITY,
    )

    assert selected is first


def test_selector_rejects_empty_candidates() -> None:
    with pytest.raises(ValueError, match="No route candidates available"):
        PluginSelector().select([], RouteStrategy.FIRST_MATCH)
