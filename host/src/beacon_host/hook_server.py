"""Localhost HTTP receiver for hook and statusline payloads.

POST /event   -> queue the payload, reply 204 with an empty body
POST /status  -> queue the payload, reply 200 with the status line to print
GET  /health  -> 200 with a short summary

The empty body on /event matters. Claude Code feeds some hooks' stdout back
into the session as context, so the forwarder must print nothing.

Runs in a daemon thread. The main loop drains the queue.
"""

from __future__ import annotations

import contextlib
import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from .statusline import compose

HOST = "127.0.0.1"
PORT = 47391
MAX_BODY = 1 << 20  # 1 MiB; statusline payloads are small, cap the rest


class _Handler(BaseHTTPRequestHandler):
    q: queue.Queue                       # set by start()
    device_ok: bool = False              # updated by the main loop, read here
    # Deliberately shared across every request: the main loop swaps the whole
    # dict, so readers see one consistent snapshot without locking.
    stats: ClassVar[dict] = {}

    protocol_version = "HTTP/1.1"

    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(min(n, MAX_BODY)) if n > 0 else b""

    def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self) -> None:
        kind = {"/event": "event", "/status": "status"}.get(self.path)
        if kind is None:
            self._send(404)
            return

        raw = self._read_body()
        # A malformed body is dropped; a hook must never see an error.
        payload = None
        with contextlib.suppress(ValueError, UnicodeDecodeError):
            payload = json.loads(raw)
        if isinstance(payload, dict):
            # Drop rather than block. A hook waiting on this would stall the
            # Claude Code session that fired it.
            with contextlib.suppress(queue.Full):
                self.q.put_nowait((kind, payload))

        if kind == "status":
            text = compose(payload, type(self).device_ok) if isinstance(payload, dict) else ""
            self._send(200, text.encode("utf-8", "replace"))
        else:
            self._send(204)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._send(404)
            return
        # events_received is the field that matters when nothing shows on the
        # display: zero means Claude Code is not calling the hooks at all,
        # which is a settings problem, not a daemon or wiring problem.
        cls = type(self)
        body = json.dumps({"ok": True, "device": cls.device_ok, **cls.stats}).encode()
        self._send(200, body, "application/json")

    def log_message(self, *args) -> None:  # silence default stderr logging
        pass


def start(q: queue.Queue, host: str = HOST, port: int = PORT) -> ThreadingHTTPServer:
    _Handler.q = q
    srv = ThreadingHTTPServer((host, port), _Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, name="hook-server", daemon=True).start()
    return srv


def set_device_ok(ok: bool) -> None:
    """Called from the main loop. A plain attribute write, atomic under the GIL."""
    _Handler.device_ok = ok


def set_stats(**kwargs) -> None:
    """Replace the /health extras. Whole-dict swap, so readers never tear."""
    _Handler.stats = dict(kwargs)
