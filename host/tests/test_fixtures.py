"""Replay real Claude Code hook payloads through the state machine.

The payloads in tests/fixtures/hook_payloads.jsonl were captured from this
machine by running the daemon with --capture, not written by hand. That matters:
the published schema this project was originally built against disagrees with
what Claude Code 2.1.261 actually sends, and only a captured payload catches
that. See docs/claude-code-integration.md.

Conversation content is stripped at capture time, so the fixtures carry field
names and shapes and none of the text.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from beacon_host.capture import redact
from beacon_host.state import SessionStore, State

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURES = FIXTURES_DIR / "hook_payloads.jsonl"
BACKGROUND_STOP = FIXTURES_DIR / "stop_with_background_tasks.json"


def load() -> dict[str, dict]:
    lines = FIXTURES.read_text(encoding="utf-8").splitlines()
    payloads = [json.loads(line) for line in lines if line.strip()]
    return {p["hook_event_name"]: p for p in payloads}


@pytest.fixture(scope="module")
def events() -> dict[str, dict]:
    return load()


def test_fixtures_cover_the_observed_lifecycle(events):
    assert set(events) == {
        "SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "SessionEnd"
    }


def test_every_payload_has_the_fields_the_state_machine_reads(events):
    """session_id, cwd and hook_event_name are the three the store depends on."""
    for name, p in events.items():
        assert p.get("session_id"), name
        assert p.get("cwd"), name
        assert p.get("hook_event_name") == name


def test_real_lifecycle_drives_the_expected_states(events):
    """The sequence a session actually produces, replayed end to end."""
    store = SessionStore()
    sid = events["SessionStart"]["session_id"]

    store.apply_event(events["SessionStart"], 0)
    assert store.sessions[sid].state == State.STARTING
    assert store.sessions[sid].label == "session-beacon"

    store.apply_event(events["UserPromptSubmit"], 1)
    assert store.sessions[sid].state == State.WORKING
    # permission_mode rides along on most events and is captured for later use.
    assert store.sessions[sid].permission_mode == "default"

    store.apply_event(events["Stop"], 2)
    assert store.sessions[sid].state == State.IDLE

    store.apply_event(events["SessionEnd"], 3)
    assert store.sessions[sid].state == State.ENDED
    store.tick(3 + 31)
    assert sid not in store.sessions


def test_real_post_tool_use_marks_work_and_records_the_tool(events):
    """Everything in the payload that does not touch the filesystem."""
    store = SessionStore()
    p = events["PostToolUse"]
    store.apply_event(p, 0)
    s = store.sessions[p["session_id"]]
    assert s.state == State.WORKING
    assert s.last_tool == "Bash"
    assert s.permission_mode == "auto"
    # The captured cwd is a subdirectory of the repo, which is what makes the
    # label test below worth doing.
    assert s.cwd.replace("\\", "/").endswith("/host")


def test_real_payload_label_resolves_to_the_repository(events, tmp_path):
    """The captured cwd is a real absolute path from the machine that recorded
    it, and label resolution walks the filesystem looking for a `.git`. Replaying
    the payload as-is therefore passes only on that one machine: anywhere else
    the path does not exist, the walk finds nothing, and the label falls back to
    the directory basename `host`.

    So rebuild that shape under tmp_path and repoint the payload at it. The code
    path exercised is identical; the filesystem it walks is now the test's own.
    """
    repo = tmp_path / "session-beacon"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "host"
    sub.mkdir()

    payload = {**events["PostToolUse"], "cwd": str(sub)}
    store = SessionStore()
    store.apply_event(payload, 0)

    assert store.sessions[payload["session_id"]].label == "session-beacon"


def test_fixtures_carry_no_conversation_content(events):
    """A guard against a future capture change leaking transcripts into the repo.
    Redacted values are markers like '<str len=22>', never the text itself."""
    for f in sorted(FIXTURES_DIR.iterdir()):
        blob = f.read_text(encoding="utf-8")
        for leak in ("igork", "AppData", "C:\\\\Users"):
            assert leak not in blob, f"{f.name} leaks {leak!r}"

    for name, p in events.items():
        for field in ("prompt", "user_prompt", "last_assistant_message", "tool_response"):
            if field in p:
                assert str(p[field]).startswith("<"), f"{name}.{field} is not redacted"
        for k, v in (p.get("tool_input") or {}).items():
            assert str(v).startswith("<"), f"{name}.tool_input.{k} is not redacted"


def test_redact_is_idempotent(events):
    """Re-redacting a fixture must not corrupt it, so fixtures can be refreshed
    from a capture file without special-casing."""
    for p in events.values():
        assert redact(p) == p


@pytest.fixture(scope="module")
def background_stop() -> dict:
    return json.loads(BACKGROUND_STOP.read_text(encoding="utf-8"))


def test_background_stop_fixture_is_a_real_capture(background_stop):
    """Captured from this machine by ending a turn with a subagent running.

    The empty list in hook_payloads.jsonl showed the field existed but not what
    a populated one looks like, which is why the fix waited on this rather than
    guessing the shape.
    """
    tasks = background_stop["background_tasks"]
    assert background_stop["hook_event_name"] == "Stop"
    assert tasks and tasks[0]["status"] == "running"
    assert tasks[0]["type"] == "subagent"
    # Free text is stripped; the fields the state machine reads survive.
    assert tasks[0]["description"].startswith("<")


def test_a_turn_ending_on_a_running_agent_does_not_go_idle(background_stop, events):
    """The false red, replayed from real payloads end to end."""
    store = SessionStore()
    store.apply_event(events["UserPromptSubmit"], 0)
    sid = events["UserPromptSubmit"]["session_id"]

    store.apply_event({**background_stop, "session_id": sid}, 1)
    assert store.sessions[sid].state == State.WORKING

    store.apply_event({"hook_event_name": "Notification", "session_id": sid,
                       "cwd": background_stop["cwd"],
                       "notification_type": "idle_prompt"}, 2)
    assert store.sessions[sid].state == State.WORKING

    # The real Stop from the earlier capture, with the list empty, ends the turn.
    store.apply_event({**events["Stop"], "session_id": sid}, 3)
    assert store.sessions[sid].state == State.IDLE


def test_background_stop_fixture_is_already_redacted(background_stop):
    assert redact(background_stop) == background_stop
