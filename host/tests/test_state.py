"""State machine tests. Payload shapes are provisional until fixtures are captured."""

from beacon_host.state import SessionStore, State, elide_label


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


def test_post_tool_batch_answers_a_prompt_no_tool_ever_ran():
    """A rejected call runs no tool, so PostToolUse never arrives.

    PostToolBatch fires once the batch resolves either way, which is the moment
    the prompt was answered. Without it the row stayed red until Claude's next
    tool call, a minute of thinking later.
    """
    st = SessionStore()
    st.apply_event(ev("UserPromptSubmit"), 0)
    st.apply_event(ev("PermissionRequest", tool_name="ExitPlanMode"), 1)
    assert st.sessions["abc12345-0000"].state == State.NEEDS_INPUT

    st.apply_event(ev("PostToolBatch", tool_calls=[
        {"tool_name": "ExitPlanMode", "tool_use_id": "toolu_1",
         "tool_input": {}, "tool_response": "rejected"}]), 2)
    s = st.sessions["abc12345-0000"]
    assert s.state == State.WORKING
    assert s.last_tool == "ExitPlanMode"


def test_post_tool_batch_names_the_last_tool_and_survives_a_bad_payload():
    """A batch is one entry per call, so the last is the nearest thing to the
    `tool_name` the per-tool events carry. A malformed list must not blank it."""
    st = SessionStore()
    st.apply_event(ev("PostToolBatch", tool_calls=[
        {"tool_name": "Read"}, {"tool_name": "Bash"}]), 0)
    assert st.sessions["abc12345-0000"].last_tool == "Bash"

    st.apply_event(ev("PostToolBatch", tool_calls="nonsense"), 1)
    s = st.sessions["abc12345-0000"]
    assert s.state == State.WORKING
    assert s.last_tool == "Bash"


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


def test_attention_ladder_de_escalates():
    """Waiting on a human pulses, then holds red, then settles to amber."""
    st = SessionStore()
    st.apply_event(ev("PermissionRequest"), 0)
    s = st.sessions["abc12345-0000"]
    assert s.state == State.NEEDS_INPUT

    st.tick(119)
    assert s.state == State.NEEDS_INPUT       # still pulsing just under 2 min
    st.tick(121)
    assert s.state == State.NEEDS_HELD        # static red

    st.tick(599)
    assert s.state == State.NEEDS_HELD        # still red just under 10 min
    st.tick(601)
    assert s.state == State.WAITING           # amber and quiet


def test_ladder_keeps_the_age_running():
    """The rungs must not reset state_since: it is both the displayed age and
    the ladder's own origin, so resetting it would delay `wait` to 12 minutes."""
    st = SessionStore()
    st.apply_event(ev("PermissionRequest"), 0)
    st.tick(121)
    assert st.snapshot(121)["s"][0]["age"] == 121
    st.tick(601)
    assert st.snapshot(601)["s"][0]["age"] == 601


def test_repeat_notification_does_not_restart_the_pulse():
    """idle_prompt re-fires while nobody answers. Re-arming on each one would
    pulse forever, which is exactly what the ladder exists to stop."""
    st = SessionStore()
    st.apply_event(ev("Notification", notification_type="idle_prompt"), 0)
    st.tick(200)
    assert st.sessions["abc12345-0000"].state == State.NEEDS_HELD

    st.apply_event(ev("Notification", notification_type="idle_prompt"), 210)
    assert st.sessions["abc12345-0000"].state == State.NEEDS_HELD
    st.tick(220)
    assert st.snapshot(220)["s"][0]["age"] == 220


def test_ladder_re_arms_after_leaving_the_family():
    """A session that was answered and later blocks again starts over."""
    st = SessionStore()
    st.apply_event(ev("PermissionRequest"), 0)
    st.tick(700)
    assert st.sessions["abc12345-0000"].state == State.WAITING

    st.apply_event(ev("PostToolUse", tool_name="Bash"), 701)
    assert st.sessions["abc12345-0000"].state == State.WORKING
    st.apply_event(ev("PermissionRequest"), 702)
    assert st.sessions["abc12345-0000"].state == State.NEEDS_INPUT


