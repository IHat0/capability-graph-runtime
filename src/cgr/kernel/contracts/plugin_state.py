"""
Plugin lifecycle states for the Capability Graph Runtime.
"""

from enum import Enum


class PluginState(str, Enum):
    """
    Represents the lifecycle state of a plugin.
    """

    DISCOVERED = "discovered"
    REGISTERED = "registered"
    INITIALIZED = "initialized"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"