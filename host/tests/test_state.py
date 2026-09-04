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


def test_permission_request_and_denied():
    """PermissionRequest is a dedicated event, more precise than Notification."""
    st = SessionStore()
    st.apply_event(ev("UserPromptSubmit"), 0)
    st.apply_event(ev("PermissionRequest", tool_name="Bash"), 1)
    assert st.sessions["abc12345-0000"].state == State.NEEDS_INPUT
    st.apply_event(ev("PermissionDenied", tool_name="Bash"), 2)
    assert st.sessions["abc12345-0000"].state == State.WORKING


def test_stop_failure_is_error_not_stale():
    """A rate-limited turn must not masquerade as a busy session."""
    st = SessionStore()
    st.apply_event(ev("UserPromptSubmit"), 0)
    st.apply_event(ev("StopFailure", error_type="rate_limit"), 1)
    s = st.sessions["abc12345-0000"]
    assert s.state == State.ERROR
    assert s.error_type == "rate_limit"
    # A new prompt clears it.
    st.apply_event(ev("UserPromptSubmit"), 2)
    assert st.sessions["abc12345-0000"].state == State.WORKING
    assert st.sessions["abc12345-0000"].error_type == ""


def test_attention_notification_types():
    st = SessionStore()
    for i, kind in enumerate(["permission_prompt", "idle_prompt", "agent_needs_input"]):
        sid = f"s{i}"
        st.apply_event(ev("Notification", sid=sid, notification_type=kind), 0)
        assert st.sessions[sid].state == State.NEEDS_INPUT, kind
    st.apply_event(ev("Notification", sid="quiet", notification_type="auth_success"), 0)
    assert st.sessions["quiet"].state == State.STARTING


def test_error_sorts_above_working_below_needs_input():
    st = SessionStore()
    st.apply_event(ev("UserPromptSubmit", sid="w", cwd="C:/Repos/w"), 0)
    st.apply_event(ev("StopFailure", sid="e", cwd="C:/Repos/e", error_type="overloaded"), 0)
    st.apply_event(ev("PermissionRequest", sid="n", cwd="C:/Repos/n"), 0)
    assert [r["st"] for r in st.snapshot(1)["s"]] == ["need", "err", "work"]


def test_ctx_pct_from_statusline():
    st = SessionStore()
    st.apply_status({
        "session_id": "x",
        "workspace": {"current_dir": "C:/Repos/session-beacon"},
        "model": {"display_name": "Claude Opus 5"},
        "cost": {"total_cost_usd": 1.5},
        "context_window": {"used_percentage": 62.7, "context_window_size": 200000},
    }, 0)
    s = st.sessions["x"]
    assert s.ctx_pct == 62 and s.model == "opus5" and s.cost_usd == 1.5


def test_ctx_pct_falls_back_to_token_counts():
    """used_percentage is null early in a session and after a compaction."""
    st = SessionStore()
    st.apply_status({
        "session_id": "x",
        "context_window": {
            "used_percentage": None,
            "total_input_tokens": 40000,
            "total_output_tokens": 10000,
            "context_window_size": 200000,
        },
    }, 0)
    assert st.sessions["x"].ctx_pct == 25


def test_permission_mode_captured():
    st = SessionStore()
    st.apply_event(ev("SessionStart", permission_mode="bypassPermissions"), 0)
    assert st.sessions["abc12345-0000"].permission_mode == "bypassPermissions"