def test_waiting_sorts_below_working():
    """A long-idle session must not crowd a working one off a six-row display."""
    st = SessionStore()
    st.apply_event(ev("PermissionRequest", sid="n", cwd="C:/Repos/n"), 0)
    st.apply_event(ev("PermissionRequest", sid="h", cwd="C:/Repos/h"), 0)
    st.apply_event(ev("PermissionRequest", sid="p", cwd="C:/Repos/p"), 0)
    st.apply_event(ev("StopFailure", sid="e", cwd="C:/Repos/e", error_type="overloaded"), 0)
    st.apply_event(ev("UserPromptSubmit", sid="w", cwd="C:/Repos/w"), 0)
    # Walk h and p down the ladder, then bring n back to the top rung.
    st.tick(601)
    st.apply_event(ev("PermissionRequest", sid="h"), 601)   # no re-arm: stays wait
    st.sessions["h"].state = State.NEEDS_HELD               # place one on rung 2
    st.apply_event(ev("PostToolUse", sid="n", tool_name="Bash"), 601)
    st.apply_event(ev("PermissionRequest", sid="n"), 602)
    st.apply_event(ev("UserPromptSubmit", sid="w"), 602)

    assert [r["st"] for r in st.snapshot(603)["s"]] == [
        "need", "held", "err", "work", "wait"]


def test_stop_with_background_work_is_not_idle():
    """A turn that ends with an agent still running is waiting on the machine."""
    st = SessionStore()
    st.apply_event(ev("UserPromptSubmit"), 0)
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "agent_type": "Explore",
         "status": "running", "description": "<str len=23>"}]), 1)
    assert st.sessions["abc12345-0000"].state == State.WORKING

    # The agent finishes, the turn really ends, and the session goes idle.
    st.apply_event(ev("Stop", background_tasks=[]), 2)
    assert st.sessions["abc12345-0000"].state == State.IDLE


def test_idle_prompt_is_suppressed_while_background_work_runs():
    """The false red: nothing for a human to do, so nothing should go red."""
    st = SessionStore()
    st.apply_event(ev("UserPromptSubmit"), 0)
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "running"}]), 1)
    st.apply_event(ev("Notification", notification_type="idle_prompt"), 2)
    assert st.sessions["abc12345-0000"].state == State.WORKING

    # Once the background work is done, the same notification does escalate.
    st.apply_event(ev("Stop", background_tasks=[]), 3)
    st.apply_event(ev("Notification", notification_type="idle_prompt"), 4)
    assert st.sessions["abc12345-0000"].state == State.NEEDS_INPUT


def test_real_asks_are_never_suppressed():
    """A permission prompt raised inside a subagent still needs answering."""
    for kind in ("permission_prompt", "agent_needs_input", "elicitation_dialog"):
        st = SessionStore()
        st.apply_event(ev("Stop", background_tasks=[
            {"id": "a1", "type": "subagent", "status": "running"}]), 0)
        st.apply_event(ev("Notification", notification_type=kind), 1)
        assert st.sessions["abc12345-0000"].state == State.NEEDS_INPUT, kind

    st = SessionStore()
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "running"}]), 0)
    st.apply_event(ev("PermissionRequest", tool_name="Bash"), 1)
    assert st.sessions["abc12345-0000"].state == State.NEEDS_INPUT


def test_finished_background_tasks_do_not_count():
    st = SessionStore()
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "completed"},
        {"id": "a2", "type": "subagent", "status": "failed"}]), 0)
    assert st.sessions["abc12345-0000"].state == State.IDLE


def test_a_task_with_no_status_counts_as_running():
    """The unknown case takes the quieter side: assuming a task had finished is
    what produces the false alarm."""
    st = SessionStore()
    st.apply_event(ev("Stop", background_tasks=[{"id": "a1", "type": "subagent"}]), 0)
    assert st.sessions["abc12345-0000"].state == State.WORKING


