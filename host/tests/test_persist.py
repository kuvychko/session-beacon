"""Session state must survive a daemon restart.

A session parked on a prompt sends no hook events, so without persistence a
restart drops it and the display under-reports until the user touches it.
"""

import json

from beacon_host import persist
from beacon_host.state import SessionStore, State


def ev(name: str, sid: str, cwd: str = "C:/Repos/session-beacon", **extra):
    return {"hook_event_name": name, "session_id": sid, "cwd": cwd, **extra}


def test_round_trip_keeps_the_ladder_rung_and_the_clock(tmp_path):
    """A session waiting three hours must still read three hours afterwards."""
    p = tmp_path / "sessions.json"
    a = SessionStore()
    a.apply_event(ev("PermissionRequest", "n", "C:/Repos/env_monitoring"), 1000)
    a.tick(1000 + 3 * 3600)
    assert a.sessions["n"].state == State.WAITING

    persist.save(p, a, 1000 + 3 * 3600)

    b = SessionStore()
    assert persist.load(p, b, 1000 + 3 * 3600, 86400.0) == 1
    s = b.sessions["n"]
    assert s.state == State.WAITING
    assert s.label == "env_monitoring"
    # state_since is absolute, so the age carries across the restart rather
    # than restarting at zero.
    assert b.snapshot(1000 + 3 * 3600)["s"][0]["age"] == 3 * 3600


def test_a_look_row_survives_and_still_graduates(tmp_path):
    """The untracked-work count is not restored, but the row and its clock are,
    so the graduation timer carries on from where it was."""
    p = tmp_path / "sessions.json"
    a = SessionStore()
    a.apply_event(ev("Stop", "o", background_tasks=[
        {"id": "w1", "type": "workflow", "status": "running"}]), 0)
    a.apply_event(ev("Notification", "o", notification_type="idle_prompt"), 10)
    persist.save(p, a, 100)

    b = SessionStore(look_s=300)
    assert persist.load(p, b, 100, 86400.0) == 1
    assert b.sessions["o"].state == State.NEEDS_LOOK
    b.tick(200)
    assert b.sessions["o"].state == State.NEEDS_LOOK
    b.tick(311)
    assert b.sessions["o"].state == State.NEEDS_INPUT


def test_statusline_figures_survive(tmp_path):
    p = tmp_path / "sessions.json"
    a = SessionStore()
    a.apply_event(ev("UserPromptSubmit", "w"), 0)
    a.apply_status({
        "session_id": "w",
        "model": {"display_name": "Opus 5"},
        "cost": {"total_cost_usd": 3.9},
        "context_window": {"used_percentage": 10},
    }, 1)
    persist.save(p, a, 2)

    b = SessionStore()
    persist.load(p, b, 2, 86400.0)
    row = b.snapshot(2)["s"][0]
    assert row["ctx"] == 10 and row["m"] == "opus5"
    assert b.snapshot(2)["cost"] == 3.9


def test_old_sessions_are_dropped_not_restored(tmp_path):
    """Nothing tells the daemon a window closed while it was down, so a row
    that has not moved in longer than the cutoff is treated as a ghost."""
    p = tmp_path / "sessions.json"
    a = SessionStore()
    a.apply_event(ev("PermissionRequest", "fresh", "C:/Repos/a"), 100_000)
    a.apply_event(ev("PermissionRequest", "ghost", "C:/Repos/b"), 0)
    persist.save(p, a, 100_000)

    b = SessionStore()
    assert persist.load(p, b, 100_000, 86400.0) == 1
    assert "fresh" in b.sessions and "ghost" not in b.sessions


def test_ended_sessions_are_not_written(tmp_path):
    p = tmp_path / "sessions.json"
    a = SessionStore()
    a.apply_event(ev("SessionEnd", "gone"), 0)
    a.apply_event(ev("UserPromptSubmit", "live"), 0)
    persist.save(p, a, 1)
    assert [r["session_id"] for r in json.loads(p.read_text())["sessions"]] == ["live"]


def test_a_live_session_is_never_overwritten_by_a_saved_one(tmp_path):
    """Restoring runs at startup, but must not clobber anything already known."""
    p = tmp_path / "sessions.json"
    a = SessionStore()
    a.apply_event(ev("PermissionRequest", "x"), 0)
    persist.save(p, a, 1)

    b = SessionStore()
    b.apply_event(ev("UserPromptSubmit", "x"), 2)
    assert persist.load(p, b, 2, 86400.0) == 0
    assert b.sessions["x"].state == State.WORKING


def test_corrupt_or_missing_state_is_survivable(tmp_path):
    """Bad state on disk must never stop the daemon starting."""
    store = SessionStore()
    assert persist.load(tmp_path / "absent.json", store, 0, 86400.0) == 0

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert persist.load(bad, store, 0, 86400.0) == 0

    wrong_v = tmp_path / "v9.json"
    wrong_v.write_text(json.dumps({"v": 9, "sessions": []}), encoding="utf-8")
    assert persist.load(wrong_v, store, 0, 86400.0) == 0

    unknown = tmp_path / "unknown.json"
    unknown.write_text(json.dumps({
        "v": persist.FORMAT_V, "saved_at": 0,
        "sessions": [{"session_id": "u", "state": "teleporting", "last_event": 0}],
    }), encoding="utf-8")
    assert persist.load(unknown, store, 0, 86400.0) == 0
    assert store.sessions == {}


def test_save_replaces_rather_than_truncates(tmp_path):
    """An interrupted write must not leave a file that fails to load forever."""
    p = tmp_path / "sessions.json"
    a = SessionStore()
    a.apply_event(ev("PermissionRequest", "one"), 0)
    persist.save(p, a, 1)
    a.apply_event(ev("PermissionRequest", "two", "C:/Repos/two"), 1)
    persist.save(p, a, 2)

    assert list(tmp_path.iterdir()) == [p]          # no .tmp left behind
    b = SessionStore()
    assert persist.load(p, b, 2, 86400.0) == 2
