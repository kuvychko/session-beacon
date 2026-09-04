"""Append raw Claude Code hook payloads to a JSONL file, for building fixtures.

This is the tool for step one of the first-run checklist in
docs/claude-code-integration.md. It records what Claude Code actually sends on
this machine, so the state machine tests can be pointed at real payloads
instead of hand-written dictionaries.

Point a hook at it temporarily, in a scratch project rather than globally:

    "PermissionRequest": [{ "hooks": [{ "type": "command",
      "command": "python C:/Repos/session-beacon/hooks/capture_payloads.py" }] }]

Then trigger the events you care about and inspect the file:

    Get-Content $env:LOCALAPPDATA/session-beacon/payloads.jsonl | ConvertFrom-Json

Prints nothing and always exits 0, so it cannot disturb a session.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

OUT = Path(os.environ.get("LOCALAPPDATA", ".")) / "session-beacon" / "payloads.jsonl"


def main() -> int:
    try:
        raw = sys.stdin.buffer.read()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"_unparsed": raw.decode("utf-8", "replace")}
        record = {"_captured_at": time.time(), "_payload": payload}

        OUT.parent.mkdir(parents=True, exist_ok=True)
        with OUT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # never disturb the session, whatever happens
    return 0


if __name__ == "__main__":
    sys.exit(main())
