"""Synchronous in-process event bus for the runtime."""

from .event import Event
from .event_handler import EventHandler
from .event_type import EventType


class EventBus:
    """Publish events to ordered subscribers and retain event history."""

    def __init__(self) -> None:
        self._subscribers: dict[EventType, list[EventHandler]] = {}
        self._history: list[Event] = []

    def subscribe(
        self,
        event_type: EventType,
        handler: EventHandler,
    ) -> None:
        """Subscribe a handler to an event type."""
        self._subscribers.setdefault(event_type, []).append(handler)

    def unsubscribe(
        self,
        event_type: EventType,
        handler: EventHandler,
    ) -> None:
        """Remove a handler if it is subscribed to the event type."""
        handlers = self._subscribers.get(event_type)
        if handlers is None:
            return

        try:
            handlers.remove(handler)
        except ValueError:
            pass

    def publish(self, event: Event) -> None:
        """Record an event and deliver it in subscription order."""
        self._history.append(event)
        for handler in list(self._subscribers.get(event.type, [])):
            handler(event)

    def history(self) -> list[Event]:
        """Return a copy of all published events."""
        return list(self._history)

    def history_by_type(self, event_type: EventType) -> list[Event]:
        """Return published events matching an event type."""
        return [event for event in self._history if event.type == event_type]

    def clear(self) -> None:
        """Clear event history without removing subscribers."""
        self._history.clear()
