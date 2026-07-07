"""Deterministic demonstration pipeline over model capabilities."""

from typing import Any

from cgr.kernel.contracts import (
    Capability,
    CapabilityVersion,
    ExecutionContext,
    ExecutionRequest,
    ExecutionStatus,
)
from cgr.kernel.fusion import FusionEngine, FusionStrategy
from cgr.kernel.model import ModelMessage, ModelRequest, ModelRole
from cgr.kernel.runtime import KernelRuntime
from cgr.kernel.verification import SchemaVerifier

from .model_pipeline_result import ModelPipelineResult


class ModelPipeline:
    """Run reasoning, coding, fusion, and verification model stages."""

    def __init__(self, runtime: KernelRuntime) -> None:
        self._runtime = runtime

    def run(self, prompt: str) -> ModelPipelineResult:
        """Run the deterministic model demonstration pipeline."""
        if not prompt:
            raise ValueError("Prompt must not be empty.")

        model_request = ModelRequest(
            messages=[ModelMessage(role=ModelRole.USER, content=prompt)]
        )
        reasoning_request = self._execution_request(
            capability_id="model.reason",
            name="Model Reason",
            description="Generate a reasoning response.",
            payload=model_request,
        )
        coding_request = self._execution_request(
            capability_id="model.code",
            name="Model Code",
            description="Generate a coding response.",
            payload=model_request,
        )

        reasoning_output = self._execute_output(reasoning_request)
        coding_output = self._execute_output(coding_request)

        fused_output: Any | None = None
        try:
            fusion_result = FusionEngine(self._runtime).fuse(
                reasoning_request,
                FusionStrategy.COLLECT_ALL,
            )
            fused_output = fusion_result.fused_output
        except Exception:
            pass

        verifier = SchemaVerifier("model-response", {"text", "model_id"})
        reasoning_verified = (
            reasoning_output is not None
            and verifier.verify(reasoning_output).passed
        )
        coding_verified = (
            coding_output is not None and verifier.verify(coding_output).passed
        )
        return ModelPipelineResult(
            prompt=prompt,
            reasoning_output=reasoning_output,
            coding_output=coding_output,
            fused_output=fused_output,
            verified=reasoning_verified and coding_verified,
        )

    def _execute_output(
        self,
        request: ExecutionRequest[ModelRequest],
    ) -> dict[str, Any] | None:
        """Execute a model stage and return a successful dictionary output."""
        try:
            result = self._runtime.execute_capability(request)
        except Exception:
            return None
        if result.status != ExecutionStatus.SUCCESS:
            return None
        if not isinstance(result.output, dict):
            return None
        return dict(result.output)

    @staticmethod
    def _execution_request(
        capability_id: str,
        name: str,
        description: str,
        payload: ModelRequest,
    ) -> ExecutionRequest[ModelRequest]:
        """Build one model capability execution request."""
        return ExecutionRequest(
            capability=Capability(
                id=capability_id,
                name=name,
                description=description,
                version=CapabilityVersion(major=1, minor=0, patch=0),
            ),
            context=ExecutionContext(),
            payload=payload,
        )
