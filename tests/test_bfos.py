import os
import unittest
import asyncio
import tempfile
import logging
import time
from bfos.bus import SpannerBus
from bfos.status import StatusTracker
from bfos.config import Config
from bfos.logging import setup_logging, SpannerLogHandler
from bfos.recorder import Recorder
from bfos.replayer import Replayer

# Configure root logging for tests
logging.basicConfig(level=logging.INFO)

class TestBFOS(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.bus = SpannerBus()

    def tearDown(self):
        self.loop.close()

    def test_bus_pub_sub_wildcards(self):
        """Test wildcard subscription matching."""
        received = []

        async def callback(topic, payload):
            received.append((topic, payload))

        self.bus.subscribe("status/*", callback)
        self.bus.subscribe("status/battery/+", callback) # fnmatch style ? or glob style *
        self.bus.subscribe("log/ERROR", callback)

        async def run():
            await self.bus.publish("status/battery/level", 92)
            await self.bus.publish("status/gps", "fix")
            await self.bus.publish("log/INFO", "starting")
            await self.bus.publish("log/ERROR", "failure")
            # Wait briefly for background callbacks to complete
            await asyncio.sleep(0.05)

        self.loop.run_until_complete(run())

        self.assertEqual(len(received), 3)
        topics = [item[0] for item in received]
        self.assertIn("status/battery/level", topics)
        self.assertIn("status/gps", topics)
        self.assertIn("log/ERROR", topics)

    def test_status_tracker_updates(self):
        """Test that updating status tracking parameters publishes updates to the bus."""
        tracker = StatusTracker(self.bus)
        received_updates = []

        def callback(topic, payload):
            received_updates.append(payload)

        self.bus.subscribe("status/speed", callback)

        async def run():
            tracker["speed"] = 1.2
            await asyncio.sleep(0.05)
            tracker["speed"] = 1.5
            await asyncio.sleep(0.05)

        self.loop.run_until_complete(run())

        self.assertEqual(len(received_updates), 2)
        self.assertEqual(received_updates[0]["value"], 1.2)
        self.assertEqual(received_updates[1]["value"], 1.5)

    def test_dynamic_config(self):
        """Test dynamic file configuration reads, atomic writes, and reload events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.json")
            cfg = Config(config_path, self.bus)

            received_changes = []
            def on_config_change(topic, payload):
                received_changes.append(payload)

            self.bus.subscribe("config/changed/*", on_config_change)

            async def run():
                cfg.set("max_speed", 2.0)
                await asyncio.sleep(0.05)

            self.loop.run_until_complete(run())

            self.assertEqual(cfg.get("max_speed"), 2.0)
            self.assertEqual(len(received_changes), 1)
            self.assertEqual(received_changes[0]["key"], "max_speed")
            self.assertEqual(received_changes[0]["new_value"], 2.0)

            # Check that it reload/reads back correctly on new initialization
            cfg2 = Config(config_path, self.bus)
            self.assertEqual(cfg2.get("max_speed"), 2.0)

    def test_recorder_and_replayer(self):
        """Test full SQLite recorder and replayer workflow."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "telemetry.db")
            recorder = Recorder(db_path, self.bus)
            
            replayed_events = []
            async def on_replayed_event(topic, payload):
                replayed_events.append((topic, payload))

            async def run():
                # Start recording
                await recorder.start()

                # Publish events
                await self.loop.create_task(self.bus.publish("status/battery", {"timestamp": time.time(), "value": 80}))
                await asyncio.sleep(0.05)
                await self.loop.create_task(self.bus.publish("status/gps", {"timestamp": time.time(), "value": "FIX"}))
                await asyncio.sleep(0.05)

                # Stop recording
                await recorder.stop()

                # Set up a new bus to play back into, simulating a separate system instance
                playback_bus = SpannerBus()
                playback_bus.subscribe("*", on_replayed_event)

                replayer = Replayer(db_path, playback_bus)
                # Play back at 5x speed
                await replayer.play(speed_factor=5.0)
                await asyncio.sleep(0.05)
                await replayer.stop()

            self.loop.run_until_complete(run())

            # Verify both events were recorded and replayed chronologically
            self.assertEqual(len(replayed_events), 2)
            self.assertEqual(replayed_events[0][0], "status/battery")
            self.assertEqual(replayed_events[1][0], "status/gps")
            self.assertEqual(replayed_events[0][1]["value"], 80)
            self.assertEqual(replayed_events[1][1]["value"], "FIX")

    def test_pub_sub_extreme_concurrency_stress(self):
        """Stress-test asyncio pub/sub concurrency under heavy load."""
        received_all = []
        received_status = []
        received_status_temp = []
        received_errors = []
        
        async def cb_all(topic, payload):
            received_all.append((topic, payload))
            
        async def cb_status(topic, payload):
            received_status.append((topic, payload))
            
        async def cb_status_temp(topic, payload):
            received_status_temp.append((topic, payload))
            
        async def cb_errors(topic, payload):
            received_errors.append((topic, payload))
            # Simulate a subscriber raising an error to test fault tolerance
            if payload.get("fail"):
                raise ValueError("Simulated subscriber callback failure")

        self.bus.subscribe("*", cb_all)
        self.bus.subscribe("status/*", cb_status)
        self.bus.subscribe("status/*/temp", cb_status_temp)
        self.bus.subscribe("log/ERROR", cb_errors)

        num_publishers = 20
        messages_per_publisher = 40
        
        async def publisher(pub_id):
            for i in range(messages_per_publisher):
                # Send to different topics
                if i % 4 == 0:
                    await self.bus.publish("status/device/temp", {"pub_id": pub_id, "i": i, "val": 25.0 + i})
                elif i % 4 == 1:
                    await self.bus.publish("status/battery", {"pub_id": pub_id, "i": i, "val": i})
                elif i % 4 == 2:
                    # Occasional simulated failures on log/ERROR
                    should_fail = (i == 2)
                    await self.bus.publish("log/ERROR", {"pub_id": pub_id, "i": i, "fail": should_fail})
                else:
                    await self.bus.publish("other/topic", {"pub_id": pub_id, "i": i})
                # Yield control to let other coroutines run
                await asyncio.sleep(0.001)

        total_expected = num_publishers * messages_per_publisher
        expected_status = total_expected // 2
        expected_status_temp = total_expected // 4
        expected_errors = total_expected // 4

        async def run():
            # Start all publishers concurrently
            tasks = [asyncio.create_task(publisher(p)) for p in range(num_publishers)]
            await asyncio.gather(*tasks)
            # Wait for all background callback tasks to finish
            for _ in range(100):
                if (len(received_all) >= total_expected and 
                    len(received_status) >= expected_status and 
                    len(received_status_temp) >= expected_status_temp and 
                    len(received_errors) >= expected_errors):
                    break
                await asyncio.sleep(0.05)

        self.loop.run_until_complete(run())

        # Verify exact message counts
        self.assertEqual(len(received_all), total_expected)
        self.assertEqual(len(received_status), expected_status)
        self.assertEqual(len(received_status_temp), expected_status_temp)
        self.assertEqual(len(received_errors), expected_errors)

    def test_sqlite_wal_stress(self):
        """Stress-test SQLite WAL mode write throughput and concurrency under heavy pressure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "telemetry_stress.db")
            recorder = Recorder(db_path, self.bus)
            
            async def run():
                await recorder.start()
                
                num_messages = 1000
                start_time = time.time()
                
                # Concurrently publish 1000 messages onto the bus
                publish_tasks = []
                for i in range(num_messages):
                    payload = {"timestamp": time.time(), "seq": i, "data": "X" * 100}
                    publish_tasks.append(self.bus.publish("status/stress", payload))
                
                await asyncio.gather(*publish_tasks)
                
                # Stop recorder to flush the queue and close connection
                await recorder.stop()
                
                duration = time.time() - start_time
                throughput = num_messages / duration
                logging.info(f"SQLite WAL write throughput: {throughput:.2f} messages/sec (took {duration:.4f}s)")
                
                # Verify DB content
                import sqlite3
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM telemetry;")
                count = cursor.fetchone()[0]
                conn.close()
                
                self.assertEqual(count, num_messages)

            self.loop.run_until_complete(run())

    def test_replayer_clock_drift_precision(self):
        """Verify replayer's clock drift compensation precision over a larger playback cycle."""
        import sqlite3
        import json
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "telemetry_drift.db")
            
            # Manually populate database with a sequence of events with precise timestamps
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    topic TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
            """)
            
            num_events = 50
            interval = 0.02  # 20ms apart
            base_time = 1000.0  # arbitrary start timestamp
            
            for i in range(num_events):
                ts = base_time + (i * interval)
                payload = json.dumps({"timestamp": ts, "val": i})
                conn.execute(
                    "INSERT INTO telemetry (timestamp, topic, payload) VALUES (?, ?, ?);",
                    (ts, f"test/topic/{i}", payload)
                )
            conn.commit()
            conn.close()
            
            replayed_times = []
            
            async def on_event(topic, payload):
                replayed_times.append((payload["val"], time.time()))
                
            async def run():
                playback_bus = SpannerBus()
                playback_bus.subscribe("test/topic/*", on_event)
                
                replayer = Replayer(db_path, playback_bus)
                
                await replayer.play(speed_factor=1.0)
                
                # Wait for playback to complete (expected duration is num_events * interval = 1.0s)
                for _ in range(40):
                    if len(replayed_times) >= num_events:
                        break
                    await asyncio.sleep(0.05)
                    
                await replayer.stop()
                
            self.loop.run_until_complete(run())
            
            self.assertEqual(len(replayed_times), num_events)
            
            # Analyze time drift
            # The first event is replayed immediately
            first_val, first_time = replayed_times[0]
            self.assertEqual(first_val, 0)
            
            drifts = []
            for val, act_time in replayed_times:
                expected_offset = val * interval
                actual_offset = act_time - first_time
                drift = actual_offset - expected_offset
                drifts.append(abs(drift))
                
            max_drift = max(drifts)
            avg_drift = sum(drifts) / len(drifts)
            
            logging.info(f"Clock drift precision: Max drift = {max_drift*1000:.2f}ms, Avg drift = {avg_drift*1000:.2f}ms")
            
            # With drift compensation, the maximum drift should stay well within a very tight tolerance (e.g. < 50ms)
            self.assertLess(max_drift, 0.050)

    def test_atomic_config_write_failure_safety(self):
        """Test atomic configuration write safety during simulated I/O and disk-full failures."""
        from unittest.mock import patch
        import json
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.json")
            cfg = Config(config_path, self.bus)
            
            async def run():
                # Set initial valid configuration
                cfg.set("param1", "value1")
                cfg.set("param2", 42)
                await asyncio.sleep(0.1)
                
                # Verify it's on disk
                with open(config_path, "r") as f:
                    data = json.load(f)
                self.assertEqual(data["param1"], "value1")
                self.assertEqual(data["param2"], 42)
                
                # Simulate a write failure (e.g., Disk Full) when saving the file.
                original_open = open
                
                def faulty_open(file, mode='r', *args, **kwargs):
                    if isinstance(file, str) and file.endswith(".tmp") and 'w' in mode:
                        raise OSError("No space left on device (Simulated)")
                    return original_open(file, mode, *args, **kwargs)
                    
                with patch("builtins.open", side_effect=faulty_open):
                    # This set should fail to save to disk but should NOT crash,
                    # and must NOT corrupt the original config file.
                    cfg.set("param1", "corrupted_attempt")
                    await asyncio.sleep(0.1)
                    
                # Verify the file on disk remains completely intact and uncorrupted.
                with open(config_path, "r") as f:
                    disk_data = json.load(f)
                self.assertEqual(disk_data["param1"], "value1")
                self.assertEqual(disk_data["param2"], 42)
                
                # Ensure the temp file was cleaned up or not present
                temp_path = f"{config_path}.tmp"
                self.assertFalse(os.path.exists(temp_path))
                
                # If we re-load a Config instance, it should recover and have the old valid values from disk
                cfg_recovered = Config(config_path, self.bus)
                self.assertEqual(cfg_recovered.get("param1"), "value1")
                self.assertEqual(cfg_recovered.get("param2"), 42)

            self.loop.run_until_complete(run())

if __name__ == "__main__":
    unittest.main()
