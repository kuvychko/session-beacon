"""Compose the terminal status line returned to the statusline hook.

This runs in the HTTP handler thread and is a pure function of the payload it
was just handed, so it needs no shared state and no locking. The only outside
fact it uses is whether the device is currently connected, passed in.

Keeping this on the daemon side is what lets the statusline hook be a plain
curl call: curl POSTs the payload and prints the response body verbatim.
"""

from __future__ import annotations

import os
from typing import Any

from .state import extract_ctx_pct


def compose(st: dict[str, Any], device_ok: bool) -> str:
    """Build a one-line status string. Never raises; returns "" on bad input."""
    try:
        parts: list[str] = []

        model = (st.get("model") or {}).get("display_name")
        if model:
            parts.append(str(model))

        cwd = (st.get("workspace") or {}).get("current_dir") or st.get("cwd")
        if cwd:
            base = os.path.basename(str(cwd).replace("\\", "/").rstrip("/"))
            if base:
                parts.append(base)

        pct = extract_ctx_pct(st)
        if pct is not None:
            parts.append(f"ctx {pct}%")

        cost = (st.get("cost") or {}).get("total_cost_usd")
        if isinstance(cost, (int, float)):
            parts.append(f"${cost:.2f}")

        # A quiet marker, so a dead daemon or unplugged device is visible in
        # the terminal without going to look at the beacon itself.
        parts.append("beacon" if device_ok else "beacon?")

        return "  ".join(parts)
    except Exception:
        return ""
