"""Explicit deterministic providers for validating local A/B measurement."""

import json
from typing import Any

from cgr.kernel.contracts import (
    Capability,
    CapabilityVersion,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    HealthStatus,
    Plugin,
    PluginMetadata,
    PluginState,
)
from cgr.kernel.model import ModelRequest, ModelResponse


class _LocalCodingProvider(Plugin[Any, dict[str, Any]]):
    """Base for deterministic local evaluation providers, never real models."""

    def __init__(
        self,
        plugin_id: str,
        name: str,
        capability_ids: list[str],
        solved_tasks: set[str],
    ) -> None:
        self._state = PluginState.DISCOVERED
        self._solved_tasks = solved_tasks
        tags = ["local", "evaluation", "coding"]
        self._metadata = PluginMetadata(
            id=plugin_id,
            name=name,
            version="1.0.0",
            author="CGR",
            description="Deterministic local coding A/B evaluation provider.",
            capabilities=[
                Capability(
                    id=capability_id,
                    name=name,
                    description="Local deterministic coding evaluation capability.",
                    version=CapabilityVersion(major=1, minor=0, patch=0),
                    tags=tags,
                )
                for capability_id in capability_ids
            ],
            tags=tags,
        )

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata

    @property
    def state(self) -> PluginState:
        return self._state

    @property
    def health(self) -> HealthStatus:
        return (
            HealthStatus.HEALTHY
            if self._state == PluginState.RUNNING
            else HealthStatus.DEGRADED
        )

    def initialize(self) -> None:
        self._state = PluginState.RUNNING

    def shutdown(self) -> None:
        self._state = PluginState.STOPPED

    def execute(self, request: ExecutionRequest[Any]) -> ExecutionResult[dict[str, Any]]:
        model_request = ModelRequest.model_validate(request.payload)
        prompt = model_request.latest_user_message
        if prompt.startswith("Critique the proposed coding patch"):
            text = "Apply the correction described by the issue."
        else:
            task_id, original, corrected = self._task_data(prompt)
            files = corrected if task_id in self._solved_tasks else original
            text = json.dumps(
                {"files": files, "explanation": "Local evaluation response."}
            )
        response = ModelResponse(
            text=text,
            model_id=self.metadata.id,
            metadata={"provider": "local_evaluation"},
        )
        return ExecutionResult(
            context=request.context,
            status=ExecutionStatus.SUCCESS,
            output=response.model_dump(),
        )

    @staticmethod
    def _task_data(
        prompt: str,
    ) -> tuple[str, dict[str, str], dict[str, str]]:
        if "math_utils.py" in prompt:
            return (
                "local.add",
                {"math_utils.py": "def add(a, b):\n    return a - b\n"},
                {"math_utils.py": "def add(a, b):\n    return a + b\n"},
            )
        if "numbers.py" in prompt:
            return (
                "local.is_even",
                {"numbers.py": "def is_even(n):\n    return n % 2 == 1\n"},
                {"numbers.py": "def is_even(n):\n    return n % 2 == 0\n"},
            )
        return (
            "local.greeting",
            {"app.py": 'print("hello")\n'},
            {"app.py": 'print("hello CGR")\n'},
        )


class LocalBaselineCodingProvider(_LocalCodingProvider):
    """Local baseline fixture that solves only the greeting task."""

    def __init__(self) -> None:
        super().__init__(
            "provider.local.baseline",
            "Local Baseline Coding Provider",
            ["model.code.baseline"],
            {"local.greeting"},
        )


class LocalSingleCodingProvider(_LocalCodingProvider):
    """Local single-agent fixture that solves greeting and addition."""

    def __init__(self) -> None:
        super().__init__(
            "provider.local.single",
            "Local Single Coding Provider",
            ["model.code.single"],
            {"local.greeting", "local.add"},
        )


class LocalMultiCodingProvider(_LocalCodingProvider):
    """Local multi-agent fixture that solves every local task."""

    def __init__(self) -> None:
        super().__init__(
            "provider.local.multi",
            "Local Multi Coding Provider",
            ["model.code.multi", "model.reason.multi"],
            {"local.greeting", "local.add", "local.is_even"},
        )
