"""Single-model coding agent plugin."""

from typing import Any

from cgr.kernel.coding import (
    CodingTask,
    JsonPatchParser,
    build_patch_prompt,
    build_repair_prompt,
    select_patch,
    verify_patch,
)
from cgr.kernel.contracts import (
    Capability,
    CapabilityVersion,
    ExecutionContext,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    HealthStatus,
    Plugin,
    PluginMetadata,
    PluginState,
)
from cgr.kernel.model import ModelMessage, ModelRequest, ModelRole
from cgr.kernel.runtime import KernelRuntime


class SingleModelCodingAgentPlugin(Plugin[Any, dict[str, Any]]):
    """Use one routed model capability to produce a structured coding patch."""

    def __init__(
        self,
        runtime: KernelRuntime,
        model_capability_id: str = "model.code",
        plugin_id: str = "agent.single_model_coding",
    ) -> None:
        self._runtime = runtime
        self._model_capability_id = model_capability_id
        self._state = PluginState.DISCOVERED
        tags = ["agent", "coding", "patch"]
        self._metadata = PluginMetadata(
            id=plugin_id,
            name="Single Model Coding Agent",
            version="1.0.0",
            author="CGR",
            description="Generates a coding patch using one model capability.",
            capabilities=[
                Capability(
                    id="coding.patch",
                    name="Coding Patch",
                    description="Generate patched files for a coding issue.",
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

    def execute(self, request: ExecutionRequest[Any]) -> ExecutionResult[dict[str, Any]]:
        task = self._parse_task(request.payload)
        model_result = self._execute_model(build_patch_prompt(task))
        parser = JsonPatchParser()
        patch = parser.parse(self._model_text(model_result.output))
        verification = verify_patch(task, patch)
        duration_ms = model_result.duration_ms
        if verification is not None and not verification[0]:
            repair_result = self._execute_model(
                build_repair_prompt(task, patch.files, verification[1])
            )
            duration_ms += repair_result.duration_ms
            try:
                repaired = parser.parse(self._model_text(repair_result.output))
            except ValueError:
                repaired = patch
            repaired_verification = verify_patch(task, repaired)
            repaired_passed = (
                repaired_verification[0]
                if repaired_verification is not None
                else False
            )
            patch = select_patch(patch, False, repaired, repaired_passed)
        return ExecutionResult(
            context=request.context,
            status=ExecutionStatus.SUCCESS,
            output=patch.model_dump(),
            duration_ms=duration_ms,
        )

    def _execute_model(self, prompt: str) -> ExecutionResult[Any]:
        capability = Capability(
            id=self._model_capability_id,
            name="Coding Agent Model",
            description="Model capability used by a coding agent.",
            version=CapabilityVersion(major=1, minor=0, patch=0),
        )
        result = self._runtime.execute_capability(
            ExecutionRequest[ModelRequest](
                capability=capability,
                context=ExecutionContext(),
                payload=ModelRequest(
                    messages=[ModelMessage(role=ModelRole.USER, content=prompt)]
                ),
            )
        )
        if result.status != ExecutionStatus.SUCCESS:
            raise RuntimeError(result.error or "Coding model execution failed.")
        return result

    @staticmethod
    def _parse_task(payload: Any) -> CodingTask:
        if isinstance(payload, CodingTask):
            return payload
        if isinstance(payload, dict):
            return CodingTask.model_validate(payload)
        raise ValueError("Coding payload must be a CodingTask or dictionary.")

    @staticmethod
    def _model_text(output: Any) -> str:
        if not isinstance(output, dict) or not isinstance(output.get("text"), str):
            raise RuntimeError("Model response did not contain text.")
        return output["text"]
