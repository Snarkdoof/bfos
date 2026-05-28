import asyncio
import os
import sys
import shutil
import logging

# Ensure parent directory is in sys.path so bfos module is findable
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bfos

# Get a pre-configured logger that automatically publishes standard logs directly to the BFOS bus
logger = bfos.get_logger("system_monitor")

async def monitor_loop():
    """Periodically queries system health and publishes updates directly via the zero-scaffolding API."""
    logger.info("System health monitoring started.")
    logger.info("Monitoring root disk partition at '/'")
    
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

            # Update telemetry values using the simple high-level API
            await bfos.set_status("memory/total_bytes", total_mem)
            await bfos.set_status("memory/free_bytes", free_mem)
            await bfos.set_status("memory/used_percent", mem_used_pct)
            
            await bfos.set_status("cpu/load_1m", cpu_load[0])
            await bfos.set_status("cpu/load_5m", cpu_load[1])
            await bfos.set_status("cpu/load_15m", cpu_load[2])
            
            await bfos.set_status("disk/total_bytes", total_disk)
            await bfos.set_status("disk/free_bytes", free_disk)
            await bfos.set_status("disk/used_percent", disk_used_pct)

        except Exception as e:
            logger.error(f"Error querying system health statistics: {e}", exc_info=True)

        await asyncio.sleep(2.0)

async def main():
    # Watch system error logs and status variables
    async def console_watcher(topic, payload):
        print(f"📡 [BUS TELEMETRY] {topic} -> {payload}")

    bus = bfos.get_bus()
    # Watch all status variables as they stream live
    bus.subscribe("status/monitor/#", console_watcher)
    # Also watch system error logs
    bus.subscribe("log/ERROR", console_watcher)

    db_file = "system_monitor.db"
    retention_time = 10.0  # Keep only the last 10 seconds of logs to prevent disk clutter!
    
    # Run the high-level BFOS scaffolding with automated recording & retention
    async with bfos.run(db_path=db_file, retention_seconds=retention_time, status_prefix="status/monitor"):
        try:
            await monitor_loop()
        except asyncio.CancelledError:
            print("\nShutting down monitor loop...")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nMonitor terminated.")
