"""Deterministic selection of capability route candidates."""

from math import inf

from .route_candidate import RouteCandidate
from .route_strategy import RouteStrategy


class PluginSelector:
    """Select one candidate according to a routing strategy."""

    def select(
        self,
        candidates: list[RouteCandidate],
        strategy: RouteStrategy,
    ) -> RouteCandidate:
        """Select a candidate using the requested strategy."""
        if not candidates:
            raise ValueError("No route candidates available.")

        if strategy == RouteStrategy.FIRST_MATCH:
            return candidates[0]
        if strategy == RouteStrategy.HIGHEST_PRIORITY:
            return max(candidates, key=lambda candidate: candidate.priority)
        if strategy == RouteStrategy.MEMORY_BEST:
            return max(
                candidates,
                key=lambda candidate: (
                    candidate.success_rate
                    if candidate.success_rate is not None
                    else 0.0,
                    -candidate.average_duration_ms
                    if candidate.average_duration_ms is not None
                    else -inf,
                    candidate.total_executions
                    if candidate.total_executions is not None
                    else 0,
                ),
            )

        raise ValueError(f"Unsupported route strategy: {strategy!r}.")
