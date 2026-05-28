import sqlite3
import json
import logging
import asyncio
import time
from typing import Optional, List, Tuple
from .bus import SpannerBus

logger = logging.getLogger("bfos.replayer")

class Replayer:
    """
    Reads recorded telemetry logs from a SQLite database and republishes them onto 
    the SpannerBus, preserving original relative timestamps with optional speed factors.
    Uses drift compensation for microsecond playback accuracy.
    """
    def __init__(self, db_path: str, bus: SpannerBus):
        self._db_path = db_path
        self._bus = bus
        self._playback_task: Optional[asyncio.Task] = None
        self._is_playing = False

    async def play(self, speed_factor: float = 1.0, loop_playback: bool = False) -> None:
        """
        Asynchronously play back the recorded telemetry.
        Compensates for sleep inaccuracies to prevent timeline drift.
        """
        if self._is_playing:
            return

        self._is_playing = True
        self._playback_task = asyncio.create_task(self._playback_loop(speed_factor, loop_playback))
        logger.info("Playback started with speed_factor = %s", speed_factor)

    async def stop(self) -> None:
        """Stop playback immediately."""
        if not self._is_playing:
            return

        self._is_playing = False
        if self._playback_task:
            self._playback_task.cancel()
            try:
                await self._playback_task
            except asyncio.CancelledError:
                pass
            self._playback_task = None
        logger.info("Playback stopped.")

    async def _playback_loop(self, speed_factor: float, loop_playback: bool) -> None:
        loop = asyncio.get_running_loop()

        def fetch_first_timestamp() -> Optional[float]:
            try:
                conn = sqlite3.connect(self._db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT timestamp FROM telemetry ORDER BY timestamp ASC LIMIT 1;")
                row = cursor.fetchone()
                conn.close()
                return row[0] if row else None
            except Exception as e:
                logger.error("Failed to query first timestamp from '%s': %s", self._db_path, e)
                return None

        def fetch_chunk(last_id: int, limit: int = 2000) -> List[Tuple[int, float, str, str]]:
            try:
                conn = sqlite3.connect(self._db_path)
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, timestamp, topic, payload FROM telemetry WHERE id > ? ORDER BY id ASC LIMIT ?;",
                    (last_id, limit)
                )
                rows = cursor.fetchall()
                conn.close()
                return rows
            except Exception as e:
                logger.error("Failed to query chunk from '%s': %s", self._db_path, e)
                return []

        while self._is_playing:
            first_record_ts = await loop.run_in_executor(None, fetch_first_timestamp)
            if first_record_ts is None:
                logger.warning("No records found in database to play back.")
                break

            logger.info("Commencing playback with streamed DB chunking.")
            
            playback_start_real = time.time()
            last_id = 0
            chunk_limit = 2000
            
            while self._is_playing:
                # Load a chunk of records in threadpool to avoid memory blockages
                records = await loop.run_in_executor(None, fetch_chunk, last_id, chunk_limit)
                if not records:
                    break  # Reached end of database
                
                for r_id, ts, topic, payload_str in records:
                    if not self._is_playing:
                        break
                    
                    last_id = r_id
                    
                    # Calculate relative offset from first record
                    offset_from_start = ts - first_record_ts
                    # Scaled offset depending on speed_factor
                    scaled_offset = offset_from_start / speed_factor
                    
                    # Target wall clock time for this record to be published
                    target_publish_time = playback_start_real + scaled_offset
                    
                    # Sleep until target time
                    now = time.time()
                    time_to_sleep = target_publish_time - now
                    if time_to_sleep > 0:
                        await asyncio.sleep(time_to_sleep)
                    
                    # Deserialize and publish
                    try:
                        payload = json.loads(payload_str)
                        await self._bus.publish(topic, payload)
                    except Exception as e:
                        logger.error("Error republishing topic '%s' during playback: %s", topic, e)

            if not loop_playback:
                break
            
            logger.info("Looping playback...")

        self._is_playing = False
        logger.info("Playback loop finished.")
pre_init = None
