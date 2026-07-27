"""Pulsate Labs product API."""

from importlib import import_module
from typing import Any

__all__ = ["app"]


def __getattr__(name: str) -> Any:
    if name != "app":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    resolved_app = import_module(".app", __name__).app
    globals()["app"] = resolved_app
    return resolved_app
