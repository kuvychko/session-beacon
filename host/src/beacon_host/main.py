"""beacon-host entry point.

    uv run beacon-host                      # config.local.toml, auto-detect port
    uv run beacon-host --port COM4 -v       # explicit port, debug logging
    uv run beacon-host --dry-run            # print snapshots instead of serial

Main loop: drain the hook queue, update state, age it, and push a snapshot to
the device when something changed or a second has passed.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import queue
import signal
import sys
import time
from pathlib import Path

from . import hook_server
from .capture import Capture
from .config import Config, default_config_path
from .serial_link import SerialLink
from .state import SessionStore

log = logging.getLogger("beacon_host")

TICK_S = 0.05        # main loop granularity
PUSH_EVERY_S = 1.0   # resend at least this often so on-device timers advance


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="beacon-host")
    ap.add_argument("--config", help="TOML config path (default: config.local.toml)")
    ap.add_argument("--port", help="COM port (default: auto-detect Nano ESP32 by VID/PID)")
    ap.add_argument("--http-port", type=int)
    ap.add_argument("--stale-after", type=float, help="seconds before a busy session goes stale")
    ap.add_argument("--log-file")
    ap.add_argument("--capture", metavar="FILE",
                    help="append redacted hook payloads to FILE as JSONL, "
                         "for building test fixtures")
    ap.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    ap.add_argument("--dry-run", action="store_true", help="print snapshots instead of using serial")
    return ap.parse_args(argv)


def build_config(args: argparse.Namespace) -> Config:
    cfg = Config.load(args.config or default_config_path())
    if args.port:
        cfg.port = args.port
    if args.http_port:
        cfg.http_port = args.http_port
    if args.stale_after:
        cfg.stale_after_s = args.stale_after
    if args.log_file:
        cfg.log_file = args.log_file
    if args.verbose:
        cfg.log_level = "DEBUG"
    return cfg


def setup_logging(cfg: Config) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    problem = None
    if cfg.log_file:
        # Rotating, because this runs unattended at login and nobody prunes it.
        # Create the directory: the handler will not, and a missing one takes
        # the whole daemon down at startup over a log file, which is absurd.
        try:
            Path(cfg.log_file).parent.mkdir(parents=True, exist_ok=True)
            handlers.append(
                logging.handlers.RotatingFileHandler(
                    cfg.log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
                )
            )
        except OSError as e:
            problem = f"file logging disabled, cannot use {cfg.log_file}: {e}"
    logging.basicConfig(
        level=cfg.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    if problem:
        logging.getLogger("beacon_host").warning(problem)


def run(cfg: Config, dry_run: bool = False, capture_path: str | None = None) -> int:
    stopping = False

    def stop(signum, _frame):
        nonlocal stopping
        log.info("signal %s, shutting down", signum)
        stopping = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, stop)
        except (ValueError, OSError):
            pass  # not on the main thread, or unsupported on this platform

    q: queue.Queue = queue.Queue(maxsize=10_000)
    try:
        srv = hook_server.start(q, port=cfg.http_port)
    except OSError as e:
        log.error("cannot bind 127.0.0.1:%d (%s). Another beacon-host running?",
                  cfg.http_port, e)
        return 1
    log.info("listening on 127.0.0.1:%d", cfg.http_port)
    log.info("config: %s", cfg.source or "none found, using defaults")

    store = SessionStore(
        stale_after_s=cfg.stale_after_s,
        ended_grace_s=cfg.ended_grace_s,
        max_rows=cfg.max_rows,
        label_overrides=cfg.labels,
    )
    link = None if dry_run else SerialLink(cfg.port)
    hook_server.set_device_ok(bool(dry_run))

    capture = Capture(capture_path) if capture_path else None
    if capture:
        log.info("capturing redacted payloads to %s", capture.path)

    last_key: str | None = None
    last_push = 0.0
    events_received = 0
    last_event_at: float | None = None
    warned_no_events = False
    started = time.time()
    try:
        while not stopping:
            now = time.time()
            changed = False
            while True:
                try:
                    kind, payload = q.get_nowait()
                except queue.Empty:
                    break
                if capture:
                    capture.write(kind, payload)
                if kind == "event":
                    log.debug("event %s %s", payload.get("hook_event_name"),
                              payload.get("session_id", "")[:8])
                    store.apply_event(payload, now)
                    events_received += 1
                    last_event_at = now
                else:
                    store.apply_status(payload, now)
                changed = True

            # Silence here almost always means the hooks were never added to
            # settings.json, which otherwise presents as "the display is blank"
            # and sends people looking at the wiring instead.
            if not warned_no_events and events_received == 0 and now - started > 60:
                warned_no_events = True
                log.warning(
                    "no hook events in %ds. Claude Code is probably not "
                    "configured: run scripts/install-hooks.ps1, then restart "
                    "your Claude Code sessions so the hooks load.",
                    int(now - started))

            store.tick(now)
            snap = store.snapshot(now)
            # Compare without the timestamp so a quiet minute is not a redraw.
            key = json.dumps({k: v for k, v in snap.items() if k != "ts"}, sort_keys=True)

            if changed or key != last_key or now - last_push >= PUSH_EVERY_S:
                if link is None:
                    print(json.dumps(snap, separators=(",", ":")), flush=True)
                else:
                    link.send(snap)
                    for ln in link.read_lines():
                        log.debug("device: %s", ln)
                    hook_server.set_device_ok(link.connected)
                hook_server.set_stats(
                    sessions=len(store.sessions),
                    events_received=events_received,
                    last_event_age_s=(round(now - last_event_at, 1)
                                      if last_event_at else None),
                    uptime_s=round(now - started, 1),
                    # What is actually on the display. Answering "why does the
                    # screen say that" should not need a serial cable.
                    rows=[f"{r['l']}:{r['st']}:{r['age']}s" for r in snap["s"]],
                )
                last_key, last_push = key, now

            time.sleep(TICK_S)
    finally:
        srv.shutdown()
        if link:
            link.close()
        log.info("stopped")
    return 0


def main() -> None:
    args = parse_args()
    cfg = build_config(args)
    setup_logging(cfg)
    sys.exit(run(cfg, dry_run=args.dry_run, capture_path=args.capture))


if __name__ == "__main__":
    main()
