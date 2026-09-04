"""Configuration loading. TOML file plus CLI overrides."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HTTP_PORT = 47391


@dataclass
class Config:
    port: str | None = None           # COM port; None means auto-detect by VID/PID
    http_port: int = DEFAULT_HTTP_PORT
    stale_after_s: float = 300.0
    ended_grace_s: float = 30.0
    max_rows: int = 6
    log_file: str | None = None       # None means stderr only
    log_level: str = "INFO"
    labels: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None) -> "Config":
        """Read a TOML config. A missing file is not an error; defaults apply."""
        cfg = cls()
        if path is None:
            return cfg
        p = Path(path)
        if not p.is_file():
            return cfg
        with p.open("rb") as f:
            raw = tomllib.load(f)

        for key in ("port", "http_port", "stale_after_s", "ended_grace_s",
                    "max_rows", "log_file", "log_level"):
            if key in raw:
                setattr(cfg, key, raw[key])
        # Label keys are paths; normalise separators so either form works.
        cfg.labels = {
            str(k).replace("\\", "/").rstrip("/"): str(v)
            for k, v in (raw.get("labels") or {}).items()
        }
        return cfg


def default_config_path() -> Path:
    """config.local.toml beside the package's project root, if it exists."""
    return Path(__file__).resolve().parents[2] / "config.local.toml"
