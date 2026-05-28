import sqlite3
import json
import logging
import asyncio
from typing import Optional, Any
from .bus import SpannerBus

logger = logging.getLogger("bfos.recorder")

class Recorder:
    """
    Subscribes to all events on the SpannerBus and records them chronologically
    into a local SQLite database. Uses WAL journal mode for lightweight, concurrent,
    and power-failure-resilient transactional logging.
    """
    def __init__(self, db_path: str, bus: SpannerBus, pattern: str = "*"):
        self._db_path = db_path
        self._bus = bus
        self._pattern = pattern
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        self._conn: Optional[sqlite3.Connection] = None

    def _init_db(self) -> None:
        """Initialize the SQLite schema and configure optimal telemetry performance."""
        # check_same_thread=False is crucial as the connection is accessed across multiple executor threadpool threads
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        # Enable WAL mode for asynchronous concurrent writes & read performance
        self._conn.execute("PRAGMA journal_mode=WAL;")
        # Reduce fsync frequency safely for performance while keeping transactions atomic
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        
        # Create schema
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                topic TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)
        # Index on timestamp for time-accurate fast querying during playback
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_timestamp ON telemetry (timestamp);")
        self._conn.commit()
        logger.info("Initialized SQLite telemetry recording database at '%s'", self._db_path)

    async def start(self) -> None:
        """Start recording bus events to the SQLite database."""
        if self._running:
            return

        # Initialize the database file and table
        # Running in executor to avoid blocking the asyncio event loop during physical IO
        await asyncio.get_running_loop().run_in_executor(None, self._init_db)

        self._running = True
        self._bus.subscribe(self._pattern, self._on_bus_event)
        self._worker_task = asyncio.create_task(self._writer_loop())
        logger.info("Telemetry recorder started.")

    async def stop(self) -> None:
        """Stop recording, processing any remaining queued writes."""
        if not self._running:
            return

        self._running = False
        self._bus.unsubscribe(self._pattern, self._on_bus_event)

        # Wait for remaining queued items to be processed
        if self._worker_task:
            # Signal the worker task to finish by putting None in the queue
            await self._queue.put(None)
            await self._worker_task
            self._worker_task = None

        if self._conn:
            await asyncio.get_running_loop().run_in_executor(None, self._conn.close)
            self._conn = None
        logger.info("Telemetry recorder stopped.")

    def _on_bus_event(self, topic: str, payload: Any) -> None:
        """Subscriber callback. Places the incoming message into the queue."""
        if self._running:
            self._queue.put_nowait((topic, payload))

    async def _writer_loop(self) -> None:
        """Background coroutine that handles writing events to SQLite in batches or sequentially."""
        loop = asyncio.get_running_loop()
        
        while self._running or not self._queue.empty():
            try:
                item = await self._queue.get()
            except asyncio.CancelledError:
                break

            if item is None:
                # None is used as a sentinel shutdown signal
                self._queue.task_done()
                break

            batch = [item]

            # Gather any other currently queued items to write them in a single batch transaction
            # for maximum performance.
            while len(batch) < 100:
                try:
                    next_item = self._queue.get_nowait()
                    if next_item is None:
                        # Sentinel for shutdown: put it back so next loop iteration handles it, and break batch gathering
                        await self._queue.put(None)
                        break
                    batch.append(next_item)
                except asyncio.QueueEmpty:
                    break

            def write_batch():
                if not self._conn:
                    return
                try:
                    rows = []
                    for topic, payload in batch:
                        try:
                            serialized_payload = json.dumps(payload)
                            timestamp = payload.get("timestamp") if isinstance(payload, dict) else None
                            if timestamp is None:
                                import time
                                timestamp = time.time()
                            rows.append((float(timestamp), topic, serialized_payload))
                        except Exception as serialize_err:
                            logger.error("Failed to serialize payload on topic '%s': %s", topic, serialize_err)

                    if rows:
                        self._conn.executemany(
                            "INSERT INTO telemetry (timestamp, topic, payload) VALUES (?, ?, ?);",
                            rows
                        )
                        self._conn.commit()
                except Exception as e:
                    logger.error("SQLite batch write error: %s", e)

            try:
                await loop.run_in_executor(None, write_batch)
            except Exception as e:
                logger.error("Failed to execute batch write: %s", e)
            finally:
                for _ in range(len(batch)):
                    self._queue.task_done()
