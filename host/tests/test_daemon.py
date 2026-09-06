"""Config, statusline composition, and an end-to-end pass through the HTTP server."""

import json
import logging
import queue
import urllib.request

from beacon_host import hook_server
from beacon_host.config import Config
from beacon_host.main import setup_logging
from beacon_host.state import SessionStore
from beacon_host.statusline import compose

STATUS = {
    "session_id": "abc",
    "model": {"display_name": "Claude Opus 5"},
    "workspace": {"current_dir": "C:/Repos/session-beacon"},
    "cost": {"total_cost_usd": 1.2345},
    "context_window": {"used_percentage": 62.7, "context_window_size": 200000},
}


def test_compose_statusline():
    out = compose(STATUS, device_ok=True)
    assert "Claude Opus 5" in out and "session-beacon" in out
    assert "ctx 62%" in out and "$1.23" in out and out.endswith("beacon")


def test_compose_marks_missing_device():
    assert compose(STATUS, device_ok=False).endswith("beacon?")


def test_compose_survives_garbage():
    assert compose({}, True) == "beacon"
    assert compose({"model": "not-a-dict"}, True) == ""


def test_config_defaults_and_missing_file():
    cfg = Config.load("does-not-exist.toml")
    assert cfg.http_port == 47391 and cfg.port is None and cfg.max_rows == 6


def test_config_from_file(tmp_path):
    """Windows paths belong in TOML *literal* strings, the single-quoted kind.
    A lone backslash inside a double-quoted TOML string is a parse error, which
    is an easy trap when hand-writing this file."""
    p = tmp_path / "c.toml"
    p.write_text(
        'port = "COM9"\n'
        "stale_after_s = 42\n\n"
        "[labels]\n"
        r"'C:\Repos\backslash-form' = 'bs'" "\n"
        "'C:/Repos/slash-form' = 'sl'\n",
        encoding="utf-8")
    cfg = Config.load(p)
    assert cfg.port == "COM9" and cfg.stale_after_s == 42
    # Both separators normalise to the same lookup key.
    assert cfg.labels["C:/Repos/backslash-form"] == "bs"
    assert cfg.labels["C:/Repos/slash-form"] == "sl"


def test_label_override_applies():
    store = SessionStore(label_overrides={"C:/Repos/thing": "thg"})
    store.apply_event(
        {"hook_event_name": "SessionStart", "session_id": "s", "cwd": r"C:\Repos\thing"}, 0)
    assert store.snapshot(0)["s"][0]["l"] == "thg"


def _post(port, path, obj):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(obj).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=2) as r:
        return r.status, r.read().decode()


def test_http_event_and_status_roundtrip():
    """An /event reply must be empty: Claude Code feeds some hook stdout back."""
    q = queue.Queue()
    srv = hook_server.start(q, port=47455)
    hook_server.set_device_ok(True)
    try:
        status, body = _post(47455, "/event",
                             {"hook_event_name": "Stop", "session_id": "s1",
                              "cwd": "C:/Repos/homelab"})
        assert status == 204 and body == ""

        status, body = _post(47455, "/status", STATUS)
        assert status == 200 and "ctx 62%" in body

        with urllib.request.urlopen("http://127.0.0.1:47455/health", timeout=2) as r:
            assert json.loads(r.read())["device"] is True

        kinds = [q.get_nowait() for _ in range(2)]
        assert [k for k, _ in kinds] == ["event", "status"]
    finally:
        srv.shutdown()


def test_http_ignores_malformed_body():
    q = queue.Queue()
    srv = hook_server.start(q, port=47456)
    try:
        req = urllib.request.Request("http://127.0.0.1:47456/event",
                                     data=b"{not json", method="POST")
        with urllib.request.urlopen(req, timeout=2) as r:
            assert r.status == 204
        assert q.empty()
    finally:
        srv.shutdown()


