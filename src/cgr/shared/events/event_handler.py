"""Event handler contract for in-process subscribers."""

from typing import Protocol

from .event import Event


class EventHandler(Protocol):
    """Callable that handles a published event."""

    def __call__(self, event: Event, /) -> None:
        """Handle an event."""
        ...
