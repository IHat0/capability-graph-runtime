"""Roles used by model conversation messages."""

from enum import Enum


class ModelRole(str, Enum):
    """Supported roles for model messages."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
