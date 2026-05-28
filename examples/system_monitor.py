import asyncio
import os
import sys
import shutil
import logging
import time

# Ensure parent directory is in sys.path so bfos module is findable
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bfos import SpannerBus, StatusTracker, Recorder, setup_logging

# Configure logging to go both to stdout and to the bus structured
logger = logging.getLogger("system_monitor")

async def monitor_loop(bus: SpannerBus, status: StatusTracker):
    """Periodically queries system health and publishes updates to the SpannerBus."""
    logger.info("System health monitoring started.")
    
    while True:
        try:
            # 1. Gather Free Memory and Total Memory (cross-platform fallback)
            free_mem = 0
            total_mem = 0
            if os.path.exists("/proc/meminfo"):
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if "MemTotal" in line:
                            total_mem = int(line.split()[1]) * 1024  # kB to bytes
                        elif "MemAvailable" in line or "MemFree" in line:
                            # Prefer MemAvailable over MemFree if present
                            free_mem = int(line.split()[1]) * 1024
            else:
                # Mock memory metrics on macOS/Windows environments
                total_mem = 16 * 1024 * 1024 * 1024
                free_mem = 8 * 1024 * 1024 * 1024

            mem_used_pct = round(((total_mem - free_mem) / total_mem) * 100.0, 2) if total_mem > 0 else 0

            # 2. Gather CPU Load Average
            cpu_load = [0.0, 0.0, 0.0]
            try:
                cpu_load = list(os.getloadavg())
            except (AttributeError, OSError):
                # Fallback load avg simulation if unsupported on OS
                import random
                cpu_load = [round(random.uniform(0.1, 2.0), 2) for _ in range(3)]

            # 3. Gather Disk Space
            total_disk, used_disk, free_disk = shutil.disk_usage("/")
            disk_used_pct = round((used_disk / total_disk) * 100.0, 2) if total_disk > 0 else 0

            # Update telemetry values on the bus
            # StatusTracker publishes on the bus ONLY if these values have changed!
            await status.set("memory/total_bytes", total_mem)
            await status.set("memory/free_bytes", free_mem)
            await status.set("memory/used_percent", mem_used_pct)
            
            await status.set("cpu/load_1m", cpu_load[0])
            await status.set("cpu/load_5m", cpu_load[1])
            await status.set("cpu/load_15m", cpu_load[2])
            
            await status.set("disk/total_bytes", total_disk)
            await status.set("disk/free_bytes", free_disk)
            await status.set("disk/used_percent", disk_used_pct)

            # Log a info notification
            logger.info(
                f"Status update: CPU 1m={cpu_load[0]} | Mem Used={mem_used_pct}% | Disk Free={free_disk // (1024**3)} GB"
            )

        except Exception as e:
            logger.error(f"Error querying system health statistics: {e}", exc_info=True)

        await asyncio.sleep(2.0)

async def main():
    # Setup bus and redirect standard logging output directly onto the bus log topics
    bus = SpannerBus()
    setup_logging(bus, level=logging.INFO)

    # Status tracker prefixes all variables with status/monitor/
    status = StatusTracker(bus, prefix="status/monitor")

    # Define simple watcher to show real-time bus telemetry
    async def console_watcher(topic, payload):
        print(f"📡 [BUS TELEMETRY] {topic} -> {payload}")

    # Watch all status variables as they stream live
    bus.subscribe("status/monitor/#", console_watcher)
    # Also watch system error logs
    bus.subscribe("log/ERROR", console_watcher)

    # Setup the SQLite recorder with a 10-second data retention period (Keep it small for testing)
    # For a robotic mower, you'd set retention_seconds = 7 * 24 * 3600 (1 week)
    db_file = "system_monitor.db"
    retention_time = 10.0  # Keep only the last 10 seconds of logs to prevent disk clutter!
    
    print(f"\n🚀 Starting System Health Monitor...")
    print(f"💾 Recording to database '{db_file}' with {retention_time}s dynamic retention limit")
    print("Press Ctrl+C to terminate and inspect the database file.")

    async with Recorder(db_file, bus, retention_seconds=retention_time) as recorder:
        try:
            await monitor_loop(bus, status)
        except asyncio.CancelledError:
            print("\nShutting down monitor loop...")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nMonitor terminated.")
