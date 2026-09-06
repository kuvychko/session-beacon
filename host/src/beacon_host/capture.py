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
