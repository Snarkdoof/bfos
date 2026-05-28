# "Bag full of spanners" (BFOS) 🔧 - Developer Skills & Guide

**BFOS** is an ultra-lightweight, high-performance, asynchronous Python-native framework designed to separate **Logging**, **Status (telemetry)**, and **Configuration** on autonomous robotic systems. 

It provides robust, real-time message passing (via an `asyncio` Pub/Sub bus with wildcard support), atomic configuration management, standardized logging ingestion, and precise, drift-compensated time-series record & replay via an optimized SQLite WAL (Write-Ahead Logging) storage back-end.

---

## Architecture Blueprint

```text
[ Sensor / Driver ] --(Logs / Status)--> [ SpannerBus ] <--> [ Dynamic Config ]
                                              │
                                              v (Record / Capture)
                                       [ SQLite Database (WAL) ]
                                              │
                                              v (Chronological Playback)
                                       [ High-Fidelity Replayer ]
```

---

## 1. Core Quickstart

### Initialize the Bus & Status Tracker
```python
import asyncio
from bfos import SpannerBus, StatusTracker

async def main():
    bus = SpannerBus()
    tracker = StatusTracker(bus)

    # Subscribe to status changes using wildcards
    def on_status_change(topic, payload):
        print(f"Status changed: {topic} -> {payload['value']}")

    bus.subscribe("status/*", on_status_change)

    # Set parameters - updates are automatically published
    tracker["battery_level"] = 98.5
    tracker["gps_coords"] = "59.9138,10.7522"
    
    # Wait for background publish tasks to settle
    await asyncio.sleep(0.1)

asyncio.run(main())
```

---

## 2. Dynamic Configuration (`bfos.config`)
Manage configurations stored in a local, reloadable JSON file. Updates are saved atomically (preventing file corruption on power cut) and publish a notification allowing modules to dynamically adjust runtime settings.

```python
from bfos import Config, SpannerBus

bus = SpannerBus()
config = Config("mower_config.json", bus)

# Subscribe to max speed modifications
def on_speed_change(topic, payload):
    print(f"Max speed reloaded to: {payload['new_value']}")

bus.subscribe("config/changed/max_speed", on_speed_change)

# Set value - instantly updates local file and triggers callback
config.set("max_speed", 1.8)
```

---

## 3. Structured Logging (`bfos.logging`)
Hook Python's standard `logging` library directly into the SpannerBus. Log entries are structured and dispatched to topics like `log/INFO` or `log/WARNING`.

```python
import logging
from bfos import SpannerBus, setup_logging

bus = SpannerBus()

# Route all standard logs onto the SpannerBus
setup_logging(bus, level=logging.INFO)

logger = logging.getLogger("navigation")
logger.warning("Lidar path blocked. Re-routing!")
```

---

## 4. Telemetry Recorder & Replayer (`bfos.recorder`, `bfos.replayer`)
The recording mechanism leverages SQLite in **WAL (Write-Ahead Logging) mode** and **NORMAL synchronization** to allow fast, non-blocking, and corrupt-free transactions.

### Recording Telemetry
```python
from bfos import SpannerBus, Recorder

bus = SpannerBus()
recorder = Recorder("mower_session.db", bus)

# Start recording all events (status/*, config/*, log/*)
await recorder.start()

# ... run mower, perform operations ...

# Safely flush queue and stop
await recorder.stop()
```

### High-Fidelity Drift-Compensated Playback
The `Replayer` reads records and republishes them to a SpannerBus. It uses dynamic drift compensation (adjusting sleeps to target wall-clock time offsets) to prevent time dilation, even during prolonged playback runs.

```python
from bfos import SpannerBus, Replayer

playback_bus = SpannerBus()

# Listen to replayed status changes
playback_bus.subscribe("status/*", lambda t, p: print(f"Playback: {t} -> {p}"))

replayer = Replayer("mower_session.db", playback_bus)

# Play back at 2x double speed!
await replayer.play(speed_factor=2.0)
```

---

## 5. Deployment & System Requirements
*   **Operating System:** Linux / macOS / Windows
*   **Python Version:** Python 3.8+ (Supports 3.13+)
*   **Dependencies:** **Zero external dependencies!** Uses Python's standard library `asyncio`, `sqlite3`, `json`, and `logging`.
