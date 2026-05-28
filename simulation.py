"""
Bag full of spanners (BFOS) - Mock Lawn Mower Simulation 🔧🚜

This script simulates a robotic lawn mower equipped with GPS, IMU, and Battery sensors.
It demonstrates real-time status tracking, dynamic logging, database recording,
and exact telemetry playback.
"""

import asyncio
import logging
import time
import sqlite3
import os
from bfos.bus import SpannerBus
from bfos.status import StatusTracker
from bfos.config import Config
from bfos.logging import setup_logging
from bfos.recorder import Recorder
from bfos.replayer import Replayer

# Configure terminal logging
logging.basicConfig(level=logging.INFO, format="[Live] %(message)s")
logger = logging.getLogger("mower")

async def gps_driver(tracker: StatusTracker):
    """Simulates a GPS module acquiring a lock and updating coordinates."""
    logger.info("GPS driver starting up...")
    await asyncio.sleep(0.2)
    tracker["gps_fix"] = "3D_FIX"
    logger.info("GPS 3D Lock Acquired!")
    
    # Mock coordinates
    coords = [59.9138, 10.7522] # Oslo
    for _ in range(5):
        coords[0] += 0.0001
        coords[1] += 0.0002
        tracker["gps_coords"] = f"{coords[0]:.6f},{coords[1]:.6f}"
        await asyncio.sleep(0.5)

async def battery_driver(tracker: StatusTracker):
    """Simulates battery depletion."""
    battery = 99.0
    for _ in range(5):
        tracker["battery_level"] = battery
        if battery < 95.0:
            logger.warning("Battery level is dropping below 95%!")
        battery -= 1.5
        await asyncio.sleep(0.6)

async def imu_driver(tracker: StatusTracker):
    """Simulates high-frequency IMU telemetry (Euler angles)."""
    pitch, roll, yaw = 0.0, 0.0, 180.0
    for i in range(15):
        pitch = i * 0.1
        roll = i * -0.05
        yaw = (yaw + 1.0) % 360.0
        tracker["imu_yaw"] = f"{yaw:.2f}"
        await asyncio.sleep(0.2)

async def run_simulation(bus: SpannerBus, tracker: StatusTracker, db_path: str):
    # Setup recorder to capture everything
    recorder = Recorder(db_path, bus)
    await recorder.start()

    logger.info("=========================================")
    logger.info("Starting Robotic Lawn Mower Simulation...")
    logger.info("=========================================")

    # Run sensor tasks concurrently
    await asyncio.gather(
        gps_driver(tracker),
        battery_driver(tracker),
        imu_driver(tracker)
    )

    # Let remaining writes flush and stop recording
    await asyncio.sleep(0.2)
    await recorder.stop()
    logger.info("Simulation completed and telemetry recorded.")

def inspect_database(db_path: str):
    """Reads and prints contents of the recorded SQLite database."""
    logger.info("\n=========================================")
    logger.info("Inspecting Recorded SQLite DB Telemetry:")
    logger.info("=========================================")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, timestamp, topic, payload FROM telemetry ORDER BY id ASC LIMIT 15;")
    for row in cursor.fetchall():
        print(f"ID: {row[0]} | Time: {row[1]:.4f} | Topic: {row[2]} | Payload: {row[3]}")
    
    cursor.execute("SELECT COUNT(*) FROM telemetry;")
    total = cursor.fetchone()[0]
    print(f"\n[DB] Total recorded rows: {total}")
    conn.close()

async def play_back_telemetry(db_path: str):
    logger.info("\n=========================================")
    logger.info("Initiating Playback of Recorded Telemetry...")
    logger.info("=========================================")
    
    # Set up a clean playback bus & tracker to receive replayed data
    playback_bus = SpannerBus()
    
    # Print replayed events to terminal
    def print_replayed(topic, payload):
        print(f"[Playback] REPLAYED: Topic: {topic} -> {payload}")

    playback_bus.subscribe("status/*", print_replayed)
    playback_bus.subscribe("log/*", print_replayed)

    replayer = Replayer(db_path, playback_bus)
    
    # Play at 2.0x accelerated speed!
    await replayer.play(speed_factor=2.0)
    # Give the replayer time to finish
    await asyncio.sleep(2.0)
    await replayer.stop()

async def main():
    db_path = "mower_telemetry.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    bus = SpannerBus()
    tracker = StatusTracker(bus)
    
    # Configure dynamic logging handler to push logging onto SpannerBus
    setup_logging(bus, level=logging.INFO)

    # Run the live simulation and record
    await run_simulation(bus, tracker, db_path)

    # Inspect the saved SQLite DB
    inspect_database(db_path)

    # Play back everything that was recorded!
    await play_back_telemetry(db_path)

    # Cleanup DB
    if os.path.exists(db_path):
        os.remove(db_path)
        # SQLite WAL files cleanup
        for ext in ["-wal", "-shm"]:
            if os.path.exists(db_path + ext):
                os.remove(db_path + ext)

if __name__ == "__main__":
    asyncio.run(main())
