import logging
import asyncio
import time
from typing import Optional
from .bus import SpannerBus

class SpannerLogHandler(logging.Handler):
    """
    Custom logging Handler that forwards all standard Python log records
    onto the SpannerBus as structured messages.
    """
    def __init__(self, bus: SpannerBus, level: int = logging.NOTSET):
        super().__init__(level=level)
        self._bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        try:
            log_entry = {
                "timestamp": record.created,
                "logger": record.name,
                "level": record.levelname,
                "level_no": record.levelno,
                "message": self.format(record),
                "pathname": record.pathname,
                "lineno": record.lineno,
                "funcName": record.funcName
            }
            # Publish to topic log/<LEVEL> (e.g. log/INFO, log/ERROR)
            topic = f"log/{record.levelname}"
            
            # Since logging is usually synchronous, we schedule publishing
            # as a non-blocking background task in the running loop
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    loop.create_task(self._bus.publish(topic, log_entry))
            except RuntimeError:
                # If there's no running async event loop in this thread, we cannot publish
                # to the async bus directly, but we can print a debug/warning.
                pass
        except Exception:
            self.handleError(record)


def setup_logging(bus: SpannerBus, level: int = logging.INFO, logger_name: Optional[str] = None) -> SpannerLogHandler:
    """
    Helper function to configure standard python logging to pipe through BFOS.
    Attaches SpannerLogHandler to the specified logger (or root if logger_name is None).
    """
    target_logger = logging.getLogger(logger_name)
    target_logger.setLevel(level)

    # Prevent duplicating if handler is already attached
    for handler in list(target_logger.handlers):
        if isinstance(handler, SpannerLogHandler):
            return handler

    handler = SpannerLogHandler(bus)
    # Standard clean formatting for console if desired, but we keep raw values for the bus.
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
    handler.setFormatter(formatter)
    handler.setLevel(level)
    target_logger.addHandler(handler)
    return handler
