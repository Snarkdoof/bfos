import curses
import asyncio
import sqlite3
import json
import os
import sys
import time
from typing import Dict, Any, List

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

        # Collapsible tree configurations
        self.configs: Dict[str, Any] = {}
        self.collapsed_configs = set()
        self.config_selected_index = 0
        self.confirming_clear = False

        # Seed local configurations initially from standard files if present
        for cfg_file in ["system_monitor_config.json", "config.json"]:
            if os.path.exists(cfg_file):
                try:
                    with open(cfg_file, "r") as f:
                        data = json.load(f)
                        for k, v in data.items():
                            self.configs[k] = v
                except Exception:
                    pass

    def get_config_tree_lines(self) -> List[Dict[str, Any]]:
        """Parses flat configurations and builds a sorted, collapsible tree structure."""
        tree = {}
        for key, val in self.configs.items():
            parts = key.split("/")
            curr = tree
            for part in parts[:-1]:
                if part not in curr:
                    curr[part] = {}
                curr = curr[part]
            curr[parts[-1]] = val

        lines = []
        def traverse(node: dict, current_path_parts: list, depth: int):
            for name in sorted(node.keys()):
                val = node[name]
                path_parts = current_path_parts + [name]
                full_path = "/".join(path_parts)
                if isinstance(val, dict):
                    collapsed = full_path in self.collapsed_configs
                    lines.append({
                        "full_path": full_path,
                        "name": name,
                        "is_namespace": True,
                        "depth": depth,
                        "collapsed": collapsed
                    })
                    if not collapsed:
                        traverse(val, path_parts, depth + 1)
                else:
                    lines.append({
                        "full_path": full_path,
                        "name": name,
                        "is_namespace": False,
                        "depth": depth,
                        "value": val
                    })
        traverse(tree, [], 0)
        return lines

    def clear_database(self) -> bool:
        """Atomically clear all telemetry and log rows from the database."""
        if not os.path.exists(self.db_path):
            return False
        try:
            # Use read-write connection to clear and vacuum database
            conn = sqlite3.connect(self.db_path)
            conn.execute("DELETE FROM telemetry;")
            conn.execute("VACUUM;")
            conn.commit()
            conn.close()
            # Clear local CLI state caches instantly
            self.telemetry_data.clear()
            self.logs.clear()
            self.last_timestamp = 0.0
            return True
        except Exception:
            return False

    def load_latest_data(self):
        """Reads latest values from SQLite telemetry database."""
        if not os.path.exists(self.db_path):
            return

        try:
            # Connect in read-only mode to prevent lock conflicts
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            
            # 1. Fetch latest states for status & config topics
            cursor.execute("""
                SELECT t1.topic, t1.payload, t1.timestamp
                FROM telemetry t1
                INNER JOIN (
                    SELECT topic, MAX(id) as max_id
                    FROM telemetry
                    WHERE topic LIKE 'status/%' OR topic LIKE 'config/changed/%'
                    GROUP BY topic
                ) t2 ON t1.id = t2.max_id;
            """)
            for topic, payload, timestamp in cursor.fetchall():
                try:
                    parsed_payload = json.loads(payload)
                except Exception:
                    parsed_payload = payload

                if topic.startswith("config/changed/"):
                    config_key = topic[15:]
                    if isinstance(parsed_payload, dict) and "new_value" in parsed_payload:
                        self.configs[config_key] = parsed_payload["new_value"]
                    else:
                        self.configs[config_key] = parsed_payload
                elif topic.startswith("status/"):
                    self.telemetry_data[topic] = parsed_payload

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
            # DB might be temporarily locked, ignore gracefully
            pass
        except Exception:
            pass

    def draw_screen(self, stdscr):
        """Main redraw curses execution logic."""
        curses.curs_set(0)  # Hide cursor
        stdscr.nodelay(True)  # Non-blocking input reads
        stdscr.keypad(True)  # Enable keypad support for Arrows
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

            # Render Configurations Collapsible Tree
            config_start_row = row + 1
            stdscr.addstr(config_start_row, 2, "══ CONFIGURATIONS ⚙️ ═════════════════════════", curses.color_pair(1) | curses.A_BOLD)
            
            tree_lines = self.get_config_tree_lines()
            
            # Clamp selection bounds
            if self.config_selected_index >= len(tree_lines):
                self.config_selected_index = max(0, len(tree_lines) - 1)
                
            config_row = config_start_row + 2
            max_config_rows = height - config_row - 4
            
            for i, line in enumerate(tree_lines[:max_config_rows]):
                indent = "  " * line["depth"]
                is_selected = (i == self.config_selected_index)
                style = curses.A_REVERSE if is_selected else curses.A_NORMAL
                
                if line["is_namespace"]:
                    prefix = "▶ " if line["collapsed"] else "▼ "
                    text = f"{indent}{prefix}{line['name']}"
                    stdscr.addstr(config_row, 3, f"{text:<35}", style | curses.color_pair(5) | curses.A_BOLD)
                else:
                    text = f"{indent}• {line['name']}: {line['value']}"
                    stdscr.addstr(config_row, 3, f"{text:<35}", style | curses.color_pair(2))
                config_row += 1

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
                    stdscr.addstr(log_row, log_start_col + 1, log_line[:log_width], col)
                    log_row += 1

            # Help Bar & Prompts
            if self.confirming_clear:
                prompt_str = " ⚠️  CONFIRM CLEAR DATABASE? All recorded telemetry rows will be deleted. (y/N): "
                # Render in red reversed alert style
                stdscr.addstr(height - 2, 2, f"{prompt_str:<76}", curses.color_pair(4) | curses.A_BOLD | curses.A_REVERSE)
            else:
                stdscr.addstr(height - 2, 2, "Arrows/WS: Select | Space/Enter: Expand | L: Level | C: Clear DB | Q: Exit", curses.A_DIM)
                
            stdscr.refresh()

            # Input check
            try:
                ch = stdscr.getch()
                if self.confirming_clear:
                    if ch in [ord('y'), ord('Y')]:
                        self.clear_database()
                        self.confirming_clear = False
                    elif ch in [ord('n'), ord('N'), 27]: # Esc or N/n
                        self.confirming_clear = False
                else:
                    if ch == ord('q') or ch == ord('Q'):
                        self.running = False
                    elif ch == ord('l') or ch == ord('L'):
                        self.level_index = (self.level_index + 1) % len(self.levels_list)
                    elif ch == ord('c') or ch == ord('C'):
                        self.confirming_clear = True
                    elif ch in [curses.KEY_UP, ord('w'), ord('W')]:
                        self.config_selected_index = max(0, self.config_selected_index - 1)
                    elif ch in [curses.KEY_DOWN, ord('s'), ord('S')]:
                        self.config_selected_index = min(len(tree_lines) - 1, self.config_selected_index + 1)
                    elif ch in [10, 13, 32]: # Enter/Space
                        if tree_lines and 0 <= self.config_selected_index < len(tree_lines):
                            node = tree_lines[self.config_selected_index]
                            if node["is_namespace"]:
                                path = node["full_path"]
                                if path in self.collapsed_configs:
                                    self.collapsed_configs.remove(path)
                                else:
                                    self.collapsed_configs.add(path)
            except Exception:
                pass

            time.sleep(0.3)

def main():
    db_path = "system_monitor.db"
    if len(sys.argv) > 1:
        db_path = sys.argv[1]

    monitor = CursesMonitor(db_path)
    
    try:
        curses.wrapper(monitor.draw_screen)
    except KeyboardInterrupt:
        pass
    print("\nCurses Monitor exited.")

if __name__ == "__main__":
    main()