def test_a_new_prompt_forgets_the_previous_turns_tasks():
    st = SessionStore()
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "running"}]), 0)
    st.apply_event(ev("UserPromptSubmit"), 1)
    assert st.sessions["abc12345-0000"].bg_tasks == 0
    st.apply_event(ev("Notification", notification_type="idle_prompt"), 2)
    assert st.sessions["abc12345-0000"].state == State.NEEDS_INPUT


def test_malformed_background_tasks_are_ignored():
    """Never trust the payload's shape: a bad value must not crash the daemon."""
    for bad in ("nonsense", 3, {"a": 1}, [None, 7, "x"], None):
        st = SessionStore()
        st.apply_event(ev("Stop", background_tasks=bad), 0)
        assert st.sessions["abc12345-0000"].state == State.IDLE, repr(bad)


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


# ---- subagent attribution -------------------------------------------------
#
# A subagent's hook events carry the *parent's* session_id, so the only thing
# separating them is `agent_id`. Treating them as the parent's own is what let a
# session sit blocked on a human while its row showed WORKING.


def test_a_subagents_tool_call_does_not_answer_the_parents_prompt():
    """The reported bug: the parent is blocked, a subagent keeps working, and
    every one of its tool calls repainted the red row blue."""
    st = SessionStore()
    st.apply_event(ev("UserPromptSubmit"), 0)
    st.apply_event(ev("PermissionRequest", tool_name="Bash"), 1)
    assert st.sessions["abc12345-0000"].state == State.NEEDS_INPUT

    st.apply_event(ev("PostToolUse", tool_name="Grep", agent_id="a1",
                      agent_type="Explore"), 2)
    st.apply_event(ev("PostToolBatch", agent_id="a1", tool_calls=[
        {"tool_name": "Grep"}]), 3)
    assert st.sessions["abc12345-0000"].state == State.NEEDS_INPUT


def test_a_subagents_tool_call_still_keeps_the_session_off_stale():
    """It refreshes the timer even though it does not change the state. A long
    subagent run is the only traffic its session produces, so dropping the event
    outright would send an actively working parent to STALE."""
    st = SessionStore(stale_after_s=10)
    st.apply_event(ev("UserPromptSubmit"), 0)
    for t in range(1, 30, 5):
        st.apply_event(ev("PostToolUse", tool_name="Read", agent_id="a1"), t)
        st.tick(t)
    assert st.sessions["abc12345-0000"].state == State.WORKING


def test_a_subagent_answers_the_prompt_it_raised_itself():
    """A permission prompt raised inside a subagent is answered by that
    subagent's own PostToolBatch, and nothing else arrives to say so."""
    st = SessionStore()
    st.apply_event(ev("UserPromptSubmit"), 0)
    st.apply_event(ev("PermissionRequest", tool_name="Bash", agent_id="a1"), 1)
    s = st.sessions["abc12345-0000"]
    assert s.state == State.NEEDS_INPUT and s.attn_agent == "a1"

    # Another agent's work is not an answer.
    st.apply_event(ev("PostToolUse", tool_name="Read", agent_id="a2"), 2)
    assert s.state == State.NEEDS_INPUT
    # The parent's own is not either: it never saw the prompt.
    st.apply_event(ev("PostToolUse", tool_name="Read"), 3)
    assert s.state == State.NEEDS_INPUT

    st.apply_event(ev("PostToolBatch", agent_id="a1", tool_calls=[
        {"tool_name": "Bash"}]), 4)
    assert s.state == State.WORKING


def test_agent_type_alone_is_the_main_thread():
    """`agent_type` is also set on the main thread of a session started with
    --agent, without `agent_id`. Filtering on it would ignore a real session's
    every tool call, so only `agent_id` may be used."""
    st = SessionStore()
    st.apply_event(ev("PermissionRequest"), 0)
    st.apply_event(ev("PostToolUse", tool_name="Bash", agent_type="code-reviewer"), 1)
    assert st.sessions["abc12345-0000"].state == State.WORKING


# ---- background work resolves without another Stop ------------------------


