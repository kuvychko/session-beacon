"""USB serial link to the beacon device. Handles discovery and reconnect.

The device disappears and comes back on every reflash, and Windows can move
the COM number if the cable changes port, so this never assumes the link is
up. Every send is best-effort and a failure just closes the port for the next
reconnect attempt.

An open port is not a working device. A board whose sketch has hung stays
enumerated, and Windows accepts writes to it without complaint for as long as
anyone cares to send them, so the only evidence of life is the heartbeat the
firmware sends every 3 s. `DeviceHealth` turns that into the device state the
daemon reports.

A missing heartbeat is not proof of a frozen display, though this file used to
say it was. The device's return path can die on its own -- see txWatchdog() in
the firmware -- leaving a board that renders every snapshot it is sent and says
nothing back. Nothing on this side can tell the two apart, so the wording here
and in the status line commits to neither, and the cure lives in the firmware.
"""

from __future__ import annotations

import json
import logging
import socket
import time
from typing import Any

import serial
from serial.tools import list_ports

log = logging.getLogger(__name__)

# The boards the beacon firmware runs on, as (VID, PID). The Nano ESP32's pair
# is its own; the RP2040's is the Raspberry Pi vendor with the generic RP2040
# product id, which every board built on that chip shares, so this recognises
# "an RP2040", not specifically a Zero. Detection only picks which port to open:
# a board that is not a beacon sends no heartbeat and is reported `silent`, and
# `--port COMx` skips the search entirely.
NANO_ESP32 = (0x2341, 0x0070)
RP2040 = (0x2E8A, 0x0003)
KNOWN_BOARDS = (NANO_ESP32, RP2040)
BAUD = 115200
RECONNECT_S = 2.0
# Five missed heartbeats. Long enough to ride out a slow USB moment, short
# enough that a board which has stopped answering is reported while someone is
# still looking.
HB_TIMEOUT_S = 15.0
RX_MAX = 8192  # a device that never sends a newline cannot grow this forever

# The device states reported by /health and the statusline.
OK = "ok"          # port open and the board has sent a heartbeat recently
ABSENT = "absent"  # no port: unplugged, rebooting, or held by another program
SILENT = "silent"  # port open but no heartbeat: see the note at the top


def find_port() -> str | None:
    """First known board by USB VID/PID, so a moved cable does not need config."""
    for p in list_ports.comports():
        if (p.vid, p.pid) in KNOWN_BOARDS:
            return p.device
    return None


def _wedge_detail(hb: dict[str, Any]) -> str:
    """What the board saw during the episode its last self-reboot ended."""
    cause, flips = hb.get("wcause"), hb.get("wflip")
    if cause == "det":
        what = "the board believed no host held the port"
    elif cause == "att":
        what = "the port was held but nothing it wrote was being collected"
    else:
        return "firmware too old to say why"
    if isinstance(flips, int) and flips:
        what += f", and that flag changed {flips} time(s) during it"
    return what


class DeviceHealth:
    """What the heartbeats say about the board. Pure: the caller passes the clock.

    The last heartbeat is kept across reconnects on purpose. A watchdog reboot
    drops the port, so the only way to see that one happened is to compare the
    board's uptime before and after.
    """

    def __init__(self, timeout_s: float = HB_TIMEOUT_S) -> None:
        self.timeout_s = timeout_s
        self.port = ""
        self.opened_at: float | None = None
        self.last_hb: dict[str, Any] | None = None
        self.last_hb_at: float | None = None
        self._silent = False

    def on_open(self, port: str, now: float) -> None:
        # Grace period: a board that has just been opened is given a full
        # timeout to send its first heartbeat before it is called silent.
        self.port = port
        self.opened_at = now

    def on_line(self, line: str, now: float) -> bool:
        """Record a heartbeat. Returns False for anything that is not one."""
        try:
            msg = json.loads(line)
        except ValueError:
            return False
        if not isinstance(msg, dict) or msg.get("t") != "hb":
            return False

        prev = self.last_hb
        up, prev_up = msg.get("up"), (prev or {}).get("up")
        if isinstance(up, int) and isinstance(prev_up, int) and up < prev_up:
            log.warning("beacon rebooted after %ds up (reset reason: %s)",
                        prev_up, msg.get("rst", "unknown"))
        # `wedge` counts the firmware's own cures for a dead return path and is
        # kept across the reboot that performs one, so a rise here is the only
        # report anyone gets that an episode happened at all: the board was
        # rendering the whole time, and by now it is answering again.
        # `wcause` and `wflip` (0.2.2) say what the episode looked like from
        # the board, which nothing on this side can see.
        w, prev_w = msg.get("wedge"), (prev or {}).get("wedge")
        if isinstance(w, int) and isinstance(prev_w, int) and w > prev_w:
            log.warning("beacon rebooted itself to recover its serial return "
                        "path (%d time(s) so far; %s). The display was never "
                        "frozen. If this repeats, turn off USB selective "
                        "suspend for the port.", w, _wedge_detail(msg))
        self.last_hb = msg
        self.last_hb_at = now
        return True

    def state(self, now: float, port_open: bool) -> str:
        if not port_open:
            return ABSENT
        # The later of the open and the last heartbeat, so a heartbeat left
        # over from before a reconnect cannot make a fresh port look silent.
        heard = max((t for t in (self.opened_at, self.last_hb_at) if t is not None),
                    default=now)
        return OK if now - heard < self.timeout_s else SILENT

    def check(self, now: float, port_open: bool) -> str:
        """`state`, plus a log line when the board stops or resumes answering."""
        st = self.state(now, port_open)
        if st == SILENT and not self._silent:
            self._silent = True
            hb = self.last_hb or {}
            log.warning(
                "beacon on %s has sent no heartbeat for %ds with the port open "
                "(last: fw %s, up %ss, rx %s). Either the firmware has stopped, "
                "or only its return path has and the display is still fine; "
                "from here the two are identical. Firmware 0.2.2 and later "
                "reboots itself out of both within a minute. On an older one, "
                "or if this persists, replug the board.",
                self.port or "?", int(self.timeout_s), hb.get("fw", "?"),
                hb.get("up", "?"), hb.get("rx", "?"))
        elif st == OK and self._silent:
            self._silent = False
            log.info("beacon on %s is answering again", self.port or "?")
        elif st == ABSENT:
            # Replugging is the cure for silence, so a disconnect ends the episode.
            self._silent = False
        return st

    def report(self, now: float) -> dict[str, Any] | None:
        """The last heartbeat's counters, for /health."""
        if self.last_hb is None or self.last_hb_at is None:
            return None
        keys = ("fw", "board", "up", "rx", "bad", "drop", "txdrop", "aflip",
                "wedge", "wcause", "wflip", "rst")
        out = {k: self.last_hb[k] for k in keys if k in self.last_hb}
        out["age_s"] = round(now - self.last_hb_at, 1)
        return out


