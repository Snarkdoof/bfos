import time
import asyncio
from typing import Any, Dict, Optional
from .bus import SpannerBus

class StatusElement:
    """Represents a single telemetry status parameter."""
    def __init__(self, name: str, value: Any, bus: SpannerBus):
        self.name = name
        self._value = value
        self._timestamp = time.time()
        self._bus = bus
        self._lock = asyncio.Lock()

    @property
    def timestamp(self) -> float:
        return self._timestamp

    def get(self) -> Any:
        """Get the current value."""
        return self._value

    def set(self, new_value: Any, force: bool = False) -> None:
        """
        Set a new value. If the value has changed (or force is True),
        updates the value and publishes the change event to the SpannerBus.
        """
        if self._value != new_value or force:
            self._value = new_value
            self._timestamp = time.time()
            # Publish change event in a non-blocking background task
            asyncio.create_task(self._bus.publish(
                f"status/{self.name}",
                {
                    "timestamp": self._timestamp,
                    "name": self.name,
                    "value": new_value
                }
            ))

    def __str__(self) -> str:
        return f"{self.name}={self._value}"


class StatusTracker:
    """Manages a collection of StatusElements and integrates them with a SpannerBus."""
    def __init__(self, bus: SpannerBus):
        self._bus = bus
        self._elements: Dict[str, StatusElement] = {}
        self._lock = threading_lock = None  # We'll use a local lock if needed, but dict ops in Python are atomic

    def get_or_create(self, name: str, initial_value: Any = None) -> StatusElement:
        """Get an existing status element or create a new one."""
        if name not in self._elements:
            self._elements[name] = StatusElement(name, initial_value, self._bus)
            # Publish initial value
            self._elements[name].set(initial_value, force=True)
        return self._elements[name]

    def __getitem__(self, name: str) -> Any:
        """Get the value of a status element."""
        if name in self._elements:
            return self._elements[name].get()
        return None

    def __setitem__(self, name: str, value: Any) -> None:
        """Set the value of a status element (auto-creates if missing)."""
        element = self.get_or_create(name, initial_value=value)
        element.set(value)

    def keys(self) -> list:
        """Return all tracked status element names."""
        return list(self._elements.keys())
