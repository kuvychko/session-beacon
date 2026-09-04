# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A USB desk gadget (Arduino Nano ESP32 + 1.8" ST7735 TFT) that shows the status of every Claude Code session on the PC: which are working, which are blocked on the user, plus cost and context usage. Windows host first.

Read `docs/architecture.md` before changing anything. The protocol in `docs/protocol.md` is the contract between host and firmware; change it in both places and bump `v` for breaking changes.

## Layout

```
firmware/beacon/beacon.ino   Arduino sketch. Arduino IDE, board "Arduino Nano ESP32".
host/                        Python 3.12+ package, managed with uv. Entry point: beacon-host.
  src/beacon_host/           main, hook_server, state, serial_link, config
  tests/                     pytest; fixtures are real hook payloads captured from Claude Code
hooks/beacon_hook.py         Forwarder invoked by Claude Code hooks and statusline. Must stay fast.
hooks/settings.example.json  Snippet to merge into ~/.claude/settings.json
docs/                        architecture, hardware, protocol, claude-code-integration, roadmap
```

## Conventions

- Firmware uses Arduino pin names (`D10`), never raw GPIO numbers. Wiring is in `docs/hardware.md` and matches the `env_monitoring` project at `C:\Repos\env_monitoring`.
- Firmware is dumb: it renders what the host sends. Do not add policy (sorting, thresholds, labels) to the firmware.
- The hook forwarder must never block or fail loudly. Any error means exit 0 with nothing on stdout (except in `--statusline` mode, where it must still print a status line).
- Host state logic lives in `state.py` and is pure (no I/O) so it can be unit tested with fixtures.
- Session labels are derived from `cwd` basename; overrides go in `host/config.toml`, not in code.

## Commands

```powershell
cd host
uv sync
uv run pytest
uv run beacon-host --port COM5 --log-level DEBUG
```

Firmware is built and flashed from the Arduino IDE. There is no CLI build yet.

## Do not

- Do not commit `host/config.local.toml` or anything containing real session transcripts.
- Do not add Wi-Fi, MQTT, or cloud features. USB only by design.
- Do not spawn subagents for this project unless asked; it is small.