class SerialLink:
    def __init__(self, port: str | None = None) -> None:
        self._port = port
        self._ser: serial.Serial | None = None
        self._next_try = 0.0
        self._warned = False
        self._rx = bytearray()
        self.health = DeviceHealth()

    @property
    def connected(self) -> bool:
        """The port is open. Says nothing about whether the board is running."""
        return bool(self._ser and self._ser.is_open)

    def ensure_open(self) -> bool:
        if self.connected:
            return True
        now = time.monotonic()
        if now < self._next_try:
            return False
        self._next_try = now + RECONNECT_S

        port = self._port or find_port()
        if not port:
            if not self._warned:
                log.warning("no beacon device found; will keep looking")
                self._warned = True
            return False
        try:
            self._ser = serial.Serial(port, BAUD, timeout=0, write_timeout=1)
        except serial.SerialException as e:
            if not self._warned:
                # Usually the Arduino IDE serial monitor holding the port.
                log.warning("cannot open %s (%s); will keep retrying", port, e)
                self._warned = True
            self._ser = None
            return False

        log.info("connected to beacon on %s", port)
        self._warned = False
        self._rx.clear()
        self.health.on_open(port, time.monotonic())
        self.send({"t": "hello", "v": 1, "host": socket.gethostname()})
        return True

    def send(self, msg: dict[str, Any]) -> bool:
        if not self.ensure_open():
            return False
        line = json.dumps(msg, separators=(",", ":")) + "\n"
        try:
            self._ser.write(line.encode())  # type: ignore[union-attr]
            return True
        except (serial.SerialException, OSError) as e:
            log.warning("write failed, dropping link: %s", e)
            self.close()
            return False

    def poll(self, now: float | None = None) -> list[str]:
        """Drain device-to-host lines, feed heartbeats to `health`. Non-blocking.

        Returns the complete lines read. A line split across two reads is held
        until its newline arrives rather than parsed as two broken halves.
        """
        now = time.monotonic() if now is None else now
        lines: list[str] = []
        if self.connected:
            try:
                data = self._ser.read(4096)  # type: ignore[union-attr]
            except (serial.SerialException, OSError):
                self.close()
                data = b""
            if data:
                self._rx += data
                *complete, rest = self._rx.split(b"\n")
                self._rx = bytearray(rest[-RX_MAX:])
                for raw in complete:
                    ln = raw.decode(errors="replace").strip()
                    if not ln:
                        continue
                    log.debug("device: %s", ln)
                    self.health.on_line(ln, now)
                    lines.append(ln)
        self.health.check(now, self.connected)
        return lines

    def device_state(self, now: float | None = None) -> str:
        now = time.monotonic() if now is None else now
        return self.health.state(now, self.connected)

    def heartbeat_report(self, now: float | None = None) -> dict[str, Any] | None:
        return self.health.report(time.monotonic() if now is None else now)

    def close(self) -> None:
        if self._ser:
            try:
                self._ser.close()
            except (serial.SerialException, OSError) as e:
                log.debug("error closing port, dropping it anyway: %s", e)
        self._ser = None
