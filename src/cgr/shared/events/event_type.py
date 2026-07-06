"""Event type definitions for the Capability Graph Runtime."""

from enum import Enum


class EventType(str, Enum):
    """Types of events published within the runtime."""

    EXECUTION_STARTED = "execution.started"
    EXECUTION_COMPLETED = "execution.completed"
    EXECUTION_FAILED = "execution.failed"
    PLUGIN_REGISTERED = "plugin.registered"
    PLUGIN_UNREGISTERED = "plugin.unregistered"
