"""Configuration loading. TOML file plus CLI overrides."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HTTP_PORT = 47391


@dataclass
class Config:
    port: str | None = None           # COM port; None means auto-detect by VID/PID
    http_port: int = DEFAULT_HTTP_PORT
    stale_after_s: float = 300.0
    need_pulse_s: float = 120.0       # how long an attention row pulses
    need_red_s: float = 600.0         # how long it stays red at all
    # How long an idle_prompt softened by untracked background work (a workflow,
    # a background shell) stays a calm `look` row before joining the ladder.
    look_s: float = 300.0
    # How long an outstanding background-task count is believed without fresh
    # evidence, before an idle_prompt is allowed through anyway.
    bg_quiet_s: float = 180.0
    ended_grace_s: float = 30.0
    max_rows: int = 6
    log_file: str | None = None       # None means stderr only
    state_file: str | None = None     # None means the default path below
    # A session parked overnight is still a session; one untouched for longer
    # than this is more likely a window closed while the daemon was down, whose
    # SessionEnd went nowhere. Restoring that would show a row that never moves.
    # The running store applies the same cutoff, so a restart cannot renew one.
    restore_max_age_s: float = 86400.0
    log_level: str = "INFO"
    labels: dict[str, str] = field(default_factory=dict)
    source: Path | None = None        # which file this came from, for logging

    @classmethod
    def load(cls, path: str | Path | None) -> Config:
        """Read a TOML config. A missing file is not an error; defaults apply."""
        cfg = cls()
        if path is None:
            return cfg
        p = Path(path)
        if not p.is_file():
            return cfg
        cfg.source = p
        with p.open("rb") as f:
            raw = tomllib.load(f)

        for key in ("port", "http_port", "stale_after_s", "need_pulse_s",
                    "need_red_s", "look_s", "bg_quiet_s", "ended_grace_s", "max_rows",
                    "log_file", "state_file", "restore_max_age_s", "log_level"):
            if key in raw:
                setattr(cfg, key, raw[key])
        # Label keys are paths; normalise separators so either form works.
        cfg.labels = {
            str(k).replace("\\", "/").rstrip("/"): str(v)
            for k, v in (raw.get("labels") or {}).items()
        }
        return cfg


#  config.toml is listed because it is the name people reach for first, and
#  silently ignoring it looks exactly like the daemon ignoring your settings.
CONFIG_NAMES = ("config.local.toml", "config.toml")


def default_state_path() -> Path:
    """Where session state is kept across restarts.

    The same directory the scheduled task logs to, so `uninstall.ps1` already
    removes it: that script clears the whole directory, and anything installed
    outside the repository has to be removable by it.
    """
    if base := os.environ.get("LOCALAPPDATA"):
        return Path(base) / "session-beacon" / "sessions.json"
    return Path.home() / ".local" / "state" / "session-beacon" / "sessions.json"


def default_config_path() -> Path | None:
    """First recognised config file in the host project root, if any."""
    root = Path(__file__).resolve().parents[2]
    for name in CONFIG_NAMES:
        p = root / name
        if p.is_file():
            return p
    return None
