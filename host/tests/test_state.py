"""State machine tests. Payload shapes are provisional until fixtures are captured."""

from beacon_host.state import SessionStore, State


def ev(name: str, sid: str = "abc12345-0000", cwd: str = "C:\\Repos\\session-beacon", **extra):
    return {"hook_event_name": name, "session_id": sid, "cwd": cwd, **extra}


def test_lifecycle():
    st = SessionStore()
    st.apply_event(ev("SessionStart"), 0)
    assert st.sessions["abc12345-0000"].state == State.STARTING
    assert st.sessions["abc12345-0000"].label == "session-beacon"

    st.apply_event(ev("UserPromptSubmit"), 1)
    assert st.sessions["abc12345-0000"].state == State.WORKING

    st.apply_event(ev("Notification", notification_type="permission_prompt"), 2)
    assert st.sessions["abc12345-0000"].state == State.NEEDS_INPUT

    st.apply_event(ev("PreToolUse", tool_name="Bash"), 3)
    assert st.sessions["abc12345-0000"].state == State.WORKING

    st.apply_event(ev("Stop"), 4)
    assert st.sessions["abc12345-0000"].state == State.IDLE

    st.apply_event(ev("SessionEnd"), 5)
    st.tick(5 + 31)
    assert "abc12345-0000" not in st.sessions


def test_stale_and_sort():
    st = SessionStore(stale_after_s=10)
    st.apply_event(ev("UserPromptSubmit", sid="a"), 0)
    st.apply_event(ev("UserPromptSubmit", sid="b", cwd="C:/Repos/x"), 0)
    st.apply_event(ev("Notification", sid="b", notification_type="idle_prompt"), 1)
    st.tick(20)
    assert st.sessions["a"].state == State.STALE
    snap = st.snapshot(20)
    assert [r["st"] for r in snap["s"]] == ["need", "stale"]
    assert snap["s"][0]["age"] == 19
