"""Capability classification for execution requests."""

from typing import Any

from cgr.kernel.contracts import ExecutionRequest


class CapabilityClassifier:
    """Classify requests by their declared capability identifier."""

    def classify(self, request: ExecutionRequest[Any]) -> str:
        """Return the capability identifier declared by the request."""
        return request.capability.id
