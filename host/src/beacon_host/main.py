"""beacon-host entry point.

    uv run beacon-host --port COM5 --log-level DEBUG

Main loop: drain hook queue -> update state -> tick -> push snapshot if changed
or once per second.
"""

from __future__ import annotations

import argparse
import json
import logging
import queue
import time

from . import hook_server
from .serial_link import SerialLink
from .state import SessionStore

log = logging.getLogger("beacon_host")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="beacon-host")
    ap.add_argument("--port", help="COM port (default: auto-detect Nano ESP32 by VID/PID)")
    ap.add_argument("--http-port", type=int, default=hook_server.PORT)
    ap.add_argument("--stale-after", type=float, default=300.0, help="seconds")
    ap.add_argument("--log-level", default="INFO")
    ap.add_argument("--dry-run", action="store_true", help="print snapshots instead of using serial")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    q: queue.Queue = queue.Queue()
    hook_server.start(q, port=args.http_port)
    log.info("listening on 127.0.0.1:%d", args.http_port)

    store = SessionStore(stale_after_s=args.stale_after)
    link = None if args.dry_run else SerialLink(args.port)

    last_sent: str | None = None
    last_push = 0.0
    while True:
        now = time.time()
        changed = False
        try:
            while True:
                kind, payload = q.get_nowait()
                if kind == "event":
                    store.apply_event(payload, now)
                else:
                    store.apply_status(payload, now)
                changed = True
        except queue.Empty:
            pass

        store.tick(now)
        snap = store.snapshot(now)
        # Compare without the age/ts fields so we do not spam on every tick.
        key = json.dumps({k: v for k, v in snap.items() if k != "ts"}, sort_keys=True)
        if changed or key != last_sent or now - last_push >= 1.0:
            if link:
                link.send(snap)
                for ln in link.read_lines():
                    log.debug("device: %s", ln)
            else:
                print(json.dumps(snap, separators=(",", ":")))
            last_sent, last_push = key, now

        time.sleep(0.05)


if __name__ == "__main__":
    main()
