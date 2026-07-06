"""
Execution status definitions for the Capability Graph Runtime.
"""

from enum import Enum


class ExecutionStatus(str, Enum):
    """
    Represents the lifecycle state of an execution.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"