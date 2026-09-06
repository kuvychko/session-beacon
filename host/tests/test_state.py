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


def test_ctx_pct_falls_back_to_input_tokens_only():
    """used_percentage is null early in a session and after a compaction.

    The fallback counts input tokens only. Output is excluded: everything the
    model has already said comes back as input on the next call, so adding
    total_output_tokens double-counts all but the most recent reply. Here that
    is 40000/200000, not 50000/200000.
    """
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
    assert st.sessions["x"].ctx_pct == 20


def test_ctx_pct_is_none_when_nothing_usable_is_present():
    st = SessionStore()
    st.apply_status({"session_id": "x", "context_window": {"used_percentage": None}}, 0)
    assert st.sessions["x"].ctx_pct is None


def test_permission_mode_captured():
    st = SessionStore()
    st.apply_event(ev("SessionStart", permission_mode="bypassPermissions"), 0)
    assert st.sessions["abc12345-0000"].permission_mode == "bypassPermissions"


def test_label_uses_repo_root_not_transient_cwd(tmp_path):
    """A session's cwd moves as you work. Labelling from it directly meant a
    repo called session-beacon displayed as "host" the moment anything ran in
    its host/ subdirectory, and the wrong label then stuck for the session's
    whole life."""
    repo = tmp_path / "session-beacon"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "host" / "src"
    sub.mkdir(parents=True)

    st = SessionStore()
    st.apply_event(ev("PostToolUse", cwd=str(sub)), 0)
    assert st.snapshot(1)["s"][0]["l"] == "session-beacon"

    # Moving back up must not change it either.
    st.apply_event(ev("PostToolUse", cwd=str(repo)), 2)
    assert st.snapshot(3)["s"][0]["l"] == "session-beacon"


def test_label_updates_when_the_session_changes_repo(tmp_path):
    a, b = tmp_path / "alpha", tmp_path / "beta"
    (a / ".git").mkdir(parents=True)
    (b / ".git").mkdir(parents=True)
    st = SessionStore()
    st.apply_event(ev("PostToolUse", cwd=str(a)), 0)
    assert st.snapshot(1)["s"][0]["l"] == "alpha"
    st.apply_event(ev("PostToolUse", cwd=str(b)), 2)
    assert st.snapshot(3)["s"][0]["l"] == "beta"


def test_label_falls_back_to_basename_outside_a_repo(tmp_path):
    plain = tmp_path / "just-a-folder"
    plain.mkdir()
    st = SessionStore()
    st.apply_event(ev("PostToolUse", cwd=str(plain)), 0)
    assert st.snapshot(1)["s"][0]["l"] == "just-a-folder"


def test_label_override_matches_repo_root_or_exact_cwd(tmp_path):
    repo = tmp_path / "long-project-name"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "sim"
    sub.mkdir()
    root_key = str(repo).replace("\\", "/")

    st = SessionStore(label_overrides={root_key: "fd-research"})
    st.apply_event(ev("PostToolUse", cwd=str(sub)), 0)
    assert st.snapshot(1)["s"][0]["l"] == "fd-research"


STATUS_WITH_RATES = {
    "session_id": "x",
    "model": {"display_name": "Opus 5"},
    "context_window": {"used_percentage": 54},
    "rate_limits": {
        "five_hour": {"used_percentage": 92, "resets_at": 1788571200},
        "seven_day": {"used_percentage": 28.000000000000004, "resets_at": 1788800400},
    },
}


def test_rate_limits_reach_the_snapshot():
    """Account usage was assumed unavailable during scoping; the statusline
    carries it. Shape matches a payload captured from this machine."""
    st = SessionStore()
    st.apply_status(STATUS_WITH_RATES, 100.0)
    snap = st.snapshot(101.0)
    assert snap["rl"] == {"h5": 92, "d7": 28}   # note the float is rounded


def test_rate_limits_expire_rather_than_go_stale():
    """They only refresh while a statusline is running. A percentage from an
    hour ago is worse than none when the point is knowing where you stand."""
    st = SessionStore()
    st.apply_status(STATUS_WITH_RATES, 0.0)
    assert "rl" in st.snapshot(299.0)
    assert "rl" not in st.snapshot(301.0)


def test_rate_limits_absent_when_the_statusline_does_not_carry_them():
    st = SessionStore()
    st.apply_status({"session_id": "x", "context_window": {"used_percentage": 10}}, 0)
    assert "rl" not in st.snapshot(1)


def test_rate_limits_clamped_and_partial_accepted():
    st = SessionStore()
    st.apply_status({"session_id": "x", "rate_limits": {
        "five_hour": {"used_percentage": 130}}}, 0)
    assert st.snapshot(1)["rl"] == {"h5": 100}