def test_subagent_stop_resyncs_the_count_and_hands_back_to_the_user():
    """The turn already ended, so no further Stop is coming. SubagentStop is the
    only thing that can retire the count."""
    st = SessionStore()
    st.apply_event(ev("UserPromptSubmit"), 0)
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "running"}]), 1)
    s = st.sessions["abc12345-0000"]
    assert s.state == State.WORKING and s.bg_tasks == 1

    st.apply_event(ev("SubagentStop", agent_id="a1", background_tasks=[]), 2)
    assert s.bg_tasks == 0
    assert s.state == State.IDLE


def test_subagent_stop_does_not_count_the_agent_it_is_reporting():
    """Its own payload may still list it as running; counting it would put the
    stale count straight back."""
    st = SessionStore()
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "running"}]), 0)
    st.apply_event(ev("SubagentStop", agent_id="a1", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "running"}]), 1)
    s = st.sessions["abc12345-0000"]
    assert s.bg_tasks == 0 and s.state == State.IDLE


def test_subagent_stop_leaves_the_row_alone_while_others_run():
    st = SessionStore()
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "running"},
        {"id": "a2", "type": "subagent", "status": "running"}]), 0)
    st.apply_event(ev("SubagentStop", agent_id="a1", background_tasks=[
        {"id": "a2", "type": "subagent", "status": "running"}]), 1)
    s = st.sessions["abc12345-0000"]
    assert s.bg_tasks == 1 and s.state == State.WORKING


def test_subagent_stop_without_background_tasks_decrements():
    """The field is optional in the CLI's schema. An absent list must not read
    as "nothing left running"."""
    st = SessionStore()
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "running"},
        {"id": "a2", "type": "subagent", "status": "running"}]), 0)
    st.apply_event(ev("SubagentStop", agent_id="a1"), 1)
    assert st.sessions["abc12345-0000"].bg_tasks == 1
    st.apply_event(ev("SubagentStop", agent_id="a2"), 2)
    assert st.sessions["abc12345-0000"].bg_tasks == 0
    # And it floors at zero rather than going negative.
    st.apply_event(ev("SubagentStop", agent_id="a3"), 3)
    assert st.sessions["abc12345-0000"].bg_tasks == 0


def test_a_subagent_finishing_mid_turn_does_not_go_idle():
    """Only a turn that had already ended hands back to the user."""
    st = SessionStore()
    st.apply_event(ev("UserPromptSubmit"), 0)
    st.apply_event(ev("SubagentStop", agent_id="a1", background_tasks=[]), 1)
    assert st.sessions["abc12345-0000"].state == State.WORKING


def test_a_stale_background_count_stops_suppressing_idle_prompt():
    """The other half of the bug. The count used to be recomputed only on the
    next Stop, and a turn that has already ended may never produce one, so a
    stale count swallowed every idle_prompt the session would ever send."""
    st = SessionStore(bg_quiet_s=180)
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "running"}]), 0)

    # Inside the window the hold still applies: this is the false red it exists
    # to prevent.
    st.apply_event(ev("Notification", notification_type="idle_prompt"), 100)
    st.tick(100)
    assert st.sessions["abc12345-0000"].state == State.WORKING

    # Past it, with nothing having confirmed the count since, it escalates --
    # and now without needing a second notification to arrive.
    st.tick(400)
    assert st.sessions["abc12345-0000"].state == State.NEEDS_INPUT


def test_a_running_subagent_keeps_the_hold_alive():
    """A subagent emits hook events constantly, and each one is fresh evidence
    that the count is real, so a genuinely busy session is never escalated."""
    st = SessionStore(bg_quiet_s=180)
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "running"}]), 0)
    for t in range(100, 600, 100):
        st.apply_event(ev("PostToolUse", tool_name="Read", agent_id="a1"), t)
    st.apply_event(ev("Notification", notification_type="idle_prompt"), 600)
    assert st.sessions["abc12345-0000"].state == State.WORKING


# ---- what is in flight, and what is merely registered ---------------------
#
# `background_tasks` lists every kind of in-flight work, not just subagents.
# Counting all of it is what let a session sit blue and then amber for as long
# as it was left, having finished its turn eight minutes earlier.


