"""Save and restore the session store across daemon restarts.

The daemon holds session state in memory only, and a session parked on a
permission prompt sends no hook events at all. So a restart -- at logon, after a
crash, or to free the COM port for a reflash -- used to drop every waiting
session and show nothing until the user happened to touch each one. The display
under-reported and the obvious explanation, missing hooks, was the wrong trail.

The I/O lives here rather than in state.py, which stays pure so it can be tested
with fixtures.

Timestamps are absolute `time.time()` values and are written as-is. A session
that has been waiting three hours across a restart should still read three
hours, so nothing is rebased on load. The consequence is that a session that
*was* working when the daemon went down comes back past its staleness threshold
and shows amber, which is honest: from the daemon's side it has been silent that
long, and the next tool call corrects it.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from .state import Session, SessionStore, State

log = logging.getLogger(__name__)

FORMAT_V = 1

# Fields carried across a restart. `last_tool` is included because it costs
# nothing; `error_type` because an ERROR row is meaningless without it.
#
# `attn_agent` is here because losing it is the subagent bug again across a
# restart: a restored red row whose owner is forgotten reads as the main
# thread's, and the first tool event from anywhere clears it.
#
# `bg_tasks`, `bg_other`, `bg_seen` and `turn_over` are deliberately absent. Nothing tells
# the daemon what happened to a subagent while it was down, and forgetting the
# count errs towards letting an idle_prompt escalate, which is the direction
# this state exists to protect.
#
# `idle_held` is here for that same reason, read the other way round. It is a
# notification the session has already sent and nobody has answered, and Claude
# Code may never send another. Restoring it alongside a forgotten count means
# the first tick() releases it and the row goes red -- which is true: the
# session is waiting on you, and it was waiting before the restart.
_FIELDS = (
    "session_id", "cwd", "label", "state_since", "last_event",
    "last_tool", "model", "cost_usd", "ctx_pct", "permission_mode",
    "error_type", "attn_agent", "idle_held", "idle_held_agent",
)


def save(path: str | Path, store: SessionStore, now: float) -> None:
    """Write the store to `path` atomically. Never raises."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for s in store.sessions.values():
            # An ENDED session is already on its way out; restoring one would
            # put a dead row back on screen for the length of the grace period.
            if s.state == State.ENDED:
                continue
            row: dict[str, Any] = {f: getattr(s, f) for f in _FIELDS}
            row["state"] = s.state.value
            rows.append(row)
        blob = json.dumps(
            {"v": FORMAT_V, "saved_at": now, "sessions": rows},
            separators=(",", ":"),
        )
        # Write beside the target and replace, so a crash mid-write cannot
        # leave a truncated file that then fails to load on every start.
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(blob)
            os.replace(tmp, p)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except Exception as e:  # noqa: BLE001
        log.warning("could not save session state to %s (%s)", path, e)


def load(path: str | Path, store: SessionStore, now: float,
         max_age_s: float) -> int:
    """Restore sessions into `store`. Returns how many were restored.

    A session whose last event is older than `max_age_s` is dropped rather than
    restored: nothing tells the daemon that a window was closed while it was
    down, since SessionEnd would have gone nowhere, so an old row is more likely
    a ghost than a session still waiting. Never raises.
    """
    try:
        p = Path(path)
        if not p.is_file():
            return 0
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("v") != FORMAT_V:
            log.warning("ignoring session state at %s: format v%s, expected v%d",
                        p, data.get("v"), FORMAT_V)
            return 0

        restored = dropped = 0
        for row in data.get("sessions") or []:
            sid = row.get("session_id")
            if not sid or sid in store.sessions:
                continue
            if now - float(row.get("last_event") or 0) > max_age_s:
                dropped += 1
                continue
            try:
                state = State(row.get("state"))
            except ValueError:
                # A state name from a newer build. Dropping it is better than
                # inventing one, and the session re-registers on its next event.
                dropped += 1
                continue
            s = Session(
                session_id=sid,
                cwd=row.get("cwd") or "",
                label=row.get("label") or "",
                state=state,
            )
            for f in _FIELDS:
                if f in ("session_id", "cwd", "label"):
                    continue
                if row.get(f) is not None:
                    setattr(s, f, row[f])
            store.sessions[sid] = s
            restored += 1

        if restored or dropped:
            log.info("restored %d session(s) from %s, dropped %d as too old",
                     restored, p, dropped)
        return restored
    except Exception as e:  # noqa: BLE001
        log.warning("could not load session state from %s (%s)", path, e)
        return 0
