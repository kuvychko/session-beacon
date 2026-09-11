"""Session state machine. Pure logic, no I/O, so it is unit-testable with fixtures.

See docs/architecture.md for the state diagram and docs/protocol.md for the
snapshot shape produced by SessionStore.snapshot().
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class State(StrEnum):
    STARTING = "start"
    WORKING = "work"
    NEEDS_INPUT = "need"
    NEEDS_HELD = "held"
    NEEDS_LOOK = "look"
    WAITING = "wait"
    ERROR = "err"
    IDLE = "idle"
    STALE = "stale"
    ENDED = "end"


# The three rungs a session waiting on a human descends through, loudest first.
# Entering the family starts the ladder; re-entering it while already inside
# does not, or a prompt that re-notifies would pulse forever.
ATTENTION_STATES = (State.NEEDS_INPUT, State.NEEDS_HELD, State.WAITING)


# Display priority: lower sorts first.
#
# WAITING sits *below* WORKING, next to STALE, even though it still wants a
# human. It renders identically to STALE and it is the state sessions left idle
# on purpose settle into, so ranking it with the alarm states would park them in
# the top rows permanently and push actively working sessions off a six-row
# display. That is the noise the ladder exists to remove, not to relocate.
#
# NEEDS_LOOK sits just above WORKING: worth surfacing over a session that is
# plainly busy, never competing with a real block or an error.
PRIORITY = {
    State.NEEDS_INPUT: 0,
    State.NEEDS_HELD: 1,
    State.ERROR: 2,
    State.NEEDS_LOOK: 3,
    State.WORKING: 4,
    State.WAITING: 5,
    State.STALE: 6,
    State.STARTING: 7,
    State.IDLE: 8,
    State.ENDED: 9,
}

# Notification types that mean a human has to do something. Taken from the
# documented `notification_type` values; the rest are informational.
ATTENTION_NOTIFICATIONS = {
    "permission_prompt",
    "idle_prompt",
    "elicitation_dialog",
    "elicitation_url_dialog",
    "agent_needs_input",
}


@dataclass
class Session:
    session_id: str
    cwd: str
    label: str
    state: State = State.STARTING
    state_since: float = 0.0
    last_event: float = 0.0
    last_tool: str = ""
    model: str = ""
    cost_usd: float | None = None
    ctx_pct: int | None = None
    permission_mode: str = ""
    error_type: str = ""
    bg_tasks: int = 0
    # Background work the beacon cannot retire -- a workflow, a background shell,
    # a teammate -- as listed by the last Stop or SubagentStop. It only decides
    # how loud an idle_prompt is (NEEDS_LOOK rather than NEEDS_INPUT) and never
    # whether the row is WORKING; that stays `bg_tasks`' job alone.
    bg_other: int = 0
    # Which agent raised the attention this session is currently showing. "" is
    # the main thread. A subagent's hook events carry the *parent's* session_id,
    # so without this a subagent's tool call answers a prompt it never saw.
    attn_agent: str = ""
    # When the background count was last confirmed by a payload. A running
    # subagent emits hook events constantly, so silence means it is stale.
    bg_seen: float = 0.0
    # The turn ended while background work was still outstanding, so the session
    # is waiting on the machine and not yet on a person.
    turn_over: bool = False
    # An `idle_prompt` that arrived while background work was outstanding. Held
    # rather than dropped: Claude Code sent exactly *one* for the idle period
    # that turned this up, so a swallowed notification is a row that never goes
    # red at all. tick() releases it when the count retires.
    idle_held: bool = False
    idle_held_agent: str = ""

    def set_state(self, new: State, now: float) -> None:
        if new != self.state:
            self.state = new
            self.state_since = now
        self.last_event = now


@dataclass
class SessionStore:
    stale_after_s: float = 300.0
    # How long a session that wants a human pulses, and how long it stays red at
    # all. Both are measured from when it entered the attention family, so they
    # are absolute positions on the ladder rather than durations of each rung.
    need_pulse_s: float = 120.0
    need_red_s: float = 600.0
    # How long an idle_prompt softened to NEEDS_LOOK stays soft before it joins
    # the ladder anyway. Nothing reports the end of a workflow, so this timer is
    # the only thing standing between a wrong guess and a session nobody sees.
    look_s: float = 300.0
    ended_grace_s: float = 30.0
    # A session with no hook event at all for this long is dropped, whatever its
    # state. Nothing else ever removes one that never sent SessionEnd, and a
    # window killed by a Windows Update restart never does: one such row sat on
    # the display for 45 hours, surviving a daemon restart that restored it
    # because it was 21 hours old, under the same 24-hour cutoff persist.load()
    # applies. Applying it here too means a restart no longer renews a ghost.
    ghost_after_s: float = 86400.0
    # How long an outstanding background count is believed without fresh
    # evidence. See where it expires, in tick().
    bg_quiet_s: float = 180.0
    max_rows: int = 6
    label_overrides: dict[str, str] = field(default_factory=dict)
    sessions: dict[str, Session] = field(default_factory=dict)
    _root_cache: dict[str, str] = field(default_factory=dict, repr=False)
    # Account-level, not per-session, so it lives on the store. Timestamped
    # because it only arrives with a statusline refresh: once every session is
    # closed the figures stop updating, and a stale percentage is worse than
    # none when the whole point is knowing where you stand right now.
    rate_limits: dict[str, int] = field(default_factory=dict)
    rate_limits_at: float = 0.0
    rate_limit_max_age_s: float = 300.0

    # ---- inputs ----

    def apply_event(self, ev: dict[str, Any], now: float) -> None:
        """Apply one hook payload as sent by Claude Code.

        Field names follow the documented hook input schema. Note that
        SessionStart carries `session_start_reason` and SessionEnd carries
        `session_end_reason`; neither is a plain `source` or `reason`.
        """
        sid = ev.get("session_id")
        if not sid:
            return
        name = ev.get("hook_event_name", "")
        s = self._get_or_create(sid, ev.get("cwd", ""), now)
        if mode := ev.get("permission_mode"):
            s.permission_mode = mode

        # Which agent this event came from; "" is the session's main thread.
        # A subagent's events carry the parent's `session_id`, so this is the
        # only thing separating them. It has to be `agent_id`: `agent_type` is
        # also set on the main thread of a session started with --agent, and
        # the CLI's own schema says so in as many words.
        agent = ev.get("agent_id") or ""
        if agent:
            # A live subagent is proof that background work really is running.
            s.bg_seen = now

        if name == "SessionStart":
            s.error_type = ""
            s.set_state(State.STARTING, now)
        elif name == "UserPromptSubmit":
            s.error_type = ""
            s.bg_tasks = s.bg_other = 0
            s.turn_over = False
            s.idle_held = False
            s.set_state(State.WORKING, now)
        elif name in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
            # Activity. Only PostToolUse is registered by default; PreToolUse
            # is handled too in case someone turns it on for finer resolution.
            s.last_tool = ev.get("tool_name", "") or s.last_tool
            self._activity(s, now, agent)
        elif name == "PostToolBatch":
            # Fires once when every call in a batch has resolved, including one
            # that resolved by being *rejected*: a denied tool never runs, so it
            # produces no PostToolUse at all.
            #
            # This is the only event that lands at the moment a permission
            # prompt is answered. Rejecting a plan with feedback left the row
            # red for the minute Claude then spent thinking, because nothing
            # else arrived until its next tool call.
            s.last_tool = last_batch_tool(ev) or s.last_tool
            self._activity(s, now, agent)
        elif name == "PermissionRequest":
            # A dedicated event, more precise than watching Notification.
            self._want_attention(s, now, agent)
        elif name == "PermissionDenied":
            # The prompt was answered, just not with a yes.
            #
            # Dead on Claude Code 2.1.261: refusing a prompt produced the
            # PermissionRequest and no PermissionDenied, with the hook
            # registered. Kept because it is right if the event ever arrives,
            # and harmless if it does not -- the next tool call clears the row
            # either way.
            self._activity(s, now, agent)
        elif name == "Notification":
            kind = ev.get("notification_type")
            if kind not in ATTENTION_NOTIFICATIONS:
                s.last_event = now
            elif kind == "idle_prompt" and s.bg_tasks:
                # `idle_prompt` only means Claude has been waiting a while,
                # which is not the same as waiting on a human. The others in the
                # set are direct asks -- a permission prompt raised inside a
                # subagent still needs answering -- so only this one is held
                # back, and only while background work is outstanding.
                #
                # Held, not dropped. Dropping it assumed a later one would get
                # through, and for the idle period that turned this up Claude
                # Code sent exactly one: the row then never went red, whatever
                # happened to the count afterwards. tick() owns the release, so
                # there is no time arithmetic here and no window for the
                # notification to fall the wrong side of.
                s.idle_held, s.idle_held_agent = True, agent
                s.last_event = now
            elif kind == "idle_prompt":
                self._idle_prompt(s, now, agent)
            else:
                self._want_attention(s, now, agent)
        elif name == "Stop":
            # A turn that ends with a subagent still running has not handed
            # anything back: the session is waiting on the machine, not on a
            # person. Calling that IDLE let the next idle_prompt escalate it to
            # a red row with nothing for anyone to do.
            #
            # `bg_seen` is refreshed here because the payload is a freshly
            # enumerated snapshot of the task registry, which is exactly what
            # the field records. It is not a claim that the turn is still
            # running.
            s.bg_tasks = running_background_tasks(ev)
            s.bg_other = untracked_background_tasks(ev)
            s.bg_seen = now
            s.turn_over = bool(s.bg_tasks)
            s.set_state(State.WORKING if s.bg_tasks else State.IDLE, now)
        elif name == "SubagentStop":
            # Fires the moment a subagent ends, and carries the same
            # `background_tasks` list Stop does -- so it is a resync at exactly
            # the point the count changes. Before it was registered the count
            # could only be recomputed by the *parent's* next Stop, which never
            # arrives if the parent has finished its turn and is waiting on the
            # user. That is the case where the row stayed blue for good.
            if "background_tasks" in ev:
                # The agent that is stopping may still be listed as running in
                # its own payload; it is not, in the one capture we have, but
                # counting it would put the stale count straight back.
                s.bg_tasks = running_background_tasks(ev, exclude=agent)
                s.bg_other = untracked_background_tasks(ev, exclude=agent)
            else:
                # The field is optional in the CLI's schema, so do not read an
                # absent list as "nothing left running".
                s.bg_tasks = max(0, s.bg_tasks - 1)
            s.bg_seen = now
            if s.turn_over and not s.bg_tasks:
                # The turn had already ended and now the machine is done too,
                # so it really is the user's move.
                s.turn_over = False
                s.set_state(State.IDLE, now)
            else:
                s.last_event = now
        elif name == "StopFailure":
            # The turn ended on an API error: rate limit, overload, billing.
            # Without this the session looks busy until it goes stale, which
            # reads as a crashed editor rather than something needing a human.
            s.error_type = ev.get("error_type", "") or "error"
            s.set_state(State.ERROR, now)
        elif name == "SessionEnd":
            s.set_state(State.ENDED, now)
        else:
            s.last_event = now

    @staticmethod
    def _activity(s: Session, now: float, agent: str) -> None:
        """A tool event: the session is working, and a prompt may just have been
        answered. Which of the two depends on who sent it.

        A subagent's tool calls carry the parent's `session_id`, and treating
        them as the parent's own repainted a genuinely blocked row blue on every
        one of them -- the reason a session waiting on you could show WORKING
        indefinitely. So a tool event ends the wait only when it comes from
        whoever raised it. That keeps `PostToolBatch` clearing a prompt answered
        with "no" or with feedback, which is the only event that arrives at that
        moment, and it keeps a subagent's own `PostToolBatch` clearing a prompt
        the subagent raised.

        The event still refreshes the staleness timer rather than being dropped.
        A long subagent run is the only traffic its session produces, and
        ignoring it outright would send an actively working parent to STALE.
        """
        if not agent:
            # The session is demonstrably going again, so a turn that had ended
            # has not, and any notification held from it is out of date.
            s.turn_over = s.idle_held = False
        if s.state in ATTENTION_STATES and agent != s.attn_agent:
            s.last_event = now
            return
        s.set_state(State.WORKING, now)

    @staticmethod
    def _want_attention(s: Session, now: float, agent: str = "") -> None:
        """Put a session on the attention ladder, or leave it where it is.

        Re-entry must not restart the pulse. `idle_prompt` fires whenever Claude
        has been waiting a while, so a session nobody answers is notified again
        and again -- two were captured for one session minutes apart, see
        tests/fixtures -- and resetting the rung on each would pulse forever,
        which is the whole thing the ladder exists to stop. The ladder re-arms only after
        the session has genuinely left the family, via a tool call, a prompt or
        a Stop.
        """
        if s.state in ATTENTION_STATES:
            s.last_event = now
            return
        s.idle_held = False
        # Remembered on entry only, for the same reason the rung is: this is the
        # ask that is outstanding, and a later notification does not replace it.
        s.attn_agent = agent
        s.set_state(State.NEEDS_INPUT, now)

    @classmethod
    def _idle_prompt(cls, s: Session, now: float, agent: str) -> None:
        """An `idle_prompt` nothing is holding back. How loud it should be depends
        on what is still running.

        With untracked background work outstanding -- the case that turned this
        up was an orchestrator waiting on a `workflow` task -- the notification
        means no more than "the main thread has been quiet a while", so it gets
        NEEDS_LOOK rather than the pulse a permission prompt gets. tick()
        graduates it onto the ladder after `look_s` if nothing clears it first,
        because nothing announces the end of a workflow and a wrong guess must
        not be able to hide a session for good.

        Repeats leave the row where it is, the same rule the ladder follows: a
        timer that restarts on every notification never runs out.
        """
        if s.state in ATTENTION_STATES or s.state == State.NEEDS_LOOK:
            s.last_event = now
            return
        if not s.bg_other:
            cls._want_attention(s, now, agent)
            return
        s.idle_held = False
        s.attn_agent = agent
        s.set_state(State.NEEDS_LOOK, now)

    def apply_status(self, st: dict[str, Any], now: float) -> None:
        """Apply one statusline payload. Field names to be confirmed in Phase 1."""
        sid = st.get("session_id")
        if not sid:
            return
        cwd = (st.get("workspace") or {}).get("current_dir", "")
        s = self._get_or_create(sid, cwd, now)
        s.model = short_model((st.get("model") or {}).get("display_name", ""))
        cost = (st.get("cost") or {}).get("total_cost_usd")
        if isinstance(cost, (int, float)):
            s.cost_usd = float(cost)
        s.ctx_pct = extract_ctx_pct(st)
        if rl := extract_rate_limits(st):
            self.rate_limits, self.rate_limits_at = rl, now

    def tick(self, now: float) -> None:
        """Advance time: retire background counts, walk the ladder, mark stale."""
        for sid in list(self.sessions):
            s = self.sessions[sid]
            if s.state == State.ENDED:
                if now - s.state_since > self.ended_grace_s:
                    del self.sessions[sid]
                continue
            if now - s.last_event > self.ghost_after_s:
                # Measured from the last event, not state_since: a WAITING row
                # keeps its age across repeat notifications, and those repeats
                # are exactly what proves a parked session is still alive.
                del self.sessions[sid]
                continue

            # The hold on a background count expires here, in one place, rather
            # than inside the branch that happens to consult it. A count nothing
            # has confirmed for this long is not evidence of anything: retiring
            # it hands the row back and releases whatever it was suppressing.
            if s.bg_tasks and now - s.bg_seen > self.bg_quiet_s:
                s.bg_tasks = 0
                if s.turn_over:
                    s.turn_over = False
                    if s.state == State.WORKING:
                        # IDLE, not STALE. The turn ended, so this session is
                        # waiting on a person; STALE is the worst answer
                        # available because amber sorts *below* `work`.
                        s.set_state(State.IDLE, now)
            if s.idle_held and not s.bg_tasks:
                s.idle_held = False
                agent, s.idle_held_agent = s.idle_held_agent, ""
                # Through the same severity check as a fresh notification: a
                # subagent can retire while a workflow is still listed.
                self._idle_prompt(s, now, agent)

            if s.state == State.WORKING and now - s.last_event > self.stale_after_s:
                # Same reasoning as above for a turn that ended and then went
                # quiet without the count ever being retired.
                s.set_state(State.IDLE if s.turn_over else State.STALE, now)
            elif s.state == State.NEEDS_LOOK and now - s.state_since > self.look_s:
                # The safety valve. Nothing reports that a workflow ended, so a
                # soft row cannot wait for proof; left alone this long it joins
                # the ladder exactly where an unsoftened idle_prompt would have.
                #
                # Unlike the rungs below, this goes through set_state() and so
                # restarts the age. Time spent here was not time spent waiting
                # on a human, by this state's own definition, so the ladder's
                # two and ten minutes are counted from when the alarm began.
                self._want_attention(s, now, s.attn_agent)
            # The ladder assigns `state` directly instead of calling set_state(),
            # which is the opposite of the STALE transition above and looks like
            # a bug until you see why: set_state() resets state_since, and that
            # is both the displayed age and the ladder's own origin. Resetting
            # it would restart the age mid-wait and re-base the second
            # threshold, so `wait` would arrive ten minutes after `held` rather
            # than ten minutes after the session began waiting.
            elif s.state in (State.NEEDS_INPUT, State.NEEDS_HELD):
                # Both rungs share an origin and the larger threshold is
                # tested first, so the ladder settles in a single tick however
                # long it has been since the last one. Advancing one rung per
                # tick would leave a resumed laptop pulsing until the next pass.
                waited = now - s.state_since
                if waited > self.need_red_s:
                    s.state = State.WAITING
                elif waited > self.need_pulse_s:
                    s.state = State.NEEDS_HELD

    # ---- output ----

    def snapshot(self, now: float) -> dict[str, Any]:
        live = sorted(self.sessions.values(), key=lambda s: (PRIORITY[s.state], s.state_since))
        rows = live[: self.max_rows]
        costs = [s.cost_usd for s in live if s.cost_usd is not None]
        out: dict[str, Any] = {
            "t": "snap",
            "v": 1,
            "ts": int(now),
            "n": len(live),
            "sel": 0 if rows else -1,
            "s": [self._row(s, now) for s in rows],
        }
        if costs:
            out["cost"] = round(sum(costs), 2)
        if self.rate_limits and now - self.rate_limits_at <= self.rate_limit_max_age_s:
            out["rl"] = dict(self.rate_limits)
        return out

    def background_report(self, now: float) -> list[dict[str, Any]]:
        """Per-session background bookkeeping, for /health. Not sent to the device.

        None of this was visible from outside, and a session stuck blue behind a
        background count looks exactly like a session that is genuinely busy.
        Diagnosing that needed the persisted state file plus the session's own
        transcript to work out which branch had run; it should be one curl, the
        way `events_received` already is for missing hooks.

        Only sessions with something outstanding appear, so a quiet desk reports
        an empty list rather than a row per session saying nothing.
        """
        return [
            {
                "id": s.session_id[:8],
                "l": elide_label(s.label),
                "tasks": s.bg_tasks,
                "other": s.bg_other,
                "turn_over": s.turn_over,
                "idle_held": s.idle_held,
                "bg_seen_age_s": round(now - s.bg_seen, 1) if s.bg_seen else None,
            }
            for s in self.sessions.values()
            if s.bg_tasks or s.bg_other or s.turn_over or s.idle_held
        ]

    # ---- internals ----

    def _get_or_create(self, sid: str, cwd: str, now: float) -> Session:
        s = self.sessions.get(sid)
        if s is None:
            s = Session(session_id=sid, cwd=cwd, label=self._label(cwd),
                        state_since=now, last_event=now)
            self.sessions[sid] = s
        elif cwd and cwd != s.cwd:
            # Recompute rather than keep the first value seen. A session's cwd
            # moves as you work, and freezing the label meant a session first
            # seen from a subdirectory was mislabelled for its whole life.
            s.cwd, s.label = cwd, self._label(cwd)
        return s

    def _label(self, cwd: str) -> str:
        """Label a session by its project, not by wherever its shell happens to be.

        `cwd` is the session's live working directory and moves around: a `cd`
        into a subdirectory would otherwise relabel the row, so a repo called
        session-beacon shows up as "host" the moment anything runs in host/.
        Resolving to the enclosing repository keeps the name stable and matches
        how people actually think about their sessions.
        """
        key = cwd.replace("\\", "/").rstrip("/")
        root = self._project_root(key)
        for candidate in (key, root):
            if candidate and candidate in self.label_overrides:
                return self.label_overrides[candidate]
        return os.path.basename(root or key) or "?"

    def _project_root(self, key: str) -> str:
        """Nearest ancestor holding a .git entry, or "" if there is none.

        Cached: hooks fire on every tool call and this touches the filesystem.
        A worktree's .git is a file rather than a directory, so test existence.
        """
        if key in self._root_cache:
            return self._root_cache[key]
        root = ""
        p = Path(key)
        for cand in (p, *p.parents):
            try:
                if (cand / ".git").exists():
                    root = str(cand).replace("\\", "/").rstrip("/")
                    break
            except OSError:
                break
        self._root_cache[key] = root
        return root

    @staticmethod
    def _row(s: Session, now: float) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": s.session_id[:8],
            "l": elide_label(s.label),
            "st": s.state.value,
            "age": int(now - s.state_since),
        }
        if s.ctx_pct is not None:
            row["ctx"] = s.ctx_pct
        if s.model:
            row["m"] = s.model[:8]
        if s.last_tool:
            row["tool"] = s.last_tool[:10]
        return row


def last_batch_tool(ev: dict[str, Any]) -> str:
    """The last named tool in a PostToolBatch payload, or "".

    The batch is one entry per tool call in the turn, in order, so the last one
    is the closest equivalent of the `tool_name` the per-tool events carry.
    """
    calls = ev.get("tool_calls")
    if not isinstance(calls, list):
        return ""
    for call in reversed(calls):
        if isinstance(call, dict) and call.get("tool_name"):
            return str(call["tool_name"])
    return ""


# Task types that mean the session is waiting on the machine rather than on a
# person. Only one qualifies, and the reason is that the beacon has machinery
# for exactly one: `SubagentStop` retires a subagent, and a subagent's own hook
# events carry an `agent_id` that refreshes `bg_seen`. Nothing reports the end
# of any other type, so counting one can only ever mute the display.
#
# `monitor` is the type that forced this. An armed artifact comment monitor is
# registered for as long as the session lives, so counting it meant a session
# could never be shown as waiting on you again -- which is what happened.
COUNTED_TASK_TYPES = frozenset({"subagent"})

# Task types that are never work in flight, even for the softer signal below.
# A `monitor` is a subscription that lives as long as the session does, and the
# other two are Claude Code's own housekeeping. Counting any of them would soften
# every idle_prompt the session ever sends, which is the monitor bug again with
# a delay in front of it.
UNTRACKED_EXCLUDED_TYPES = frozenset({"monitor", "dream", "auto-mode scan"})

# Claude Code only lists in-flight work, and its own filter admits both of
# these; `pending` was previously dropped because the check compared against
# "running" alone.
LIVE_STATUSES = frozenset({"running", "pending"})


def running_background_tasks(ev: dict[str, Any], exclude: str = "") -> int:
    """How many *subagents* a Stop payload reports as still in flight.

    Shape, from a capture and from the CLI's own hook schema:

        "background_tasks": [{"id": "...", "type": "subagent",
                              "agent_type": "Explore", "status": "running",
                              "description": "..."}]

    `type` is documented as a "friendly task-type label", one of `subagent`,
    `shell`, `monitor`, `workflow`, `MCP task`, `teammate`, `cloud session`,
    `dream` or `auto-mode scan`, falling back to the raw internal name for
    anything newer. Only `subagent` is counted; see COUNTED_TASK_TYPES.

    The two unknown cases deliberately break opposite ways. A task with no
    `status` counts as running, because assuming it had finished produces the
    false red this exists to prevent. A task with an unrecognised `type` is
    ignored, because the failures are not symmetric: a wrongly ignored task
    shows a red row that de-escalates in ten minutes and clears on the next tool
    call, while a wrongly counted one that never ends silences the session for
    good and says nothing about why.

    `exclude` drops the task whose `id` matches, which is how a SubagentStop
    avoids counting the very agent whose ending it is reporting.
    """
    tasks = ev.get("background_tasks")
    if not isinstance(tasks, list):
        return 0
    return sum(
        1 for t in tasks
        if isinstance(t, dict) and t.get("type") in COUNTED_TASK_TYPES
        and t.get("status", "running") in LIVE_STATUSES
        and not (exclude and t.get("id") == exclude)
    )


def untracked_background_tasks(ev: dict[str, Any], exclude: str = "") -> int:
    """How much *other* real work a Stop payload reports as still in flight.

    Everything `running_background_tasks()` deliberately ignores, less the types
    that never finish (UNTRACKED_EXCLUDED_TYPES): `workflow`, `shell`,
    `MCP task`, `teammate`, `cloud session`. The beacon cannot retire any of
    these, so the figure never keeps a row WORKING. It only softens an
    idle_prompt to NEEDS_LOOK.

    An unrecognised `type` *is* counted here, the opposite of the subagent
    count, because the costs have flipped. NEEDS_LOOK graduates to the ladder on
    its own after `look_s`, so a wrong guess costs at most that long; the
    subagent count has no such backstop, which is why it refuses to guess.
    """
    tasks = ev.get("background_tasks")
    if not isinstance(tasks, list):
        return 0
    return sum(
        1 for t in tasks
        if isinstance(t, dict)
        and t.get("type") not in COUNTED_TASK_TYPES
        and t.get("type") not in UNTRACKED_EXCLUDED_TYPES
        and t.get("status", "running") in LIVE_STATUSES
        and not (exclude and t.get("id") == exclude)
    )


def elide_label(label: str, width: int = 16, head: int = 9, tail: int = 5) -> str:
    """Fit a label to `width` by cutting from the middle, not the end.

    Clones of one project tend to differ only in a suffix -- `longnameclone2`,
    `longnameclone3` -- and a plain prefix cut threw exactly that away, so every
    clone's row read the same. `head + 2 + tail == width`, so the result is
    always full width. Two ASCII dots rather than an ellipsis character because
    the device's font is ASCII only.
    """
    if len(label) <= width:
        return label
    return f"{label[:head]}..{label[-tail:]}"


def short_model(display_name: str) -> str:
    """'Claude Fable 5.1' -> 'fable5.1'. Good enough for an 8-char field."""
    parts = display_name.lower().replace("claude", "").split()
    return "".join(parts)[:8]


def extract_rate_limits(st: dict[str, Any]) -> dict[str, int]:
    """Account-level usage from a statusline payload.

    Scoping assumed this was not available anywhere. It is: the statusline
    carries `rate_limits.five_hour` and `rate_limits.seven_day`, each with a
    used percentage and a reset timestamp. Percentages are all the display has
    room for; the reset times are deliberately not forwarded yet.
    """
    rl = st.get("rate_limits") or {}
    out: dict[str, int] = {}
    for key, short in (("five_hour", "h5"), ("seven_day", "d7")):
        v = (rl.get(key) or {}).get("used_percentage")
        if isinstance(v, (int, float)):
            out[short] = max(0, min(100, round(v)))
    return out


def extract_ctx_pct(st: dict[str, Any]) -> int | None:
    """Context percent from a statusline payload.

    `context_window.used_percentage` is pre-calculated and is what we want,
    but the docs note it can be null early in a session and again right after
    a compaction, so fall back to the token counts before giving up.
    """
    cw = st.get("context_window") or {}
    pct = cw.get("used_percentage")
    if isinstance(pct, (int, float)):
        return int(pct)

    # Input tokens only. `total_input_tokens` already includes cache reads and
    # writes, and everything the model has previously said is resent as input,
    # so it is the figure that describes how full the window is. Adding output
    # would double-count all but the last reply.
    #
    # The published field table does not spell out the formula, but it defines
    # `exceeds_200k_tokens` as input, cache and output "combined" and pointedly
    # does not say that for `used_percentage`. Real payloads cannot settle it
    # either way: output is a few hundred tokens against half a million input,
    # so both formulas round to the same integer.
    size = cw.get("context_window_size")
    used = cw.get("total_input_tokens")
    if not isinstance(used, (int, float)):
        used = 0
    if used and isinstance(size, (int, float)) and size > 0:
        return int(100 * used / size)
    return None
