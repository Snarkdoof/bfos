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
    def __init__(self, bus: SpannerBus, prefix: Optional[str] = None):
        self._bus = bus
        self._prefix = prefix.strip("/") if prefix else None
        self._elements: Dict[str, StatusElement] = {}

    def get_or_create(self, name: str, initial_value: Any = None) -> StatusElement:
        """Get an existing status element or create a new one."""
        full_name = f"{self._prefix}/{name}" if self._prefix else name
        # Remove any leading 'status/' if duplicate
        if full_name.startswith("status/"):
            full_name = full_name[7:]

        if full_name not in self._elements:
            self._elements[full_name] = StatusElement(full_name, initial_value, self._bus)
            # Publish initial value
            self._elements[full_name].set(initial_value, force=True)
        return self._elements[full_name]

    def __getitem__(self, name: str) -> Any:
        """Get the value of a status element."""
        full_name = f"{self._prefix}/{name}" if self._prefix else name
        if full_name.startswith("status/"):
            full_name = full_name[7:]
        if full_name in self._elements:
            return self._elements[full_name].get()
        return None

    def __setitem__(self, name: str, value: Any) -> None:
        """Set the value of a status element (auto-creates if missing)."""
        element = self.get_or_create(name, initial_value=value)
        element.set(value)

    async def set(self, name: str, value: Any) -> None:
        """Async helper method to set a status value and yield execution context."""
        self.__setitem__(name, value)
        await asyncio.sleep(0)

    def keys(self) -> list:
        """Return all tracked status element names."""
        return list(self._elements.keys())
