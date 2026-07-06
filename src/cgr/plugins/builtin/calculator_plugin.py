"""Built-in safe arithmetic calculator plugin."""

import ast
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

Number = int | float


class CalculatorPlugin(Plugin[Any, Any]):
    """Evaluate arithmetic expressions by interpreting a restricted AST."""

    def __init__(self) -> None:
        self._state = PluginState.DISCOVERED
        self._metadata = PluginMetadata(
            id="builtin.calculator",
            name="Built-in Calculator",
            version="1.0.0",
            author="CGR",
            description="Evaluates safe arithmetic expressions.",
            capabilities=[
                Capability(
                    id="calculator.evaluate",
                    name="Calculator Evaluate",
                    description="Evaluate a safe arithmetic expression.",
                    version=CapabilityVersion(major=1, minor=0, patch=0),
                    tags=["builtin", "tool", "calculator", "math"],
                )
            ],
            tags=["builtin", "tool", "calculator", "math"],
        )

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata

    @property
    def state(self) -> PluginState:
        return self._state

    @property
    def health(self) -> HealthStatus:
        if self._state == PluginState.RUNNING:
            return HealthStatus.HEALTHY
        return HealthStatus.DEGRADED

    def initialize(self) -> None:
        self._state = PluginState.RUNNING

    def shutdown(self) -> None:
        self._state = PluginState.STOPPED

    def execute(
        self,
        request: ExecutionRequest[Any],
    ) -> ExecutionResult[Any]:
        payload = request.payload
        if not isinstance(payload, dict):
            raise ValueError("Calculator payload must be a dictionary.")
        expression = payload.get("expression")
        if not isinstance(expression, str):
            raise ValueError(
                "Calculator payload must contain a string expression."
            )
        if len(expression) > 500:
            raise ValueError("Expression exceeds maximum length of 500 characters.")

        parsed = ast.parse(expression, mode="eval")
        result = self._evaluate(parsed)
        return ExecutionResult(
            context=request.context,
            status=ExecutionStatus.SUCCESS,
            output={"expression": expression, "result": result},
            duration_ms=0.0,
        )

    def _evaluate(self, node: ast.AST) -> Number:
        """Evaluate one allowed arithmetic AST node."""
        if isinstance(node, ast.Expression):
            return self._evaluate(node.body)
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)) or isinstance(
                node.value, bool
            ):
                raise ValueError("Unsupported expression.")
            return node.value
        if isinstance(node, ast.UnaryOp):
            operand = self._evaluate(node.operand)
            if isinstance(node.op, ast.UAdd):
                return +operand
            if isinstance(node.op, ast.USub):
                return -operand
            raise ValueError("Unsupported expression.")
        if isinstance(node, ast.BinOp):
            left = self._evaluate(node.left)
            right = self._evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
            if isinstance(node.op, ast.Mod):
                return left % right
            if isinstance(node.op, ast.Pow):
                return left**right
        raise ValueError("Unsupported expression.")
