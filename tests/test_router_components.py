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
    success_rate: float | None = None,
    average_duration_ms: float | None = None,
    total_executions: int | None = None,
) -> RouteCandidate:
    return RouteCandidate(
        plugin_id=plugin_id,
        plugin_name=f"Plugin {plugin_id}",
        plugin_version="1.0.0",
        capability_id="echo",
        priority=priority,
        success_rate=success_rate,
        average_duration_ms=average_duration_ms,
        total_executions=total_executions,
    )


def test_route_strategy_values() -> None:
    assert [strategy.value for strategy in RouteStrategy] == [
        "first_match",
        "highest_priority",
        "memory_best",
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
        "success_rate": None,
        "average_duration_ms": None,
        "total_executions": None,
    }
    with pytest.raises(ValidationError):
        candidate.priority = 4


def test_classifier_returns_request_capability_id() -> None:
    classified = CapabilityClassifier().classify(make_request("reasoning"))

    assert classified == "reasoning"


def test_route_candidate_accepts_performance_fields() -> None:
    candidate = make_candidate(
        "observed",
        success_rate=0.75,
        average_duration_ms=12.5,
        total_executions=4,
    )

    assert candidate.success_rate == 0.75
    assert candidate.average_duration_ms == 12.5
    assert candidate.total_executions == 4


@pytest.mark.parametrize(
    "values",
    [
        {"success_rate": -0.1},
        {"success_rate": 1.1},
        {"average_duration_ms": -1.0},
        {"total_executions": -1},
    ],
)
def test_route_candidate_rejects_invalid_performance_fields(
    values: dict[str, float | int],
) -> None:
    with pytest.raises(ValidationError):
        RouteCandidate.model_validate(
            {
                "plugin_id": "invalid",
                "plugin_name": "Invalid",
                "plugin_version": "1.0.0",
                "capability_id": "echo",
                **values,
            }
        )


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


def test_selector_memory_best_prefers_higher_success_rate() -> None:
    lower = make_candidate("lower", success_rate=0.5, average_duration_ms=1.0)
    higher = make_candidate("higher", success_rate=0.9, average_duration_ms=50.0)

    selected = PluginSelector().select(
        [lower, higher],
        RouteStrategy.MEMORY_BEST,
    )

    assert selected is higher


def test_selector_memory_best_breaks_tie_by_lower_duration() -> None:
    slow = make_candidate("slow", success_rate=1.0, average_duration_ms=20.0)
    fast = make_candidate("fast", success_rate=1.0, average_duration_ms=5.0)

    selected = PluginSelector().select(
        [slow, fast],
        RouteStrategy.MEMORY_BEST,
    )

    assert selected is fast


def test_selector_memory_best_breaks_duration_tie_by_more_executions() -> None:
    less = make_candidate(
        "less",
        success_rate=1.0,
        average_duration_ms=5.0,
        total_executions=2,
    )
    more = make_candidate(
        "more",
        success_rate=1.0,
        average_duration_ms=5.0,
        total_executions=5,
    )

    selected = PluginSelector().select(
        [less, more],
        RouteStrategy.MEMORY_BEST,
    )

    assert selected is more


def test_selector_memory_best_preserves_order_for_full_tie() -> None:
    first = make_candidate("first", success_rate=1.0, average_duration_ms=5.0)
    second = make_candidate("second", success_rate=1.0, average_duration_ms=5.0)

    selected = PluginSelector().select(
        [first, second],
        RouteStrategy.MEMORY_BEST,
    )

    assert selected is first


def test_selector_memory_best_handles_none_values() -> None:
    unknown = make_candidate("unknown")
    observed = make_candidate("observed", success_rate=0.0, average_duration_ms=10.0)

    selected = PluginSelector().select(
        [unknown, observed],
        RouteStrategy.MEMORY_BEST,
    )

    assert selected is observed


def test_selector_rejects_empty_candidates() -> None:
    with pytest.raises(ValueError, match="No route candidates available"):
        PluginSelector().select([], RouteStrategy.FIRST_MATCH)
