"""
BFOS - Bag full of spanners 🔧
A lightweight, asynchronous telemetry, configuration, and logging bus for robotic systems.
"""

import logging as std_logging
from typing import Optional, Any
from contextlib import asynccontextmanager

from .bus import SpannerBus
from .status import StatusTracker, StatusElement
from .config import Config
from .logging import SpannerLogHandler, setup_logging
from .recorder import Recorder
from .replayer import Replayer

# --- High-level Global Scaffolding API ---

_default_bus: Optional[SpannerBus] = None
_default_status_tracker: Optional[StatusTracker] = None
_default_recorder: Optional[Recorder] = None

def get_bus() -> SpannerBus:
    """Gets or lazily instantiates the global shared SpannerBus."""
    global _default_bus
    if _default_bus is None:
        _default_bus = SpannerBus()
    return _default_bus

def get_status_tracker(prefix: str = "status") -> StatusTracker:
    """Gets or lazily instantiates the global shared StatusTracker."""
    global _default_status_tracker
    if _default_status_tracker is None:
        _default_status_tracker = StatusTracker(get_bus(), prefix=prefix)
    return _default_status_tracker

def get_logger(name: Optional[str] = None, level: int = std_logging.INFO) -> std_logging.Logger:
    """
    Get a pre-configured standard Python logger that automatically publishes
    all log events onto the global BFOS SpannerBus.
    """
    setup_logging(get_bus(), level=level)
    return std_logging.getLogger(name)

async def set_status(topic: str, value: Any) -> None:
    """Helper function to set a status value on the global StatusTracker."""
    await get_status_tracker().set(topic, value)

def get_status(topic: str) -> Any:
    """Helper function to retrieve a status value from the global StatusTracker."""
    return get_status_tracker().get(topic)

@asynccontextmanager
async def run(
    db_path: Optional[str] = "system_monitor.db",
    retention_seconds: Optional[float] = None,
    log_level: int = std_logging.INFO,
    status_prefix: str = "status"
):
    """
    Async context manager that boots the default BFOS recording engine
    and initializes global status/logging configurations automatically.
    """
    global _default_status_tracker, _default_recorder
    
    # Ensure logging is set up
    setup_logging(get_bus(), level=log_level)
    
    # Configure status tracker
    if _default_status_tracker is None:
        _default_status_tracker = StatusTracker(get_bus(), prefix=status_prefix)
        
    # Setup and start Recorder
    if db_path:
        _default_recorder = Recorder(db_path, get_bus(), retention_seconds=retention_seconds)
        async with _default_recorder:
            yield
    else:
        yield

__all__ = [
    "SpannerBus",
    "StatusTracker",
    "StatusElement",
    "Config",
    "SpannerLogHandler",
    "setup_logging",
    "Recorder",
    "Replayer",
    "get_bus",
    "get_status_tracker",
    "get_logger",
    "set_status",
    "get_status",
    "run",
]
