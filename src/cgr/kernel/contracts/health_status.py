"""
Plugin health states for the Capability Graph Runtime.
"""

from enum import Enum


class HealthStatus(str, Enum):
    """
    Represents the operational health of a plugin.
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"