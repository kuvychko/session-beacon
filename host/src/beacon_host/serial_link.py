"""USB serial link to the beacon device. Handles discovery and reconnect.

The device disappears and comes back on every reflash, and Windows can move
the COM number if the cable changes port, so this never assumes the link is
up. Every send is best-effort and a failure just closes the port for the next
reconnect attempt.
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

NANO_ESP32_VID = 0x2341
NANO_ESP32_PID = 0x0070
BAUD = 115200
RECONNECT_S = 2.0


def find_port() -> str | None:
    """First Nano ESP32 by USB VID/PID, so a moved cable does not need config."""
    for p in list_ports.comports():
        if p.vid == NANO_ESP32_VID and p.pid == NANO_ESP32_PID:
            return p.device
    return None


class SerialLink:
    def __init__(self, port: str | None = None) -> None:
        self._port = port
        self._ser: serial.Serial | None = None
        self._next_try = 0.0
        self._warned = False

    @property
    def connected(self) -> bool:
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

    def read_lines(self) -> list[str]:
        """Drain device-to-host lines such as heartbeats. Non-blocking."""
        if not self.connected:
            return []
        try:
            data = self._ser.read(4096)  # type: ignore[union-attr]
        except (serial.SerialException, OSError):
            self.close()
            return []
        if not data:
            return []
        return [ln for ln in data.decode(errors="replace").splitlines() if ln.strip()]

    def close(self) -> None:
        if self._ser:
            try:
                self._ser.close()
            except (serial.SerialException, OSError) as e:
                log.debug("error closing port, dropping it anyway: %s", e)
        self._ser = None
