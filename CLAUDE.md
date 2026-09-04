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
- The display panel is BGR-wired, so `applyPanelColorOrder()` must run after every `setRotation()` call. Without it red and blue render swapped. This is a per-unit trait, not a property of the part number, and `env_monitoring` does not need it. Do not "clean up" that register write.
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

Firmware builds headlessly with the arduino-cli bundled inside the Arduino IDE install. Compile before handing firmware over; it catches real problems, such as `LINE_MAX` colliding with a POSIX macro from limits.h.

```powershell
$cli = "$env:LOCALAPPDATA\Programs\Arduino IDEesourcespp\libackendesourcesrduino-cli.exe"
& $cli compile --fqbn arduino:esp32:nano_nora firmware/beacon
& $cli upload  --fqbn arduino:esp32:nano_nora -p COM4 firmware/beacon
& $cli board list        # find the port if COM4 has moved
```

Required libraries are installed: Adafruit GFX 1.12.4, Adafruit ST7735/ST7789 1.11.0, ArduinoJson 7.4.3.

## Do not

- Do not commit `host/config.local.toml` or anything containing real session transcripts.
- Do not add Wi-Fi, MQTT, or cloud features. USB only by design.
- Do not spawn subagents for this project unless asked; it is small.
