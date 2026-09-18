"""Bench test for the firmware's txWatchdog(): stand in for the daemon, send
`stall` or `wedge`, and check the board reboots itself and counts the cure.

The watchdog fires only while snapshots keep arriving, so this sends one a
second, as the daemon does. It opens the port with pyserial's default DTR/RTS
and never toggles them: the core restarts the chip into its bootloader on a
DTR/RTS pattern. It needs the port to itself, so from the repository root:

    ./scripts/restart-daemon.ps1 -StopOnly
    uv run --project host python scripts/wedge-bench.py COM4 stall   # expect wcause=att
    uv run --project host python scripts/wedge-bench.py COM4 wedge   # expect wcause=det
    ./scripts/restart-daemon.ps1

Exits 0 when the board rebooted within the run and its next heartbeat shows
`wedge` one higher and `rst` as `sw`.
"""
import json
import sys
import time

import serial

if len(sys.argv) != 3 or sys.argv[2] not in ("stall", "wedge"):
    sys.exit("usage: wedge-bench.py PORT stall|wedge")
PORT, CMD = sys.argv[1], sys.argv[2]
SNAP = json.dumps({"t": "snap", "v": 1, "ts": 0, "n": 1, "sel": 0,
                   "s": [{"id": "bench000", "l": f"bench {CMD}", "st": "work", "age": 1}]},
                  separators=(",", ":")).encode() + b"\n"


def log(msg):
    print(f"{time.monotonic() - T0:6.1f}s  {msg}", flush=True)


def open_port():
    while True:
        try:
            return serial.Serial(PORT, 115200, timeout=0, write_timeout=1)
        except serial.SerialException:
            if time.monotonic() - T0 > 120:
                raise
            time.sleep(0.5)


T0 = time.monotonic()
ser = open_port()
log(f"opened {PORT}")
ser.write(b'{"t":"hello","v":1,"host":"bench"}\n')
buf, before, sent_at, gone_at, next_snap = b"", None, None, None, 0.0
while time.monotonic() - T0 < 150:
    now = time.monotonic()
    try:
        if now >= next_snap:
            next_snap = now + 1.0
            ser.write(SNAP)
        buf += ser.read(4096)
    except serial.SerialException as e:
        gone_at = time.monotonic()
        log(f"port dropped ({type(e).__name__}); "
            f"{gone_at - sent_at:.1f}s after '{CMD}'" if sent_at else "port dropped")
        try:
            ser.close()
        except Exception:
            pass
        ser = open_port()
        log("reopened")
        buf = b""
        continue
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        text = line.decode(errors="replace").strip()
        if not text:
            continue
        try:
            hb = json.loads(text)
        except ValueError:
            log(f"text: {text}")
            continue
        if hb.get("t") != "hb":
            continue
        keys = ("up", "rx", "txdrop", "aflip", "wedge", "wcause", "wflip", "rst")
        log("hb " + " ".join(f"{k}={hb.get(k)}" for k in keys))
        if before is None:
            before = hb
            ser.write(CMD.encode() + b"\n")
            sent_at = time.monotonic()
            log(f"sent '{CMD}'")
        elif gone_at is not None:
            ok = (hb["up"] < before["up"] + 5 and hb["wedge"] == before["wedge"] + 1
                  and hb["rst"] == "sw")
            log(f"{'PASS' if ok else 'FAIL'}: wedge {before['wedge']} -> {hb['wedge']}, "
                f"wcause={hb['wcause']}, wflip={hb['wflip']}, rst={hb['rst']}")
            sys.exit(0 if ok else 1)
    time.sleep(0.05)
log("FAIL: no reboot seen")
sys.exit(1)
