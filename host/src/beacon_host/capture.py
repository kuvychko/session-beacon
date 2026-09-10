"""Record real hook payloads as JSONL, with conversation content stripped.

The daemon already receives every hook payload, so it is a better capture point
than bolting a second hook onto settings.json: no extra process per event, and
it records exactly what the state machine actually sees.

**Content is removed, not recorded.** Payloads carry prompts, tool arguments,
tool output and assistant messages. Fixtures need field *names* and *shapes*,
never the text, and this repo must not accumulate session transcripts. Values
are replaced with a marker naming the type and size, which is enough to write
tests against and useless to anyone reading the file.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Fields whose values are conversation content or user data.
CONTENT_FIELDS = frozenset({
    "user_prompt",
    "last_assistant_message",
    "tool_output",
    "tool_response",
    "error",
    "message",
    "prompt",
})


def _is_marker(v: Any) -> bool:
    """Already redacted. Re-redacting would replace '<str len=22>' with
    '<str len=12>', the length of the marker itself, silently corrupting a
    fixture file every time it was refreshed."""
    return isinstance(v, str) and v.startswith("<") and v.endswith(">")


def _marker(v: Any) -> str:
    if _is_marker(v):
        return v
    if isinstance(v, str):
        return f"<str len={len(v)}>"
    if isinstance(v, (list, dict)):
        return f"<{type(v).__name__} len={len(v)}>"
    return f"<{type(v).__name__}>"


# Free text that appears *inside* a structured field rather than at the top
# level. `description` names a background task; `ruleContent` is the permission
# rule Claude Code offers to add, which is built from the command line and so
# echoes the very thing redacting `tool_input` is meant to strip. `command` is
# the same leak once more: a background task of type `shell` carries the whole
# command line, up to a thousand characters of it.
NESTED_CONTENT_KEYS = frozenset({"description", "ruleContent", "command"})

# Top-level fields holding structured data with free text somewhere inside.
# Their shape is worth keeping: `status` tells a running background task from a
# finished one, and `behavior`/`toolName` say what a suggestion would allow.
#
# `tool_calls` is the whole of a `PostToolBatch` payload, and it is the worst of
# the three: every entry nests a `tool_input` and a `tool_response`, so the
# command line and its output sit one level below the top-level names that
# CONTENT_FIELDS already covers.
# `session_crons` rides on the same events as `background_tasks` and carries a
# `prompt` per entry -- the text of a /loop, a CronCreate or a ScheduleWakeup.
# `prompt` is already named in CONTENT_FIELDS, but that only covers the top
# level, so without this the list went through untouched.
STRUCTURED_FIELDS = frozenset({
    "background_tasks", "permission_suggestions", "tool_calls", "session_crons"})


def _redact_nested(v: Any) -> Any:
    """Walk a structured field, marking free text and keeping everything else.

    Recursive because the nesting is not one level: a permission suggestion
    holds a list of rules, and the free text is inside those.

    The content keys are the same ones the top level uses, plus NESTED_CONTENT_KEYS
    and the same `tool_input` rule: a name below the top level is no less content
    than the same name above it, and a batch entry has both.
    """
    if isinstance(v, dict):
        out: dict[str, Any] = {}
        for k, vv in v.items():
            if k in NESTED_CONTENT_KEYS or k in CONTENT_FIELDS:
                out[k] = _marker(vv)
            elif k == "tool_input" and isinstance(vv, dict):
                out[k] = {kk: _marker(v2) for kk, v2 in vv.items()}
            else:
                out[k] = _redact_nested(vv)
        return out
    if isinstance(v, list):
        return [_redact_nested(item) for item in v]
    return v


def redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep the shape, drop the content.

    `cwd` is deliberately kept: it is the one path the state machine actually
    reads, and it carries a repo name rather than anything private. Every other
    path is reduced to its last segment, because they run through the user's
    home directory and so carry a username.
    """
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if k in CONTENT_FIELDS:
            out[k] = _marker(v)
        elif k in STRUCTURED_FIELDS:
            out[k] = _redact_nested(v)
        elif k == "tool_input" and isinstance(v, dict):
            # Argument names are useful; argument values are the user's data.
            out[k] = {kk: _marker(vv) for kk, vv in v.items()}
        elif k != "cwd" and k.endswith(("_path", "_dir")) and isinstance(v, str):
            # Normalise separators first, so this is a plain trailing-slash trim
            # rather than a multi-character strip that reads like a substring.
            out[k] = f"<path>/{os.path.basename(v.replace('\\\\', '/').rstrip('/'))}"
        else:
            out[k] = v
    return out


class Capture:
    """Append redacted payloads to a JSONL file, one object per line."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.count = 0
        self._failed = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("capture disabled, cannot use %s: %s", self.path, e)
            self._failed = True

    def write(self, kind: str, payload: dict[str, Any]) -> None:
        if self._failed:
            return
        record = {"kind": kind, "payload": redact(payload)}
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            self.count += 1
        except OSError as e:
            log.warning("capture write failed, disabling: %s", e)
            self._failed = True
