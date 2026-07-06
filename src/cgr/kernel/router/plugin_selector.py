"""Deterministic selection of capability route candidates."""

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

        raise ValueError(f"Unsupported route strategy: {strategy!r}.")
