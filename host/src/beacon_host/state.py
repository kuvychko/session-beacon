"""Session state machine. Pure logic, no I/O, so it is unit-testable with fixtures.

See docs/architecture.md for the state diagram and docs/protocol.md for the
snapshot shape produced by SessionStore.snapshot().
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class State(str, Enum):
    STARTING = "start"
    WORKING = "work"
    NEEDS_INPUT = "need"
    ERROR = "err"
    IDLE = "idle"
    STALE = "stale"
    ENDED = "end"


# Display priority: lower sorts first.
PRIORITY = {
    State.NEEDS_INPUT: 0,
    State.ERROR: 1,
    State.WORKING: 2,
    State.STALE: 3,
    State.STARTING: 4,
    State.IDLE: 5,
    State.ENDED: 6,
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

    def set_state(self, new: State, now: float) -> None:
        if new != self.state:
            self.state = new
            self.state_since = now
        self.last_event = now


@dataclass
class SessionStore:
    stale_after_s: float = 300.0
    ended_grace_s: float = 30.0
    max_rows: int = 6
    label_overrides: dict[str, str] = field(default_factory=dict)
    sessions: dict[str, Session] = field(default_factory=dict)

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

        if name == "SessionStart":
            s.error_type = ""
            s.set_state(State.STARTING, now)
        elif name == "UserPromptSubmit":
            s.error_type = ""
            s.set_state(State.WORKING, now)
        elif name in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
            # Activity. Only PostToolUse is registered by default; PreToolUse
            # is handled too in case someone turns it on for finer resolution.
            s.last_tool = ev.get("tool_name", "") or s.last_tool
            s.set_state(State.WORKING, now)
        elif name == "PermissionRequest":
            # A dedicated event, more precise than watching Notification.
            s.set_state(State.NEEDS_INPUT, now)
        elif name == "PermissionDenied":
            # The prompt was answered, just not with a yes.
            s.set_state(State.WORKING, now)
        elif name == "Notification":
            if ev.get("notification_type") in ATTENTION_NOTIFICATIONS:
                s.set_state(State.NEEDS_INPUT, now)
            else:
                s.last_event = now
        elif name == "Stop":
            s.set_state(State.IDLE, now)
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

    def tick(self, now: float) -> None:
        """Advance time: mark stale sessions, drop ended ones."""
        for sid in list(self.sessions):
            s = self.sessions[sid]
            if s.state == State.ENDED and now - s.state_since > self.ended_grace_s:
                del self.sessions[sid]
            elif s.state == State.WORKING and now - s.last_event > self.stale_after_s:
                s.set_state(State.STALE, now)

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
        return out

    # ---- internals ----

    def _get_or_create(self, sid: str, cwd: str, now: float) -> Session:
        s = self.sessions.get(sid)
        if s is None:
            s = Session(session_id=sid, cwd=cwd, label=self._label(cwd), state_since=now, last_event=now)
            self.sessions[sid] = s
        elif cwd and not s.cwd:
            s.cwd, s.label = cwd, self._label(cwd)
        return s

    def _label(self, cwd: str) -> str:
        key = cwd.replace("\\", "/").rstrip("/")
        return self.label_overrides.get(key) or os.path.basename(key) or "?"

    @staticmethod
    def _row(s: Session, now: float) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": s.session_id[:8],
            "l": s.label[:16],
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


def short_model(display_name: str) -> str:
    """'Claude Fable 5.1' -> 'fable5.1'. Good enough for an 8-char field."""
    parts = display_name.lower().replace("claude", "").split()
    return "".join(parts)[:8]


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
    size = cw.get("context_window_size")
    tokens = [cw.get("total_input_tokens"), cw.get("total_output_tokens")]
    used = sum(t for t in tokens if isinstance(t, (int, float)))
    if used and isinstance(size, (int, float)) and size > 0:
        return int(100 * used / size)
    return None
