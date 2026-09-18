"""Localhost HTTP receiver for hook and statusline payloads.

POST /event   -> queue the payload, reply 204 with an empty body
POST /status  -> queue the payload, reply 200 with the status line to print
POST /forget  -> end the session named in the body (id prefix or label)
GET  /health  -> 200 with a short summary

The empty body on /event matters. Claude Code feeds some hooks' stdout back
into the session as context, so the forwarder must print nothing.

Runs in a daemon thread. The main loop drains the queue.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from . import procwatch
from .statusline import compose

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 47391
MAX_BODY = 1 << 20  # 1 MiB; statusline payloads are small, cap the rest
FORGET_WAIT_S = 2.0  # the main loop answers within a tick; this is generous


class _Handler(BaseHTTPRequestHandler):
    q: queue.Queue                       # set by start()
    # Updated by the main loop, read here: "ok", "absent" or "silent", as
    # defined in serial_link. "silent" is a board whose firmware has stopped
    # while its port stays open.
    device_state: str = "absent"
    heartbeat: dict | None = None        # the last heartbeat's counters
    # Deliberately shared across every request: the main loop swaps the whole
    # dict, so readers see one consistent snapshot without locking.
    stats: ClassVar[dict] = {}
    # Sessions whose process has already been looked up, successfully or not.
    # The lookup costs tens of milliseconds on the hook's clock, so it runs once
    # per session plus once per SessionStart, never on the busy tool events.
    tried: ClassVar[set[str]] = set()

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

    def _session_process(self, payload: dict) -> tuple[int, float] | None:
        """Look up the Claude Code process behind this hook, if it is due.

        Has to happen now, before the reply: once curl disconnects, its row
        leaves the TCP table and nothing identifies it any more.
        """
        sid = payload.get("session_id")
        if not isinstance(sid, str) or not sid:
            return None
        cls = type(self)
        if sid in cls.tried and payload.get("hook_event_name") != "SessionStart":
            return None
        cls.tried.add(sid)
        proc = procwatch.peer_session_process(self.client_address[1],
                                              self.server.server_address[1])
        if proc is None:
            log.debug("session %s: owning process not found", sid[:8])
            return None
        log.debug("session %s: process %d via %s", sid[:8], proc.pid,
                  " <- ".join(reversed(proc.chain)))
        return proc.pid, proc.created

    def _forget(self) -> None:
        key = self._read_body().decode("utf-8", "replace").strip()
        reply: queue.Queue = queue.Queue(maxsize=1)
        try:
            self.q.put_nowait(("forget", key, reply))
            hits = reply.get(timeout=FORGET_WAIT_S)
        except (queue.Full, queue.Empty):
            self._send(503, b'{"error": "daemon busy"}', "application/json")
            return
        code = 200 if len(hits) == 1 else 404 if not hits else 409
        body = {"ended" if code == 200 else "matches": hits}
        self._send(code, json.dumps(body).encode(), "application/json")

    def do_POST(self) -> None:
        if self.path == "/forget":
            self._forget()
            return
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
            proc = self._session_process(payload) if kind == "event" else None
            # Drop rather than block. A hook waiting on this would stall the
            # Claude Code session that fired it.
            with contextlib.suppress(queue.Full):
                self.q.put_nowait((kind, payload, proc))

        if kind == "status":
            text = compose(payload, type(self).device_state) if isinstance(payload, dict) else ""
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
        #
        # `device` means the board answered recently, not that its port is open.
        # A hung board keeps both its port and its last frame, and reporting the
        # port had /health say "device": true through a seven-hour freeze.
        #
        # `pid` names the process actually holding the port. A daemon launched
        # by hand rather than by the Scheduled Task looks exactly like the
        # task's own until you try to restart it, and the task then has nothing
        # to stop and its fresh copy loses the bind.
        cls = type(self)
        body = json.dumps({"ok": True, "pid": os.getpid(),
                           "device": cls.device_state == "ok",
                           "device_state": cls.device_state,
                           "heartbeat": cls.heartbeat, **cls.stats}).encode()
        self._send(200, body, "application/json")

    def log_message(self, *args) -> None:  # silence default stderr logging
        pass


class _Server(ThreadingHTTPServer):
    """A server that refuses to share its port.

    `HTTPServer` sets `allow_reuse_address`, and on Windows SO_REUSEADDR lets a
    *second* process bind an address another process is already listening on --
    unlike Linux, where it only skips the TIME_WAIT delay. So the second daemon
    started cleanly, the "another beacon-host running?" check in main never
    fired, and the two split hook events between them at random. That is easy to
    hit, because the README asks you to run `beacon-host --dry-run -v` as a
    setup step while the scheduled task is already running.

    Turning it off costs only the TIME_WAIT wait on a quick restart, which the
    daemon's own reconnect loop already tolerates.
    """

    allow_reuse_address = False


def start(q: queue.Queue, host: str = HOST, port: int = PORT) -> ThreadingHTTPServer:
    _Handler.q = q
    srv = _Server((host, port), _Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, name="hook-server", daemon=True).start()
    return srv


def set_device(state: str, heartbeat: dict | None = None) -> None:
    """Called from the main loop. Plain attribute writes, atomic under the GIL."""
    _Handler.device_state = state
    _Handler.heartbeat = heartbeat


def retry_session(sid: str) -> None:
    """Look up `sid`'s process again on its next event. Called after /forget,
    which drops the session and so the PID with it."""
    _Handler.tried.discard(sid)


def set_stats(**kwargs) -> None:
    """Replace the /health extras. Whole-dict swap, so readers never tear."""
    _Handler.stats = dict(kwargs)
