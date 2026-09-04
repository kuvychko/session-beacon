"""Forward a Claude Code hook or statusline payload to the beacon-host daemon.

Invoked by Claude Code with the event JSON on stdin. Posts it to the local
daemon and exits. Must be fast and must never fail loudly: if the daemon is
down the event is dropped and we exit 0.

Usage (from ~/.claude/settings.json):
    python C:/Repos/session-beacon/hooks/beacon_hook.py
    python C:/Repos/session-beacon/hooks/beacon_hook.py --statusline

In --statusline mode the payload goes to /status instead of /event and a
one-line status string is printed so the terminal statusline still works.
"""

from __future__ import annotations

import json
import sys
import urllib.request

DAEMON = "http://127.0.0.1:47391"
TIMEOUT_S = 0.2


def post(path: str, raw: bytes) -> None:
    req = urllib.request.Request(
        DAEMON + path,
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT_S).read()
    except Exception:
        pass  # daemon not running or slow; drop silently


def statusline_text(raw: bytes) -> str:
    """Minimal fallback statusline. Replace or wrap with your own later."""
    try:
        d = json.loads(raw)
        model = d.get("model", {}).get("display_name", "?")
        cost = d.get("cost", {}).get("total_cost_usd")
        cost_s = f" ${cost:.2f}" if isinstance(cost, (int, float)) else ""
        return f"{model}{cost_s}"
    except Exception:
        return ""


def main() -> int:
    raw = sys.stdin.buffer.read()
    if "--statusline" in sys.argv[1:]:
        post("/status", raw)
        print(statusline_text(raw))
    else:
        post("/event", raw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
