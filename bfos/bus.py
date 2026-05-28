import asyncio
import fnmatch
import logging
from typing import Callable, Any, Dict, Set, Union, Coroutine

logger = logging.getLogger("bfos.bus")

# Type alias for callbacks: can be a standard function or a coroutine function
CallbackType = Callable[[str, Any], Union[None, Coroutine[Any, Any, None]]]

class SpannerBus:
    """
    An ultra-lightweight, asynchronous Pub/Sub event bus.
    Supports Unix-style wildcards (e.g. 'status/*' or 'log/ERROR/*') for subscriptions.
    """
    def __init__(self):
        # Maps pattern -> set of callbacks
        self._subscribers: Dict[str, Set[CallbackType]] = {}
        # Cache of topic -> set of matched callbacks for O(1) matching after first lookup
        self._match_cache: Dict[str, Set[CallbackType]] = {}
        self._lock = asyncio.Lock()

    def subscribe(self, pattern: str, callback: CallbackType) -> None:
        """
        Subscribe a callback to a topic pattern.
        Supports wildcards (e.g. 'status/*', '*', 'log/??').
        """
        if pattern not in self._subscribers:
            self._subscribers[pattern] = set()
        self._subscribers[pattern].add(callback)
        self._match_cache.clear()  # Invalidate lookup cache on changes
        logger.debug("Subscribed callback to pattern '%s'", pattern)

    def unsubscribe(self, pattern: str, callback: CallbackType) -> None:
        """Unsubscribe a callback from a topic pattern."""
        if pattern in self._subscribers:
            self._subscribers[pattern].discard(callback)
            if not self._subscribers[pattern]:
                del self._subscribers[pattern]
            self._match_cache.clear()  # Invalidate lookup cache on changes
            logger.debug("Unsubscribed callback from pattern '%s'", pattern)

    async def publish(self, topic: str, payload: Any) -> None:
        """
        Asynchronously publish a payload to a topic.
        Triggers all matching subscriber callbacks in background tasks.
        """
        # Retrieve matched callbacks from cache or compute and cache them
        async with self._lock:
            if topic in self._match_cache:
                matched_callbacks = self._match_cache[topic]
            else:
                matched_set = set()
                for pattern, callbacks in self._subscribers.items():
                    normalized_pattern = pattern.replace("+", "*")
                    if fnmatch.fnmatch(topic, normalized_pattern):
                        matched_set.update(callbacks)
                self._match_cache[topic] = matched_set
                matched_callbacks = matched_set

        if not matched_callbacks:
            return

        # Trigger each callback as an independent task so they run concurrently
        # and do not block each other or the publisher.
        for cb in matched_callbacks:
            asyncio.create_task(self._safe_execute(cb, topic, payload))

    async def _safe_execute(self, callback: CallbackType, topic: str, payload: Any) -> None:
        """Safely execute a callback, catching and logging any exceptions."""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(topic, payload)
            else:
                callback(topic, payload)
        except Exception as e:
            logger.exception("Error executing callback for topic '%s': %s", topic, e)
