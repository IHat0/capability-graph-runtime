from cgr.kernel.exceptions import (
    CGRRuntimeError,
    CapabilityNotFoundError,
    PluginAlreadyRegisteredError,
    PluginExecutionError,
    PluginNotFoundError,
)


def test_runtime_errors_share_cgr_base_class() -> None:
    error_types = (
        PluginNotFoundError,
        CapabilityNotFoundError,
        PluginAlreadyRegisteredError,
        PluginExecutionError,
    )

    assert all(issubclass(error_type, CGRRuntimeError) for error_type in error_types)