def test_full_pipeline_produces_snapshot():
    """Hook events in, protocol snapshot out, in the order the display needs."""
    store = SessionStore()
    events = [
        ("UserPromptSubmit", "a", "C:/Repos/homelab"),
        ("UserPromptSubmit", "b", "C:/Repos/env_monitoring"),
        ("PermissionRequest", "b", "C:/Repos/env_monitoring"),
        ("StopFailure", "c", "C:/Repos/session-beacon"),
    ]
    for i, (name, sid, cwd) in enumerate(events):
        store.apply_event(
            {"hook_event_name": name, "session_id": sid, "cwd": cwd,
             "error_type": "rate_limit"}, i)
    store.apply_status({**STATUS, "session_id": "b"}, 4)

    snap = store.snapshot(10)
    assert snap["t"] == "snap" and snap["v"] == 1 and snap["n"] == 3
    assert [r["st"] for r in snap["s"]] == ["need", "err", "work"]
    assert snap["s"][0]["ctx"] == 62 and snap["s"][0]["m"] == "opus5"
    assert snap["cost"] == 1.23
    assert all(len(r["l"]) <= 16 for r in snap["s"])
    assert len(json.dumps(snap, separators=(",", ":")).encode()) < 1024


def test_default_config_accepts_either_filename(tmp_path, monkeypatch):
    """config.toml is the name people reach for first; ignoring it silently
    looks exactly like the daemon ignoring your settings."""
    from beacon_host import config as cfgmod

    root = tmp_path
    monkeypatch.setattr(cfgmod, "default_config_path",
                        lambda: next((root / n for n in cfgmod.CONFIG_NAMES
                                      if (root / n).is_file()), None))
    assert cfgmod.default_config_path() is None

    (root / "config.toml").write_text('port = "COM7"\n', encoding="utf-8")
    p = cfgmod.default_config_path()
    assert p is not None and p.name == "config.toml"
    assert cfgmod.Config.load(p).port == "COM7"

    # config.local.toml takes precedence when both exist.
    (root / "config.local.toml").write_text('port = "COM8"\n', encoding="utf-8")
    p = cfgmod.default_config_path()
    assert p.name == "config.local.toml"
    assert cfgmod.Config.load(p).port == "COM8"
    assert cfgmod.Config.load(p).source == p


def test_health_reports_event_count():
    """events_received is what distinguishes 'hooks not installed' from a
    daemon or wiring fault, so it has to be visible without a debugger."""
    q = queue.Queue()
    srv = hook_server.start(q, port=47457)
    try:
        hook_server.set_device_ok(True)
        hook_server.set_stats(sessions=0, events_received=0, last_event_age_s=None)
        with urllib.request.urlopen("http://127.0.0.1:47457/health", timeout=2) as r:
            h = json.loads(r.read())
        assert h["events_received"] == 0 and h["sessions"] == 0 and h["device"] is True

        hook_server.set_stats(sessions=2, events_received=17, last_event_age_s=0.4)
        with urllib.request.urlopen("http://127.0.0.1:47457/health", timeout=2) as r:
            h = json.loads(r.read())
        assert h["events_received"] == 17 and h["sessions"] == 2
    finally:
        hook_server.set_stats()
        srv.shutdown()


def test_logging_creates_missing_log_directory(tmp_path):
    """A missing log directory must not take the daemon down at startup."""
    target = tmp_path / "does" / "not" / "exist" / "beacon.log"
    setup_logging(Config(log_file=str(target)))
    logging.getLogger("beacon_host").info("hello")
    logging.shutdown()
    assert target.is_file() and "hello" in target.read_text(encoding="utf-8")


def test_logging_survives_an_unusable_log_path(tmp_path):
    """An unusable path degrades to stderr rather than killing the process."""
    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    setup_logging(Config(log_file=str(blocker / "nested" / "beacon.log")))
    logging.getLogger("beacon_host").info("still alive")
    logging.shutdown()
