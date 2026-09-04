"""Localhost HTTP receiver for hook and statusline payloads.

POST /event   -> queue ("event", payload)
POST /status  -> queue ("status", payload)
GET  /health  -> 200

Runs in a daemon thread; the main loop drains the queue.
"""

from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 47391


class _Handler(BaseHTTPRequestHandler):
    q: queue.Queue  # set by start()

    def do_POST(self) -> None:  # noqa: N802
        kind = {"/event": "event", "/status": "status"}.get(self.path)
        if kind is None:
            self.send_response(404)
            self.end_headers()
            return
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        try:
            self.q.put_nowait((kind, json.loads(raw)))
        except Exception:
            pass
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()

    def log_message(self, *args) -> None:  # silence default stderr logging
        pass


def start(q: queue.Queue, host: str = HOST, port: int = PORT) -> ThreadingHTTPServer:
    _Handler.q = q
    srv = ThreadingHTTPServer((host, port), _Handler)
    threading.Thread(target=srv.serve_forever, name="hook-server", daemon=True).start()
    return srv
