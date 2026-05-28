import curses
import asyncio
import sqlite3
import json
import os
import sys
import time
from typing import Dict, Any

class CursesMonitor:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.running = True
        self.telemetry_data: Dict[str, Any] = {}
        self.logs = []
        self.max_logs = 100
        self.last_timestamp = 0.0
        # Supported display levels and current filter index
        self.levels_list = ["DEBUG", "INFO", "WARNING", "ERROR"]
        self.level_index = 1  # Default to INFO
        self.levels_map = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}

    def load_latest_data(self):
        """Reads latest values from SQLite telemetry database."""
        if not os.path.exists(self.db_path):
            return

        try:
            # Connect in read-only mode to prevent lock conflicts with the running system recorder
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            
            # 1. Fetch all latest states for status topics
            cursor.execute("""
                SELECT t1.topic, t1.payload, t1.timestamp
                FROM telemetry t1
                INNER JOIN (
                    SELECT topic, MAX(id) as max_id
                    FROM telemetry
                    WHERE topic LIKE 'status/%'
                    GROUP BY topic
                ) t2 ON t1.id = t2.max_id;
            """)
            for topic, payload, timestamp in cursor.fetchall():
                try:
                    self.telemetry_data[topic] = json.loads(payload)
                except Exception:
                    self.telemetry_data[topic] = payload

            # 2. Fetch new log items chronologically
            cursor.execute("""
                SELECT timestamp, topic, payload 
                FROM telemetry 
                WHERE topic LIKE 'log/%' AND timestamp > ?
                ORDER BY timestamp ASC;
            """, (self.last_timestamp,))
            
            new_rows = cursor.fetchall()
            if new_rows:
                for timestamp, topic, payload in new_rows:
                    try:
                        parsed_payload = json.loads(payload)
                        msg = parsed_payload.get("message", payload) if isinstance(parsed_payload, dict) else payload
                        level = topic.split("/")[-1]
                    except Exception:
                        msg = payload
                        level = "INFO"
                    self.logs.append((timestamp, level, msg))
                    self.last_timestamp = max(self.last_timestamp, timestamp)

                # Keep logs size bounded
                if len(self.logs) > self.max_logs:
                    self.logs = self.logs[-self.max_logs:]
            
            conn.close()
        except sqlite3.OperationalError:
            # DB might be temporarily locked or initializing, ignore gracefully
            pass
        except Exception as e:
            # Just ignore log/read issues to keep monitor loop resilient
            pass

    def draw_screen(self, stdscr):
        """Main redraw curses execution logic."""
        curses.curs_set(0)  # Hide cursor
        stdscr.nodelay(True)  # Non-blocking input reads
        curses.start_color()
        curses.use_default_colors()
        
        # Color pairs (Foreground, Background)
        curses.init_pair(1, curses.COLOR_CYAN, -1)     # Topic headers
        curses.init_pair(2, curses.COLOR_GREEN, -1)    # Highlights / OK
        curses.init_pair(3, curses.COLOR_YELLOW, -1)   # Warnings
        curses.init_pair(4, curses.COLOR_RED, -1)      # Errors
        curses.init_pair(5, curses.COLOR_MAGENTA, -1)  # Subtitles

        while self.running:
            self.load_latest_data()
            stdscr.clear()
            height, width = stdscr.getmaxyx()

            # Ensure minimal terminal size
            if height < 15 or width < 60:
                stdscr.addstr(0, 0, "Terminal window too small! Resize to at least 80x24.", curses.A_REVERSE)
                stdscr.refresh()
                time.sleep(0.5)
                continue

            # Title Bar
            title_text = " 🔧 BAG FULL OF SPANNERS (BFOS) - RUNTIME CONSOLE MONITOR 🚢 "
            stdscr.addstr(0, max(0, (width - len(title_text)) // 2), title_text, curses.A_REVERSE | curses.A_BOLD)
            
            # Left pane: Status & Telemetry
            col_width = min(width // 2 - 2, 50)
            stdscr.addstr(2, 2, "══ SYSTEM STATUS 🌐 ═══════════════════════════", curses.color_pair(1) | curses.A_BOLD)
            
            # Map parameters nicely
            monitor_keys = {
                "status/monitor/cpu/load_1m": ("CPU Load (1m)", "cpu_load"),
                "status/monitor/memory/used_percent": ("Memory Usage", "%"),
                "status/monitor/disk/used_percent": ("Disk Usage", "%"),
                "status/monitor/memory/free_bytes": ("Free Memory", "bytes"),
                "status/monitor/disk/free_bytes": ("Free Disk", "bytes")
            }

            row = 4
            for topic, (label, val_type) in monitor_keys.items():
                data = self.telemetry_data.get(topic)
                if data is not None:
                    val = data.get("value", data) if isinstance(data, dict) else data
                    if val_type == "cpu_load":
                        display_val = f"{val:.2f}"
                        color = curses.color_pair(3) if val > 2.0 else curses.color_pair(2)
                    elif val_type == "%":
                        display_val = f"{val:.1f}%"
                        color = curses.color_pair(3) if val > 80.0 else curses.color_pair(2)
                    elif val_type == "bytes":
                        display_val = f"{val / (1024**3):.2f} GB"
                        color = curses.color_pair(2)
                    else:
                        display_val = str(val)
                        color = curses.color_pair(2)
                else:
                    display_val = "N/A"
                    color = curses.A_DIM

                stdscr.addstr(row, 3, f"{label:<22}: ")
                stdscr.addstr(row, 25, f"{display_val:<15}", color | curses.A_BOLD)
                row += 1

            # Render generic status updates not in monitor list
            stdscr.addstr(row + 1, 2, "══ GENERAL TELEMETRY 📡 ════════════════════════", curses.color_pair(1) | curses.A_BOLD)
            row_gen = row + 3
            count_gen = 0
            for topic, data in sorted(self.telemetry_data.items()):
                if not topic.startswith("status/monitor/") and count_gen < 6:
                    val = data.get("value", data) if isinstance(data, dict) else data
                    label = topic.replace("status/", "")
                    stdscr.addstr(row_gen, 3, f"{label[:22]:<22}: ")
                    stdscr.addstr(row_gen, 25, f"{str(val)[:15]:<15}", curses.color_pair(5))
                    row_gen += 1
                    count_gen += 1

            # Right pane: Active Logs Console
            log_start_col = col_width + 4
            log_width = width - log_start_col - 4
            current_level_name = self.levels_list[self.level_index]
            stdscr.addstr(2, log_start_col, f"══ LOGS [{current_level_name}+] 📜 ═══════════════", curses.color_pair(1) | curses.A_BOLD)
            
            # Print latest logs
            log_row = 4
            max_log_rows = height - 8
            
            # Filter logs based on selected severity value
            current_threshold = self.levels_map.get(current_level_name, 20)
            filtered_logs = [
                (ts, lvl, msg) for ts, lvl, msg in self.logs 
                if self.levels_map.get(lvl, 20) >= current_threshold
            ]
            visible_logs = filtered_logs[-max_log_rows:] if len(filtered_logs) > max_log_rows else filtered_logs
            
            for timestamp, level, msg in reversed(visible_logs):
                if log_row < height - 4:
                    # Select color based on severity level
                    if level == "ERROR":
                        col = curses.color_pair(4) | curses.A_BOLD
                    elif level == "WARNING":
                        col = curses.color_pair(3) | curses.A_BOLD
                    elif level == "DEBUG":
                        col = curses.A_DIM
                    else:
                        col = curses.color_pair(2)
                        
                    time_str = time.strftime("%H:%M:%S", time.localtime(timestamp))
                    log_line = f"[{time_str}] [{level[:4]}] {msg}"
                    # Truncate to fit terminal screen nicely
                    stdscr.addstr(log_row, log_start_col + 1, log_line[:log_width], col)
                    log_row += 1

            # Status Footer Help Bar
            stdscr.addstr(height - 2, 2, "Press 'l' to toggle Log Levels (DEBUG/INFO/WARN/ERROR) | 'q' to exit", curses.A_DIM)
            stdscr.refresh()

            # Input check
            try:
                ch = stdscr.getch()
                if ch == ord('q') or ch == ord('Q'):
                    self.running = False
                elif ch == ord('l') or ch == ord('L'):
                    self.level_index = (self.level_index + 1) % len(self.levels_list)
            except Exception:
                pass

            time.sleep(0.3)

def main():
    db_path = "system_monitor.db"
    if len(sys.argv) > 1:
        db_path = sys.argv[1]

    monitor = CursesMonitor(db_path)
    
    # Initialize and wrap with standard curses terminal handler
    try:
        curses.wrapper(monitor.draw_screen)
    except KeyboardInterrupt:
        pass
    print("\nCurses Monitor exited.")

if __name__ == "__main__":
    main()
