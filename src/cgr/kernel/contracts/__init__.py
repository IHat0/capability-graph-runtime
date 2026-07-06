"""
Core contracts exposed by the Capability Graph Runtime.
"""

from .capability import Capability
from .capability_version import CapabilityVersion
from .execution_context import ExecutionContext
from .execution_request import ExecutionRequest
from .execution_result import ExecutionResult
from .execution_status import ExecutionStatus
from .health_status import HealthStatus
from .plugin import Plugin
from .plugin_metadata import PluginMetadata
from .plugin_state import PluginState

__all__ = [
    "Capability",
    "CapabilityVersion",
    "ExecutionContext",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionStatus",
    "HealthStatus",
    "Plugin",
    "PluginMetadata",
    "PluginState",
]