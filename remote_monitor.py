"""Read-only, token-protected LAN log dashboard for AutoAgent.

Run: .venv/bin/python remote_monitor.py
Open the printed link on a device on the same trusted Wi-Fi network.
Do not port-forward this HTTP server or run it on untrusted networks.
"""

import argparse
import json
import re
import secrets
import socket
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
TAIL_BYTES = 90_000

HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="referrer" content="no-referrer" />
<title>AutoAgent • Live Monitor</title>
<style>
  :root { color-scheme: dark; font-family: -apple-system, BlinkMacSystemFont, Inter, sans-serif; }
  body { margin:0; background:#10131a; color:#e7ecf4; }
  .shell { max-width:860px; padding:22px 18px; margin:0 auto; }
  header { display:flex; justify-content:space-between; align-items:center; gap:15px; }
  h1 { font-size:20px; margin:0; }
  .status { font-size:12px; padding:7px 11px; background:#263248; border-radius:30px; color:#b4d7ff; }
  .panel { background:#1c2330; border:1px solid #303849; border-radius:14px; padding:16px; margin:16px 0; }
  .muted { color:#9da8ba; font-size:13px; line-height:1.5; }
  .heading { font-size:12px; color:#9da8ba; letter-spacing:.08em; margin-bottom:8px; }
  .step { font-size:16px; line-height:1.5; overflow-wrap:anywhere; }
  pre { margin:0; padding:14px; background:#0c111a; border-radius:10px; color:#b7dfc0;
        font:12px/1.55 ui-monospace, Menlo, Consolas, monospace; white-space:pre-wrap;
        overflow-wrap:anywhere; max-height:64vh; overflow:auto; }
  footer { font-size:12px; color:#909aab; margin-top:15px; }
</style>
</head>
<body><main class="shell">
  <header><h1>◉ AutoAgent Monitor</h1><span id="state" class="status">Connecting...</span></header>
  <section class="panel"><div class="heading">CURRENT ACTIVITY</div>
     <div id="step" class="step">Waiting for log data...</div>
     <p id="details" class="muted">Updates every second • Read-only</p>
  </section>
  <section class="panel"><div class="heading">RECENT LOGS</div>
     <pre id="logs">Waiting for AutoAgent...</pre>
  </section>
  <footer>Only available while the AutoAgent server is running on your Mac.</footer>
</main>
<script>
const token = @TOKEN@;
let lastLog = '';
async function refresh() {
  try {
    const response = await fetch('/api?token=' + encodeURIComponent(token), {cache:'no-store'});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const data = await response.json();
    document.getElementById('state').textContent = data.status;
    document.getElementById('step').textContent = data.step;
    document.getElementById('details').textContent = data.details;
    const elem = document.getElementById('logs');
    if (data.logs !== lastLog) {
      const atBottom = elem.scrollTop + elem.clientHeight >= elem.scrollHeight - 50;
      elem.textContent = data.logs || 'No logs yet';
      if (atBottom) elem.scrollTop = elem.scrollHeight;
      lastLog = data.logs;
    }
  } catch (error) {
    document.getElementById('state').textContent = 'Disconnected';
    document.getElementById('details').textContent = error.message;
  }
}
refresh(); setInterval(refresh, 1000);
</script>
</body></html>'''


def local_ip() -> str:
    """Best-effort LAN IP discovery, without sending application data."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))
            return sock.getsockname()[0]
    except OSError:
        return "YOUR_MAC_IP"


def latest_log() -> dict:
    files = list(LOG_DIR.glob("browser_*.log"))
    if not files:
        return {
            "status": "Waiting", "step": "Waiting for an agent run...",
            "details": "Start AutoAgent from TRAE to see activity.", "logs": ""
        }

    file = max(files, key=lambda path: path.stat().st_mtime_ns)
    stat = file.stat()
    with file.open("rb") as handle:
        handle.seek(max(0, stat.st_size - TAIL_BYTES))
        raw = handle.read()
    content = raw.decode("utf-8", errors="replace")
    if stat.st_size > TAIL_BYTES:
        content = content.split("\n", 1)[-1]
    lines = content.splitlines()

    status, step = "Working", "Agent started; awaiting next action..."
    for line in lines:
        if "AUTOAGENT_STATUS | FINISHED" in line:
            status, step = "Finished", "Agent finished the task"
        elif "AUTOAGENT_STATUS | INTERRUPTED" in line:
            status, step = "Interrupted", "Agent was interrupted"
        elif "AUTOAGENT_STATUS | ERROR" in line:
            status, step = "Error", "Agent encountered an error — see logs"
        else:
            matched = re.search(r"\bstep\s+\d+\s*:\s*(.*)", line, re.IGNORECASE)
            if matched and status == "Working":
                step = "Step: " + matched.group(1).strip()[:300]

    age = max(0, (datetime.now().timestamp() - stat.st_mtime))
    detail = f"Latest log: {age:.0f}s ago • {file.name}"
    if age > 10 and status == "Working":
        detail += " • May be waiting for Gemini or a page to load"
    return {"status": status, "step": step, "details": detail,
            "logs": "\n".join(lines[-160:])}


def serve(host: str, port: int) -> None:
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *args):
            # Avoid printing the token from the URL into terminal logs.
            pass

        def send_payload(self, code: int, content_type: str, data: bytes):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urlsplit(self.path)
            supplied = parse_qs(parsed.query).get("token", [""])[0]
            if not secrets.compare_digest(supplied, token):
                self.send_payload(403, "text/plain; charset=utf-8", b"Invalid dashboard token")
                return
            if parsed.path == "/":
                html = HTML.replace("@TOKEN@", json.dumps(token)).encode("utf-8")
                self.send_payload(200, "text/html; charset=utf-8", html)
            elif parsed.path == "/api":
                body = json.dumps(latest_log()).encode("utf-8")
                self.send_payload(200, "application/json; charset=utf-8", body)
            else:
                self.send_payload(404, "text/plain; charset=utf-8", b"Not found")

    with ThreadingHTTPServer((host, port), Handler) as server:
        ip = local_ip()
        print("\n=== AutoAgent remote dashboard ===", flush=True)
        print(f"On this Mac: http://127.0.0.1:{port}/?token={token}", flush=True)
        if host == "0.0.0.0":
            print(f"On your phone (same Wi-Fi): http://{ip}:{port}/?token={token}", flush=True)
            print("PRIVATE: Keep this link confidential; never port-forward this port.", flush=True)
        print("==================================\n", flush=True)
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lan", action="store_true", help="Allow phones on the same LAN")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        serve("0.0.0.0" if args.lan else "127.0.0.1", args.port)
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        raise SystemExit(f"Could not start dashboard: {exc}")
