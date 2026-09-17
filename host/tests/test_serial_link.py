"""Heartbeat tracking: telling a running board from one that has stopped.

A hung board stays enumerated and accepts writes, so an open port proved
nothing and /health said "device": true through a seven-hour freeze. These
tests pin down how the heartbeat decides instead.
"""

import json
import logging

from beacon_host.serial_link import ABSENT, OK, SILENT, DeviceHealth, SerialLink


def hb(up, **kw):
    return json.dumps({"t": "hb", "fw": "0.2.0", "up": up, "rx": 10, "bad": 0,
                       "drop": 0, "txdrop": 0, "since": 400, "render": 65,
                       "spi": "hw", "rst": "power", **kw})


def test_fresh_port_gets_a_grace_period():
    h = DeviceHealth(timeout_s=15)
    h.on_open("COM5", 100.0)
    assert h.state(100.0, True) == OK
    assert h.state(114.9, True) == OK
    assert h.state(115.0, True) == SILENT


def test_closed_port_is_absent_whatever_the_heartbeats_say():
    h = DeviceHealth()
    h.on_open("COM5", 0.0)
    h.on_line(hb(1), 1.0)
    assert h.state(2.0, False) == ABSENT


def test_heartbeats_keep_it_ok_and_their_absence_makes_it_silent():
    h = DeviceHealth(timeout_s=15)
    h.on_open("COM5", 0.0)
    for t in range(0, 60, 3):
        h.on_line(hb(t), float(t))
        assert h.state(t + 1.0, True) == OK
    assert h.state(57.0 + 14.9, True) == OK
    assert h.state(57.0 + 15.0, True) == SILENT


def test_stale_heartbeat_does_not_condemn_a_reopened_port():
    """The last heartbeat survives a reconnect; the open must still reset the clock."""
    h = DeviceHealth(timeout_s=15)
    h.on_open("COM5", 0.0)
    h.on_line(hb(1), 1.0)
    h.on_open("COM5", 100.0)
    assert h.state(101.0, True) == OK


def test_going_silent_warns_once_and_recovery_is_logged(caplog):
    h = DeviceHealth(timeout_s=15)
    h.on_open("COM5", 0.0)
    h.on_line(hb(26940, rx=19809), 1.0)
    with caplog.at_level(logging.INFO, logger="beacon_host.serial_link"):
        assert h.check(10.0, True) == OK
        assert h.check(20.0, True) == SILENT
        assert h.check(3600.0, True) == SILENT
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        msg = warnings[0].getMessage()
        assert "COM5" in msg and "replug" in msg and "19809" in msg

        caplog.clear()
        h.on_line(hb(26941), 3601.0)
        assert h.check(3601.0, True) == OK
        assert any("answering again" in r.getMessage() for r in caplog.records)


def test_unplugging_ends_a_silent_episode(caplog):
    """Replugging is the cure, so the next freeze must warn again."""
    h = DeviceHealth(timeout_s=15)
    h.on_open("COM5", 0.0)
    with caplog.at_level(logging.WARNING, logger="beacon_host.serial_link"):
        h.check(20.0, True)
        h.check(21.0, False)
        h.on_open("COM5", 30.0)
        h.check(50.0, True)
    assert sum("no heartbeat" in r.getMessage() for r in caplog.records) == 2


def test_a_reboot_is_logged_with_its_reason(caplog):
    """A watchdog rescue drops the port, so only the uptime shows it happened."""
    h = DeviceHealth()
    h.on_open("COM5", 0.0)
    h.on_line(hb(5000), 1.0)
    h.on_open("COM5", 10.0)
    with caplog.at_level(logging.WARNING, logger="beacon_host.serial_link"):
        h.on_line(hb(2, rst="task_wdt"), 11.0)
    msg = caplog.records[-1].getMessage()
    assert "rebooted after 5000s" in msg and "task_wdt" in msg


def test_other_lines_are_not_heartbeats():
    h = DeviceHealth()
    for line in ("demo mode off", "{not json", '{"t":"snap"}', "[1,2]", '"hb"'):
        assert h.on_line(line, 1.0) is False
    assert h.report(1.0) is None


def test_report_carries_the_counters_and_their_age():
    h = DeviceHealth()
    h.on_line(hb(36, rx=2, txdrop=3, aflip=1, wedge=0), 10.0)
    assert h.report(12.34) == {"fw": "0.2.0", "up": 36, "rx": 2, "bad": 0,
                               "drop": 0, "txdrop": 3, "aflip": 1, "wedge": 0,
                               "rst": "power", "age_s": 2.3}


def test_a_recovered_return_path_is_reported_once(caplog):
    """The board cures a dead return path by rebooting, so `wedge` rising is
    all anyone gets: by the time the count arrives the episode is over, and
    the display never stopped rendering while it lasted."""
    h = DeviceHealth()
    with caplog.at_level(logging.WARNING):
        h.on_line(hb(900, wedge=0), 0.0)
        assert not caplog.records
        h.on_line(hb(2, wedge=1), 40.0)   # fresh boot, one cure behind it
        msgs = [r.getMessage() for r in caplog.records]
    assert any("return path" in m and "1 time(s)" in m for m in msgs)
    assert any("rebooted after 900s up" in m for m in msgs)


def test_an_unchanged_wedge_count_says_nothing(caplog):
    """It is kept across reboots, so only a rise is news."""
    h = DeviceHealth()
    with caplog.at_level(logging.WARNING):
        h.on_line(hb(10, wedge=4), 0.0)
        h.on_line(hb(13, wedge=4), 3.0)
    assert not [r for r in caplog.records if "return path" in r.getMessage()]


def test_report_tolerates_an_older_firmware():
    """0.1.0 sends no txdrop, aflip, wedge or rst; the report leaves them out."""
    h = DeviceHealth()
    h.on_line('{"t":"hb","fw":"0.1.0","up":3,"rx":0,"bad":0,"drop":0}', 0.0)
    assert h.report(0.0) == {"fw": "0.1.0", "up": 3, "rx": 0, "bad": 0,
                             "drop": 0, "age_s": 0.0}


class _FakeSerial:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.is_open = True

    def read(self, _n):
        return self.chunks.pop(0) if self.chunks else b""

    def close(self):
        self.is_open = False


def test_poll_joins_a_line_split_across_reads():
    line = hb(42).encode() + b"\n"
    link = SerialLink("COM5")
    link._ser = _FakeSerial([line[:50], line[50:] + b"demo mode off\r\n", b""])
    link.health.on_open("COM5", 0.0)

    assert link.poll(0.1) == []
    assert link.poll(0.2) == [hb(42), "demo mode off"]
    assert link.heartbeat_report(0.2)["up"] == 42
    assert link.poll(20.0) == []
    assert link.device_state(20.0) == SILENT
