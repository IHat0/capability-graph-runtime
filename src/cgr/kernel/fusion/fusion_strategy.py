"""Deterministic output fusion strategies."""

from enum import Enum


class FusionStrategy(str, Enum):
    """Strategies supported by the fusion engine."""

    FIRST_SUCCESS = "first_success"
    COLLECT_ALL = "collect_all"
