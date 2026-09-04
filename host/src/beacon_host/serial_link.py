"""USB serial link to the beacon device. Handles discovery and reconnect."""

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
    for p in list_ports.comports():
        if p.vid == NANO_ESP32_VID and p.pid == NANO_ESP32_PID:
            return p.device
    return None


class SerialLink:
    def __init__(self, port: str | None = None) -> None:
        self._port = port
        self._ser: serial.Serial | None = None
        self._next_try = 0.0

    def ensure_open(self) -> bool:
        if self._ser and self._ser.is_open:
            return True
        now = time.monotonic()
        if now < self._next_try:
            return False
        self._next_try = now + RECONNECT_S
        port = self._port or find_port()
        if not port:
            return False
        try:
            self._ser = serial.Serial(port, BAUD, timeout=0, write_timeout=1)
            log.info("opened %s", port)
            self.send({"t": "hello", "v": 1, "host": socket.gethostname()})
            return True
        except serial.SerialException as e:
            log.warning("open %s failed: %s", port, e)
            self._ser = None
            return False

    def send(self, msg: dict[str, Any]) -> None:
        if not self.ensure_open():
            return
        line = json.dumps(msg, separators=(",", ":")) + "\n"
        try:
            self._ser.write(line.encode())  # type: ignore[union-attr]
        except serial.SerialException as e:
            log.warning("write failed, closing: %s", e)
            self.close()

    def read_lines(self) -> list[str]:
        """Drain any device-to-host lines (heartbeats). Non-blocking."""
        if not (self._ser and self._ser.is_open):
            return []
        try:
            data = self._ser.read(4096)
        except serial.SerialException:
            self.close()
            return []
        return [ln for ln in data.decode(errors="replace").split("\n") if ln.strip()]

    def close(self) -> None:
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None