def test_an_armed_monitor_is_not_work_in_flight():
    """The reported bug, in one test.

    A session with two armed artifact comment monitors and no subagents at all
    showed `work` for eight minutes and then `stale`, never red. A monitor is a
    subscription registered for the life of the session, so the count could only
    ever go up and the single idle_prompt Claude Code sent was swallowed.
    """
    st = SessionStore()
    st.apply_event(ev("UserPromptSubmit"), 0)
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "s1a2b3c4d", "type": "monitor", "status": "running",
         "description": "<str len=31>"},
        {"id": "s5e6f7a8b", "type": "monitor", "status": "running",
         "description": "<str len=27>"}]), 1)
    s = st.sessions["abc12345-0000"]
    assert s.state == State.IDLE and s.bg_tasks == 0 and s.turn_over is False

    st.apply_event(ev("Notification", notification_type="idle_prompt"), 2)
    assert s.state == State.NEEDS_INPUT
    st.tick(700)
    assert s.state == State.WAITING


def test_only_a_subagent_holds_a_session_back():
    """Nothing reports the end of the other types, so counting one can only ever
    mute the display. A subagent is retired by SubagentStop."""
    for kind in ("monitor", "shell", "workflow", "MCP task", "teammate",
                 "cloud session", "some-future-type"):
        st = SessionStore()
        st.apply_event(ev("Stop", background_tasks=[
            {"id": "x1", "type": kind, "status": "running"}]), 0)
        assert st.sessions["abc12345-0000"].state == State.IDLE, kind

    st = SessionStore()
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "running"}]), 0)
    assert st.sessions["abc12345-0000"].state == State.WORKING


def test_a_subagent_among_monitors_is_still_counted():
    st = SessionStore()
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "s1", "type": "monitor", "status": "running"},
        {"id": "a1", "type": "subagent", "status": "running"},
        {"id": "s2", "type": "monitor", "status": "running"}]), 0)
    s = st.sessions["abc12345-0000"]
    assert s.bg_tasks == 1 and s.state == State.WORKING


def test_a_pending_subagent_counts():
    """Claude Code lists in-flight work as `running` or `pending`; comparing
    against "running" alone dropped a subagent that had not started yet."""
    st = SessionStore()
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "pending"}]), 0)
    assert st.sessions["abc12345-0000"].state == State.WORKING


# ---- a held idle_prompt is not a lost one ---------------------------------


def test_a_held_idle_prompt_is_released_when_the_hold_expires():
    """Claude Code sent exactly one idle_prompt for the idle period that turned
    this up, so dropping it meant the row never went red however long it was
    left. It is remembered instead, and tick() releases it."""
    st = SessionStore(bg_quiet_s=180)
    st.apply_event(ev("UserPromptSubmit"), 0)
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "running"}]), 1)
    s = st.sessions["abc12345-0000"]

    st.apply_event(ev("Notification", notification_type="idle_prompt"), 176)
    st.tick(176)
    assert s.state == State.WORKING and s.idle_held

    st.tick(182)
    assert s.state == State.NEEDS_INPUT and not s.idle_held
    assert s.bg_tasks == 0 and s.turn_over is False


def test_the_notification_may_land_either_side_of_the_window():
    """The three-second margin that made this a coin flip. Both orderings now
    reach the same place, within a tick of each other."""
    for at in (176.0, 184.0):
        st = SessionStore(bg_quiet_s=180)
        st.apply_event(ev("Stop", background_tasks=[
            {"id": "a1", "type": "subagent", "status": "running"}]), 1)
        st.apply_event(ev("Notification", notification_type="idle_prompt"), at)
        st.tick(max(at, 182))
        assert st.sessions["abc12345-0000"].state == State.NEEDS_INPUT, at


