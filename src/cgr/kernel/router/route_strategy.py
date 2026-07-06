"""Deterministic capability routing strategies."""

from enum import Enum


class RouteStrategy(str, Enum):
    """Strategies supported by the capability router."""

    FIRST_MATCH = "first_match"
    HIGHEST_PRIORITY = "highest_priority"
