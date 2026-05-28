import http.server
import socketserver
import json
import sqlite3
import os
import sys
import threading
import urllib.parse
from typing import Optional

PORT = 8080
DB_PATH = "system_monitor.db"

class SpannerHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """
    Lightweight cross-platform pure Python HTTP Server.
    Provides directory-free static files and a simple REST JSON API 
    to query status records and real-time logs from SQLite telemetry.
    """
    def do_GET(self):
        # Parse route path
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/api/status":
            self.send_json(self.get_latest_status())
        elif path == "/api/logs":
            query_params = urllib.parse.parse_qs(parsed_url.query)
            since = float(query_params.get("since", [0.0])[0])
            self.send_json(self.get_latest_logs(since))
        elif path == "/" or path == "/index.html":
            self.send_html_dashboard()
        else:
            self.send_error(404, "File Not Found")

    def send_json(self, data: dict):
        """Helper to serialize and dispatch JSON payloads."""
        try:
            content = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Internal Server Error: {e}")

    def get_latest_status(self) -> dict:
        """Fetches the latest status configurations from SQLite."""
        data = {}
        if not os.path.exists(DB_PATH):
            return {"error": "Database not initialized. Please start system_monitor.py first."}

        try:
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
            cursor = conn.cursor()
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
                    data[topic] = {
                        "payload": json.loads(payload),
                        "timestamp": timestamp
                    }
                except Exception:
                    data[topic] = {
                        "payload": payload,
                        "timestamp": timestamp
                    }
            conn.close()
        except Exception as e:
            data = {"error": str(e)}
        return data

    def get_latest_logs(self, since: float) -> list:
        """Fetches newly written logs since a given timestamp."""
        logs = []
        if not os.path.exists(DB_PATH):
            return logs

        try:
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT timestamp, topic, payload 
                FROM telemetry 
                WHERE topic LIKE 'log/%' AND timestamp > ?
                ORDER BY timestamp ASC
                LIMIT 100;
            """, (since,))
            for timestamp, topic, payload in cursor.fetchall():
                try:
                    parsed_payload = json.loads(payload)
                    msg = parsed_payload.get("message", payload) if isinstance(parsed_payload, dict) else payload
                except Exception:
                    msg = payload
                level = topic.split("/")[-1]
                logs.append({
                    "timestamp": timestamp,
                    "level": level,
                    "message": msg
                })
            conn.close()
        except Exception:
            pass
        return logs

    def send_html_dashboard(self):
        """Serves a beautifully crafted, responsive dashboard with Glassmorphism styles and automatic REST updates."""
        html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BFOS Telemetry Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: rgba(20, 28, 45, 0.45);
            --border-color: rgba(255, 255, 255, 0.08);
            --primary: #00f2fe;
            --secondary: #4facfe;
            --success: #00e676;
            --warning: #ffd600;
            --danger: #ff1744;
            --text: #f1f5f9;
            --text-dim: #94a3b8;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Outfit', sans-serif;
            background: radial-gradient(circle at 50% 0%, #1e293b 0%, var(--bg-color) 70%);
            color: var(--text);
            min-height: 100vh;
            padding: 2rem;
            display: flex;
            flex-direction: column;
            gap: 2rem;
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 1.5rem;
        }

        .title-area h1 {
            font-size: 2.2rem;
            font-weight: 700;
            background: linear-gradient(135deg, var(--primary) 0%, var(--secondary) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .title-area p {
            color: var(--text-dim);
            font-size: 0.95rem;
            margin-top: 0.25rem;
        }

        .status-badge {
            background: rgba(0, 230, 118, 0.15);
            border: 1px solid var(--success);
            color: var(--success);
            padding: 0.5rem 1rem;
            border-radius: 50px;
            font-size: 0.85rem;
            font-weight: 600;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            box-shadow: 0 0 15px rgba(0, 230, 118, 0.2);
            animation: pulse 2s infinite alternate;
        }

        @keyframes pulse {
            0% { box-shadow: 0 0 5px rgba(0, 230, 118, 0.2); }
            100% { box-shadow: 0 0 20px rgba(0, 230, 118, 0.5); }
        }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 2rem;
        }

        .card {
            background: var(--card-bg);
            backdrop-filter: blur(16px);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            padding: 1.75rem;
            display: flex;
            flex-direction: column;
            gap: 1.25rem;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }

        .card:hover {
            border-color: rgba(0, 242, 254, 0.3);
            box-shadow: 0 10px 30px rgba(0, 242, 254, 0.05);
            transform: translateY(-4px);
        }

        .card-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .card-title {
            font-size: 1.2rem;
            font-weight: 600;
            color: var(--text);
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .metric-value {
            font-size: 2.5rem;
            font-weight: 700;
            color: var(--primary);
            text-shadow: 0 0 20px rgba(0, 242, 254, 0.2);
        }

        .progress-bar-container {
            width: 100%;
            height: 8px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 100px;
            overflow: hidden;
        }

        .progress-bar {
            height: 100%;
            width: 0%;
            background: linear-gradient(90deg, var(--primary), var(--secondary));
            border-radius: 100px;
            transition: width 0.8s ease-in-out;
        }

        .stats-list {
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            list-style: none;
        }

        .stats-item {
            display: flex;
            justify-content: space-between;
            font-size: 0.95rem;
        }

        .stats-label {
            color: var(--text-dim);
        }

        .stats-val {
            font-weight: 600;
        }

        /* Console Logs Area */
        .console-container {
            grid-column: 1 / -1;
            background: rgba(10, 14, 23, 0.85);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            gap: 1rem;
            height: 350px;
        }

        .console-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .console-terminal {
            flex-grow: 1;
            background: #05070c;
            border-radius: 12px;
            padding: 1.25rem;
            font-family: 'Courier New', monospace;
            font-size: 0.9rem;
            overflow-y: auto;
            display: flex;
            flex-direction: column-reverse; /* New logs added at bottom, visible on scroll */
            gap: 0.5rem;
            border: 1px solid rgba(255,255,255,0.03);
        }

        .log-entry {
            display: flex;
            gap: 1rem;
            line-height: 1.4;
        }

        .log-time {
            color: var(--text-dim);
            min-width: 80px;
        }

        .log-level {
            font-weight: 700;
            min-width: 60px;
            text-transform: uppercase;
        }

        .log-level.INFO { color: var(--success); }
        .log-level.WARNING { color: var(--warning); }
        .log-level.ERROR { color: var(--danger); }

        .log-msg {
            color: #e2e8f0;
            word-break: break-all;
        }
    </style>
</head>
<body>

    <header>
        <div class="title-area">
            <h1>🔧 Bag Full of Spanners (BFOS)</h1>
            <p>Real-Time Embedded Telemetry & Bus Monitor Dashboard</p>
        </div>
        <div class="status-badge">Live Feed</div>
    </header>

    <div class="grid">
        <!-- CPU Card -->
        <div class="card">
            <div class="card-header">
                <div class="card-title">💻 Processor Load</div>
            </div>
            <div class="metric-value" id="cpu-metric">0.00</div>
            <div class="progress-bar-container">
                <div class="progress-bar" id="cpu-bar"></div>
            </div>
            <ul class="stats-list">
                <li class="stats-item">
                    <span class="stats-label">Load Average (5m)</span>
                    <span class="stats-val" id="cpu-5m">0.00</span>
                </li>
                <li class="stats-item">
                    <span class="stats-label">Load Average (15m)</span>
                    <span class="stats-val" id="cpu-15m">0.00</span>
                </li>
            </ul>
        </div>

        <!-- Memory Card -->
        <div class="card">
            <div class="card-header">
                <div class="card-title">🧠 Memory Usage</div>
            </div>
            <div class="metric-value" id="mem-metric">0.0%</div>
            <div class="progress-bar-container">
                <div class="progress-bar" id="mem-bar"></div>
            </div>
            <ul class="stats-list">
                <li class="stats-item">
                    <span class="stats-label">Free Physical Space</span>
                    <span class="stats-val" id="mem-free">0.00 GB</span>
                </li>
                <li class="stats-item">
                    <span class="stats-label">Total Installed Memory</span>
                    <span class="stats-val" id="mem-total">0.00 GB</span>
                </li>
            </ul>
        </div>

        <!-- Disk Storage Card -->
        <div class="card">
            <div class="card-header">
                <div class="card-title">💾 Disk Storage</div>
            </div>
            <div class="metric-value" id="disk-metric">0.0%</div>
            <div class="progress-bar-container">
                <div class="progress-bar" id="disk-bar"></div>
            </div>
            <ul class="stats-list">
                <li class="stats-item">
                    <span class="stats-label">Free Storage Space</span>
                    <span class="stats-val" id="disk-free">0.00 GB</span>
                </li>
                <li class="stats-item">
                    <span class="stats-label">Total Disk Capacity</span>
                    <span class="stats-val" id="disk-total">0.00 GB</span>
                </li>
            </ul>
        </div>

        <!-- Console Logging Terminal Container -->
        <div class="console-container">
            <div class="console-header">
                <div class="card-title">📜 Console Active Logging</div>
            </div>
            <div class="console-terminal" id="terminal">
                <!-- Live logs will append dynamically -->
            </div>
        </div>
    </div>

    <script>
        let lastLogTimestamp = 0.0;
        const terminal = document.getElementById("terminal");

        function formatBytes(bytes) {
            if (!bytes) return "0.00 GB";
            return (bytes / (1024**3)).toFixed(2) + " GB";
        }

        async function updateStatus() {
            try {
                const res = await fetch("/api/status");
                const data = await res.json();
                
                if (data.error) return;

                // 1. Update CPU
                const cpu1m = data["status/monitor/cpu/load_1m"]?.payload?.value || 0.0;
                const cpu5m = data["status/monitor/cpu/load_5m"]?.payload?.value || 0.0;
                const cpu15m = data["status/monitor/cpu/load_15m"]?.payload?.value || 0.0;
                document.getElementById("cpu-metric").innerText = cpu1m.toFixed(2);
                document.getElementById("cpu-5m").innerText = cpu5m.toFixed(2);
                document.getElementById("cpu-15m").innerText = cpu15m.toFixed(2);
                // CPU bar scale (arbitrary 0-4 load limit scale to 100%)
                const cpuBarPct = Math.min((cpu1m / 4.0) * 100, 100);
                document.getElementById("cpu-bar").style.width = `${cpuBarPct}%`;

                // 2. Update Memory
                const memPct = data["status/monitor/memory/used_percent"]?.payload?.value || 0.0;
                const memFree = data["status/monitor/memory/free_bytes"]?.payload?.value || 0;
                const memTotal = data["status/monitor/memory/total_bytes"]?.payload?.value || 0;
                document.getElementById("mem-metric").innerText = `${memPct.toFixed(1)}%`;
                document.getElementById("mem-free").innerText = formatBytes(memFree);
                document.getElementById("mem-total").innerText = formatBytes(memTotal);
                document.getElementById("mem-bar").style.width = `${memPct}%`;

                // 3. Update Disk
                const diskPct = data["status/monitor/disk/used_percent"]?.payload?.value || 0.0;
                const diskFree = data["status/monitor/disk/free_bytes"]?.payload?.value || 0;
                const diskTotal = data["status/monitor/disk/total_bytes"]?.payload?.value || 0;
                document.getElementById("disk-metric").innerText = `${diskPct.toFixed(1)}%`;
                document.getElementById("disk-free").innerText = formatBytes(diskFree);
                document.getElementById("disk-total").innerText = formatBytes(diskTotal);
                document.getElementById("disk-bar").style.width = `${diskPct}%`;

            } catch (err) {
                console.error("Failed fetching live status: ", err);
            }
        }

        async function updateLogs() {
            try {
                const res = await fetch(`/api/logs?since=${lastLogTimestamp}`);
                const logs = await res.json();
                
                logs.forEach(log => {
                    lastLogTimestamp = Math.max(lastLogTimestamp, log.timestamp);
                    
                    const timeStr = new Date(log.timestamp * 1000).toLocaleTimeString();
                    const entry = document.createElement("div");
                    entry.className = "log-entry";
                    entry.innerHTML = `
                        <span class="log-time">[${timeStr}]</span>
                        <span class="log-level ${log.level}">${log.level}</span>
                        <span class="log-msg">${log.message}</span>
                    `;
                    // Inserts logs chronologically (reversed container reads bottom-up)
                    terminal.insertBefore(entry, terminal.firstChild);
                });

                // Bound terminal logs size to avoid memory leakage
                while(terminal.children.length > 100) {
                    terminal.removeChild(terminal.lastChild);
                }

            } catch (err) {
                console.error("Failed loading logs: ", err);
            }
        }

        // Loop intervals
        setInterval(updateStatus, 1000);
        setInterval(updateLogs, 1000);

        // Initial launch
        updateStatus();
        updateLogs();
    </script>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(html_content)))
        self.end_headers()
        self.wfile.write(html_content.encode("utf-8"))

class ThreadedHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Multiple threads allow concurrent long-polling or non-blocking connections."""
    allow_reuse_address = True

def main():
    global DB_PATH
    if len(sys.argv) > 1:
        DB_PATH = sys.argv[1]

    # Create server instance bound to port 8080
    server = ThreadedHTTPServer(("", PORT), SpannerHTTPHandler)
    print(f"\n🖥️  BFOS Premium Web Dashboard started at: http://localhost:{PORT}")
    print(f"📂 Accessing SQLite database: {DB_PATH}")
    print("Press Ctrl+C to terminate the web server.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nWeb server shutting down.")
        server.shutdown()

if __name__ == "__main__":
    main()
