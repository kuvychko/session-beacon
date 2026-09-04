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
- Session labels are derived from `cwd` basename; overrides go in `host/config.local.toml`, not in code.
- Hook forwarding is done by Windows' built-in `curl.exe`, not a Python script, because process startup dominates hook cost. Do not "simplify" the hook commands back to a script without re-measuring.
- `POST /event` must reply with an empty body. Claude Code feeds some hooks' stdout back into the session as context.
- The daemon reads `host/config.local.toml` or `host/config.toml`. Both are gitignored. Do not narrow this back to one name: the other is the one people reach for, and silently ignoring it is indistinguishable from a broken daemon.
- A blank display with the daemon connected almost always means the hooks are not installed. `/health` reports `events_received` so this is one curl away.

## Commands

```powershell
cd host
uv sync
uv run pytest
uv run beacon-host --dry-run -v      # print snapshots, no hardware needed
uv run beacon-host --port COM4 -v    # drive the display

curl.exe -s http://127.0.0.1:47391/health   # events_received, sessions, device
./scripts/install-hooks.ps1          # Claude Code hooks; -Uninstall to remove
./scripts/install-task.ps1           # run at logon; -Uninstall to remove
```

Hooks are read at session start. After `install-hooks.ps1` the user must
restart their Claude Code sessions or nothing will arrive.

Firmware builds headlessly with the arduino-cli bundled inside the Arduino IDE install. Compile before handing firmware over; it catches real problems, such as `LINE_MAX` colliding with a POSIX macro from limits.h.

```powershell
$cli = "$env:LOCALAPPDATA/Programs/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe"
& $cli compile --fqbn arduino:esp32:nano_nora firmware/beacon
& $cli upload  --fqbn arduino:esp32:nano_nora -p COM4 firmware/beacon
& $cli board list        # find the port if COM4 has moved
```

Required libraries are installed: Adafruit GFX 1.12.4, Adafruit ST7735/ST7789 1.11.0, ArduinoJson 7.4.3.

## Do not

- Do not commit `host/config.local.toml` or anything containing real session transcripts.
- Do not add Wi-Fi, MQTT, or cloud features. USB only by design.
- Do not spawn subagents for this project unless asked; it is small.
