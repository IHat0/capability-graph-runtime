"""Deterministic local model fixtures for Booster Engine measurement."""

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


class _LocalBoosterModel(Plugin[Any, dict[str, Any]]):
    def __init__(self, plugin_id: str, name: str, capability_id: str) -> None:
        self._state = PluginState.DISCOVERED
        tags = ["local", "deterministic", "booster-measurement"]
        self._metadata = PluginMetadata(
            id=plugin_id,
            name=name,
            version="1.0.0",
            author="CGR",
            description="Deterministic local Booster Engine measurement fixture.",
            capabilities=[
                Capability(
                    id=capability_id,
                    name=name,
                    description="Local deterministic booster model capability.",
                    version=CapabilityVersion(major=1, minor=0, patch=0),
                    tags=tags,
                )
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

    @staticmethod
    def _prompt(request: ExecutionRequest[Any]) -> str:
        return ModelRequest.model_validate(request.payload).latest_user_message

    def _result(
        self, request: ExecutionRequest[Any], text: str
    ) -> ExecutionResult[dict[str, Any]]:
        response = ModelResponse(
            text=text,
            model_id=self.metadata.id,
            metadata={"provider": "local_booster_fixture"},
        )
        return ExecutionResult(
            context=request.context,
            status=ExecutionStatus.SUCCESS,
            output=response.model_dump(),
        )


class LocalBoosterBaseModelPlugin(_LocalBoosterModel):
    """Base fixture where orchestration improves two of three outcomes."""

    def __init__(self) -> None:
        super().__init__(
            "provider.local.booster_base",
            "Local Booster Base Model",
            "model.code",
        )

    def execute(self, request: ExecutionRequest[Any]) -> ExecutionResult[dict[str, Any]]:
        prompt = self._prompt(request)
        if prompt.startswith("CRITIQUE"):
            return self._result(
                request, "Check the candidate against every stated requirement."
            )
        task_id, original, corrected = self._task_data(prompt)
        stage = prompt.splitlines()[0]
        solved = task_id == "local.greeting"
        if stage.startswith("CANDIDATE GENERATION"):
            solved = task_id in {"local.greeting", "local.add"}
        elif stage == "REPAIR":
            solved = task_id in {"local.greeting", "local.add"} or (
                task_id == "local.is_even" and "modulo comparison to zero" in prompt
            )
        files = corrected if solved else original
        return self._result(
            request,
            json.dumps(
                {"files": files, "explanation": "Local booster fixture response."}
            ),
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


class LocalBoosterCriticModelPlugin(_LocalBoosterModel):
    """Critic fixture that supplies the missing is-even correction."""

    def __init__(self) -> None:
        super().__init__(
            "provider.local.booster_critic",
            "Local Booster Critic Model",
            "model.reason",
        )

    def execute(self, request: ExecutionRequest[Any]) -> ExecutionResult[dict[str, Any]]:
        prompt = self._prompt(request)
        critique = (
            "Correct the modulo comparison to zero."
            if "numbers.py" in prompt
            else "Check the patch against the requested output."
        )
        return self._result(request, critique)
