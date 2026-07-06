"""Convenience bootstrap helpers for the Capability Graph Runtime."""

from cgr.plugins.examples import EchoPlugin

from .kernel_runtime import KernelRuntime


def create_runtime(include_examples: bool = False) -> KernelRuntime:
    """Create a runtime, optionally including the example Echo plugin."""
    runtime = KernelRuntime()
    if include_examples:
        runtime.register_plugin(EchoPlugin())

    return runtime