def test_a_held_idle_prompt_is_released_the_moment_the_last_subagent_stops():
    """It does not have to wait the window out when something retires the count
    first."""
    st = SessionStore()
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "running"}]), 0)
    st.apply_event(ev("Notification", notification_type="idle_prompt"), 10)
    st.tick(10)
    s = st.sessions["abc12345-0000"]
    assert s.state == State.WORKING

    st.apply_event(ev("SubagentStop", agent_id="a1", background_tasks=[]), 20)
    st.tick(20)
    assert s.state == State.NEEDS_INPUT


def test_a_held_idle_prompt_is_forgotten_when_the_session_gets_going_again():
    """It describes a turn that has since been superseded."""
    for name, extra in (("UserPromptSubmit", {}),
                        ("PostToolUse", {"tool_name": "Bash"})):
        st = SessionStore()
        st.apply_event(ev("Stop", background_tasks=[
            {"id": "a1", "type": "subagent", "status": "running"}]), 0)
        st.apply_event(ev("Notification", notification_type="idle_prompt"), 10)
        st.apply_event(ev(name, **extra), 20)
        st.tick(1000)
        assert st.sessions["abc12345-0000"].state != State.NEEDS_INPUT, name


def test_a_subagents_tool_call_does_not_forget_a_held_idle_prompt():
    """The parent's turn is over; the subagent working is the reason the
    notification is being held in the first place."""
    st = SessionStore(bg_quiet_s=180)
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "running"}]), 0)
    st.apply_event(ev("Notification", notification_type="idle_prompt"), 10)
    st.apply_event(ev("PostToolUse", tool_name="Grep", agent_id="a1"), 20)
    st.tick(20)
    s = st.sessions["abc12345-0000"]
    assert s.idle_held and s.state == State.WORKING

    # The agent goes quiet, the hold expires, and the notification lands.
    st.tick(300)
    assert s.state == State.NEEDS_INPUT


def test_a_finished_turn_falls_to_idle_not_stale():
    """`stale` is the worst answer available: amber sorts *below* `work`, so a
    session waiting on you would be quieter than one being wrong about it."""
    st = SessionStore(stale_after_s=100, bg_quiet_s=1000)
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "running"}]), 0)
    st.tick(200)
    assert st.sessions["abc12345-0000"].state == State.IDLE

    # A session that never ended its turn is still stale, as before.
    st2 = SessionStore(stale_after_s=100)
    st2.apply_event(ev("UserPromptSubmit"), 0)
    st2.tick(200)
    assert st2.sessions["abc12345-0000"].state == State.STALE


def test_the_hold_expiring_does_not_disturb_a_session_mid_turn():
    """turn_over is what makes the row IDLE. A count left over from a turn that
    has since been superseded must not end the new one for it."""
    st = SessionStore(bg_quiet_s=180)
    st.apply_event(ev("UserPromptSubmit"), 0)
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "running"}]), 1)
    st.apply_event(ev("UserPromptSubmit"), 2)     # a new turn starts
    st.tick(300)
    assert st.sessions["abc12345-0000"].state == State.WORKING


# ---- /health ---------------------------------------------------------------


def test_background_report_names_the_session_being_held_back():
    """None of this was visible from outside, and a row held behind a count
    looks exactly like a session that is genuinely busy."""
    st = SessionStore()
    assert st.background_report(0) == []

    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "running"}]), 100)
    st.apply_event(ev("Notification", notification_type="idle_prompt"), 110)
    assert st.background_report(150) == [{
        "id": "abc12345", "l": "session-beacon", "tasks": 1, "other": 0,
        "turn_over": True, "idle_held": True, "bg_seen_age_s": 50.0,
    }]

    # And it goes quiet again once there is nothing outstanding.
    st.tick(400)
    assert st.background_report(400) == []


def test_background_report_shows_untracked_work_and_elides_the_label():
    st = SessionStore()
    st.apply_event(ev("Stop", cwd="C:/nowhere/inventory-service2", background_tasks=[
        {"id": "w1", "type": "workflow", "status": "running"}]), 100)
    [row] = st.background_report(100)
    assert row["other"] == 1 and row["tasks"] == 0
    assert row["l"] == "inventory..vice2"


