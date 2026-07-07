"""Deterministic model plugins for architecture testing."""

from .mock_coding_model_plugin import MockCodingModelPlugin
from .mock_reasoning_model_plugin import MockReasoningModelPlugin

__all__ = [
    "MockCodingModelPlugin",
    "MockReasoningModelPlugin",
]
