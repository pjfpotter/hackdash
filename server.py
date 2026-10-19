#!/usr/bin/env python3
"""Hackdash server. Pure stdlib; run with:  python3 server.py

  /                 the TV dashboard (open with ?key=...)
  /remote           phone remote (open once with ?key=..., it remembers)
  /api/state        JSON state            (key required)
  /api/events       live updates via SSE  (key required)
  /api/cmd          POST {"text": "..."}  (key required)
  /mcp/<token>      MCP endpoint for Claude (the token is the secret)
"""
import hmac
import json
import os
import secrets
import shlex
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import commands
import mcp
from store import Store

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
DATA = os.environ.get("HACKDASH_DATA", os.path.join(ROOT, "data"))
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
         ".woff2": "font/woff2", ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml"}


def load_config():
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, "config.json")
    cfg = {}
    if os.path.exists(path):
        with open(path) as f:
            cfg = json.load(f)
    changed = False
    for k, make in (("key", lambda: secrets.token_urlsafe(12)), ("mcp_token", lambda: secrets.token_urlsafe(24)),
                    ("port", lambda: 8080)):
        if k not in cfg:
            cfg[k] = make()
            changed = True
    if changed:
        with open(path, "w") as f:
            json.dump(cfg, f, indent=1)
        os.chmod(path, 0o600)
    return cfg


CFG = load_config()
BUILD = str(int(time.time()))  # changes on every restart; the TV reloads when it sees a new one
_fresh = not os.path.exists(os.path.join(DATA, "state.json"))
STORE = Store(os.path.join(DATA, "state.json"))
if _fresh:
    # First boot: the dash's first job is tracking its own finish line.
    STORE.upsert_project("Hackdash", status="active", next_step="get it on the TV",
                         add_milestones=["server runs on the Pi", "on the TV", "Claude connector live", "phone remote works"])
    STORE.complete("server runs on the Pi")
    STORE.add_todos([{"text": "Add my real projects (max 3 active)"}, {"text": "Set this week's goals"}])


class Handler(BaseHTTPRequestHandler):
    server_version = "hackdash/0.1"

    def log_message(self, fmt, *args):
        if "/api/events" not in self.path:
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # ---- helpers ----
    def _send(self, code, body=b"", ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _authed(self, q):
        given = self.headers.get("X-Dash-Key") or (q.get("key") or [""])[0]
        return hmac.compare_digest(given.encode(), CFG["key"].encode())

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            raise ValueError("body too large")
        return self.rfile.read(n)

    def _mcp_path_ok(self, path):
        token = path[len("/mcp/"):].strip("/")
        return path.startswith("/mcp/") and hmac.compare_digest(token.encode(), CFG["mcp_token"].encode())

    # ---- routes ----
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            return self._static("index.html")
        if u.path in ("/remote", "/remote/"):
            return self._static("remote.html")
        if u.path.startswith("/fonts/"):
            return self._static(u.path.lstrip("/"))
        if u.path == "/healthz":
            return self._send(200, {"ok": True, "rev": STORE.rev})
        if u.path.startswith("/mcp/"):
            return self._send(405, {"error": "use POST"}, extra={"Allow": "POST"})
        if not self._authed(q):
            return self._send(401, {"error": "missing or wrong key"})
        if u.path == "/api/state":
            return self._send(200, dict(STORE.snapshot(), build=BUILD))
        if u.path == "/api/events":
            return self._sse()
        self._send(404, {"error": "not found"})

    do_HEAD = do_GET

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path.startswith("/mcp/"):
                if not self._mcp_path_ok(u.path):
                    return self._send(404, {"error": "not found"})
                res = mcp.handle_body(STORE, self._body())
                return self._send(202) if res is None else self._send(200, res)
            if not self._authed(q):
                return self._send(401, {"error": "missing or wrong key"})
            if u.path == "/api/cmd":
                data = json.loads(self._body() or b"{}")
                return self._send(200, {"replies": commands.run(STORE, data.get("text", ""))})
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, {"error": str(e)})
        self._send(404, {"error": "not found"})

    def do_DELETE(self):
        self._send(405, {"error": "sessions not used"})

    def _static(self, rel):
        path = os.path.realpath(os.path.join(STATIC, rel))
        if not path.startswith(STATIC + os.sep) or not os.path.isfile(path):
            return self._send(404, {"error": "not found"})
        with open(path, "rb") as f:
            body = f.read()
        if path.endswith(".html") and not body.lstrip()[:9].lower().startswith(b"<!doctype"):
            body = (b'<!doctype html><html lang="en"><head><meta charset="utf-8">'
                    b'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
                    b'</head><body>' + body + b'</body></html>')
        self._send(200, body, TYPES.get(os.path.splitext(path)[1], "application/octet-stream"))

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        seq = STORE.seq
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            while True:
                seq, evs = STORE.wait_events(seq, timeout=15)
                if not evs:
                    self.wfile.write(b": ping\n\n")
                for ev in evs:
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


def finance_poller():
    """Optional: run HACKDASH_FINANCE_CMD every N minutes. It must print a number or
    JSON like {"balance": 1234.5, "accounts": [{"name": "Main", "balance": 1000}]}.
    Configured only via environment, never over the network."""
    cmd = os.environ.get("HACKDASH_FINANCE_CMD")
    if not cmd:
        return
    every = int(os.environ.get("HACKDASH_FINANCE_EVERY", "30")) * 60
    while True:
        try:
            out = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=60).stdout.strip()
            data = json.loads(out) if out.startswith("{") else {"balance": float(out)}
            STORE.set_finance(balance=data.get("balance"), accounts=data.get("accounts"), source="auto")
        except Exception as e:  # keep polling whatever the bank script does
            sys.stderr.write(f"finance poll failed: {e}\n")
        time.sleep(every)


def main():
    port = int(os.environ.get("HACKDASH_PORT", CFG["port"]))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=finance_poller, daemon=True).start()
    print(f"""
  HACKDASH online on port {port}
  TV:     http://localhost:{port}/?key={CFG['key']}
  Phone:  http://<pi-address>:{port}/remote?key={CFG['key']}
  Claude: https://<your-public-url>/mcp/{CFG['mcp_token']}
""", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