# ---- NEEDS_LOOK: an idle_prompt with untracked work still in flight -------
#
# An orchestrator waiting on a `workflow` task went filled red and pulsing,
# the treatment a permission prompt gets, with nothing blocked at all. The
# subagent count cannot widen to cover it (that is the monitor bug), so a
# second signal softens the notification instead, and a timer backs it up.

WORKFLOW = [{"id": "w1", "type": "workflow", "status": "running"}]


def test_an_orchestrator_waiting_on_a_workflow_is_not_red():
    """The incident, in one test."""
    st = SessionStore()
    st.apply_event(ev("UserPromptSubmit"), 0)
    st.apply_event(ev("Stop", background_tasks=WORKFLOW), 1)
    s = st.sessions["abc12345-0000"]
    # The WORKING/IDLE decision is untouched: only a subagent keeps a row busy.
    assert s.state == State.IDLE and s.bg_tasks == 0 and s.bg_other == 1

    st.apply_event(ev("Notification", notification_type="idle_prompt"), 60)
    st.tick(60)
    assert s.state == State.NEEDS_LOOK
    assert st.snapshot(60)["s"][0]["st"] == "look"


def test_look_graduates_onto_the_ladder_and_starts_it_fresh():
    """Nothing reports that a workflow ended, so the soft row cannot wait for
    proof. Left alone it lands where an unsoftened idle_prompt would have."""
    st = SessionStore(look_s=300, need_pulse_s=120, need_red_s=600)
    st.apply_event(ev("Stop", background_tasks=WORKFLOW), 0)
    st.apply_event(ev("Notification", notification_type="idle_prompt"), 10)
    s = st.sessions["abc12345-0000"]

    st.tick(300)
    assert s.state == State.NEEDS_LOOK
    st.tick(311)
    assert s.state == State.NEEDS_INPUT
    assert st.snapshot(311)["s"][0]["age"] == 0

    # The ladder then runs its full length from the graduation, not from the
    # notification.
    st.tick(311 + 100)
    assert s.state == State.NEEDS_INPUT
    st.tick(311 + 121)
    assert s.state == State.NEEDS_HELD


def test_a_repeat_idle_prompt_does_not_restart_the_look_timer():
    """A timer that restarts on every notification never runs out."""
    st = SessionStore(look_s=300)
    st.apply_event(ev("Stop", background_tasks=WORKFLOW), 0)
    st.apply_event(ev("Notification", notification_type="idle_prompt"), 10)
    st.apply_event(ev("Notification", notification_type="idle_prompt"), 250)
    st.tick(311)
    assert st.sessions["abc12345-0000"].state == State.NEEDS_INPUT


def test_look_clears_when_the_session_picks_up_again():
    for name, extra in (("PostToolUse", {"tool_name": "Bash"}),
                        ("PostToolBatch", {}),
                        ("UserPromptSubmit", {}),
                        ("Stop", {})):
        st = SessionStore()
        st.apply_event(ev("Stop", background_tasks=WORKFLOW), 0)
        st.apply_event(ev("Notification", notification_type="idle_prompt"), 10)
        st.apply_event(ev(name, **extra), 20)
        s = st.sessions["abc12345-0000"]
        assert s.state in (State.WORKING, State.IDLE), name
        st.tick(1000)
        assert s.state not in (State.NEEDS_INPUT, State.NEEDS_HELD), name


def test_a_direct_ask_is_as_urgent_as_ever_with_a_workflow_running():
    """Only idle_prompt is softened. A permission prompt needs answering now,
    whatever else is going on, including from a row that is already cyan."""
    for name, extra in (("PermissionRequest", {"tool_name": "Bash"}),
                        ("Notification", {"notification_type": "permission_prompt"}),
                        ("Notification", {"notification_type": "elicitation_dialog"})):
        st = SessionStore()
        st.apply_event(ev("Stop", background_tasks=WORKFLOW), 0)
        st.apply_event(ev(name, **extra), 5)
        assert st.sessions["abc12345-0000"].state == State.NEEDS_INPUT, name

        st = SessionStore()
        st.apply_event(ev("Stop", background_tasks=WORKFLOW), 0)
        st.apply_event(ev("Notification", notification_type="idle_prompt"), 5)
        st.apply_event(ev(name, **extra), 6)
        assert st.sessions["abc12345-0000"].state == State.NEEDS_INPUT, name


