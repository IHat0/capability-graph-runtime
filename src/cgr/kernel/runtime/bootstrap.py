"""Convenience bootstrap helpers for the Capability Graph Runtime."""

from cgr.plugins.examples import EchoPlugin
from cgr.plugins.builtin import CalculatorPlugin, TextStatsPlugin
from cgr.plugins.model import MockCodingModelPlugin, MockReasoningModelPlugin

from .kernel_runtime import KernelRuntime


def create_runtime(
    include_examples: bool = False,
    include_builtin: bool = False,
    include_mock_models: bool = False,
) -> KernelRuntime:
    """Create a runtime, optionally including the example Echo plugin."""
    runtime = KernelRuntime()
    if include_examples:
        runtime.register_plugin(EchoPlugin())
    if include_builtin:
        runtime.register_plugin(CalculatorPlugin())
        runtime.register_plugin(TextStatsPlugin())
    if include_mock_models:
        runtime.register_plugin(MockReasoningModelPlugin())
        runtime.register_plugin(MockCodingModelPlugin())

    return runtime