def test_work_that_never_ends_does_not_soften():
    """A monitor is a subscription for the life of the session; `dream` and
    `auto-mode scan` are Claude Code's own housekeeping. Softening on them would
    delay every alarm the session ever raises."""
    for kind in ("monitor", "dream", "auto-mode scan"):
        st = SessionStore()
        st.apply_event(ev("Stop", background_tasks=[
            {"id": "x1", "type": kind, "status": "running"}]), 0)
        st.apply_event(ev("Notification", notification_type="idle_prompt"), 5)
        assert st.sessions["abc12345-0000"].state == State.NEEDS_INPUT, kind


def test_untracked_work_of_any_other_kind_softens():
    """Unknown types count here, unlike in the subagent count, because a wrong
    guess costs at most look_s."""
    for kind in ("workflow", "shell", "MCP task", "teammate", "cloud session",
                 "some-future-type"):
        st = SessionStore()
        st.apply_event(ev("Stop", background_tasks=[
            {"id": "x1", "type": kind, "status": "pending"}]), 0)
        st.apply_event(ev("Notification", notification_type="idle_prompt"), 5)
        assert st.sessions["abc12345-0000"].state == State.NEEDS_LOOK, kind


def test_a_held_idle_prompt_released_with_a_workflow_left_goes_to_look():
    """A subagent can retire while a workflow is still listed. The released
    notification goes through the same severity check as a fresh one."""
    st = SessionStore()
    st.apply_event(ev("Stop", background_tasks=[
        {"id": "a1", "type": "subagent", "status": "running"}, *WORKFLOW]), 0)
    st.apply_event(ev("Notification", notification_type="idle_prompt"), 10)
    s = st.sessions["abc12345-0000"]
    assert s.state == State.WORKING and s.idle_held

    st.apply_event(ev("SubagentStop", agent_id="a1", background_tasks=WORKFLOW), 20)
    st.tick(20)
    assert s.state == State.NEEDS_LOOK


def test_look_sorts_above_work_and_below_err():
    st = SessionStore()
    st.apply_event(ev("UserPromptSubmit", sid="w"), 0)
    st.apply_event(ev("Stop", sid="l", background_tasks=WORKFLOW), 0)
    st.apply_event(ev("Notification", sid="l", notification_type="idle_prompt"), 1)
    st.apply_event(ev("StopFailure", sid="e", error_type="rate_limit"), 2)
    assert [r["st"] for r in st.snapshot(3)["s"]] == ["err", "look", "work"]


# ---- labels are elided in the middle ---------------------------------------


def test_elide_label_keeps_both_ends():
    assert elide_label("homelab") == "homelab"
    assert elide_label("sixteen-chars-ok") == "sixteen-chars-ok"
    out = elide_label("inventory-service2")
    assert out == "inventory..vice2" and len(out) == 16
    assert len(elide_label("x" * 17)) == 16


def test_clones_with_a_long_shared_prefix_stay_distinguishable():
    """A prefix cut showed all three as the same sixteen characters."""
    clones = ("averylongprojectname", "averylongprojectname2", "averylongprojectname3")
    st = SessionStore()
    for i, name in enumerate(clones):
        st.apply_event(ev("UserPromptSubmit", sid=str(i), cwd=f"C:/nowhere/{name}"), i)
    labels = [r["l"] for r in st.snapshot(5)["s"]]
    assert len(set(labels)) == 3 and all(len(label) == 16 for label in labels)


def test_a_long_override_is_elided_too():
    st = SessionStore(label_overrides={"C:/nowhere/x": "an-override-that-is-long"})
    st.apply_event(ev("UserPromptSubmit", cwd="C:/nowhere/x"), 0)
    assert st.snapshot(1)["s"][0]["l"] == "an-overri..-long"
