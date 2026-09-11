# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A USB desk gadget (Arduino Nano ESP32 + 1.8" ST7735 TFT) that shows the status of every Claude Code session on the PC: which are working, which are blocked on the user, plus cost and context usage. Windows host first.

Read `docs/architecture.md` before changing anything. The protocol in `docs/protocol.md` is the contract between host and firmware; change it in both places and bump `v` for breaking changes.

## Layout

```
firmware/beacon/beacon.ino   Arduino sketch. Arduino IDE, board "Arduino Nano ESP32".
host/                        Python 3.12+ package, managed with uv. Entry point: beacon-host.
  src/beacon_host/           main, hook_server, state, persist, serial_link, statusline, capture, config
  tests/                     pytest; fixtures are real hook payloads captured from Claude Code
hooks/beacon_hook.py         Forwarder invoked by Claude Code hooks and statusline. Must stay fast.
hooks/settings.example.json  Snippet to merge into ~/.claude/settings.json
docs/                        architecture, hardware, enclosure, protocol, claude-code-integration, roadmap
```

## Conventions

- Firmware uses Arduino pin names (`D10`), never raw GPIO numbers. Wiring is in `docs/hardware.md` and matches the `env_monitoring` project at `C:\Repos\env_monitoring`.
- The display panel is BGR-wired, so `applyPanelColorOrder()` must run after every `setRotation()` call. Without it red and blue render swapped. Both panels bought from this listing have been BGR, including `env_monitoring`'s, which had the same bug unnoticed for months. Do not "clean up" that register write.
- Firmware is dumb: it renders what the host sends. Do not add policy (sorting, thresholds, labels) to the firmware.
- The firmware drives the TFT over hardware SPI at 24 MHz, chosen by the 3-argument
  Adafruit_ST7735 constructor. `USE_HARDWARE_SPI 0` reverts to bit-banging the same
  pins. If the picture ever shows speckles or tearing, lower `SPI_HZ` before
  suspecting anything else.
- In firmware, never clear a region and then draw text into it. Use fixed-width fields
  at fixed positions with `setTextColor(fg, bg)` so glyphs overwrite themselves. Software
  SPI is slow enough that clear-then-draw is a visible flash on every update.
- In firmware, never write `now - then >= TIMEOUT` on `millis()` values. Use `elapsed(now, since, ms)`. Unsigned subtraction underflows to ~4.3 billion when the stored stamp is ahead of `now`, which fires every timeout at once; it also breaks on the 49-day rollover. This caused the display to alternate with "no host" once a second.
- `loop()` drains serial *before* taking `now`. Parsing repaints the screen and stamps `lastMsgMs` afterwards, so a `now` taken earlier would be in the past.
- The hook forwarder must never block or fail loudly. Any error means exit 0 with nothing on stdout (except in `--statusline` mode, where it must still print a status line).
- Host state logic lives in `state.py` and is pure (no I/O) so it can be unit tested with fixtures.
- `host/tests/fixtures/` holds **real** captured payloads, not hand-written ones: `hook_payloads.jsonl` for the lifecycle, one per event, and `stop_with_background_tasks.json` for the populated `background_tasks` a plain `Stop` never shows. Refresh it with `beacon-host --capture FILE`. The published hook schema disagrees with what this build actually sends, so prefer a capture over the docs when the two conflict.
- Capture redacts conversation content and every path but `cwd`, including free text nested inside `permission_suggestions`, `background_tasks` and a `PostToolBatch`'s `tool_calls`. Never commit a payload that has not been through `capture.redact`; a test guards every file in `host/tests/fixtures/`.
- Session labels are the enclosing git repository's name, not the `cwd` basename. `cwd`
  moves as a session works and labelling from it directly mislabels any session that
  runs in a subdirectory. Overrides go in `host/config.local.toml` or `host/config.toml`,
  keyed by repo root or exact cwd, not in code.
- Hook forwarding is done by Windows' built-in `curl.exe`, not a Python script, because process startup dominates hook cost. Do not "simplify" the hook commands back to a script without re-measuring.
- `PostToolBatch` is registered alongside `PostToolUse` and is not a duplicate of
  it. A prompt answered with "no" or with feedback runs no tool, so it produces
  no `PostToolUse`, and `PermissionDenied` fires only for auto-mode classifier
  denials; `PostToolBatch` is the only event that arrives when a human answers.
  Drop it and every rejected prompt leaves a red row until the next tool call.
- `running_background_tasks()` counts `type == "subagent"` and nothing else.
  `background_tasks` also carries `shell`, `monitor`, `workflow`, `MCP task`,
  `teammate` and `cloud session` entries, and an armed artifact comment monitor
  is a `monitor` registered for the life of the session. Counting those is what
  made a session that had finished its turn show blue for eight minutes and then
  amber, never red: only a subagent can be retired (`SubagentStop`) or confirmed
  alive (its own `agent_id` events), so counting anything else can only mute the
  display, and a monitor never ends. An unknown `type` is ignored for the same
  reason. Do not "generalise" this back to counting every entry. The other
  types have their own, weaker signal; see the next point.
- `bg_other` and `NEEDS_LOOK` (`look`, cyan) only ever decide how loud an
  `idle_prompt` is. Never feed `bg_other` into `bg_tasks` or into the
  `WORKING`/`IDLE` decision, and keep `monitor` out of it
  (`UNTRACKED_EXCLUDED_TYPES`). It counts unknown types, unlike `bg_tasks`,
  and that is only safe because `tick()` graduates `NEEDS_LOOK` to
  `NEEDS_INPUT` after `look_s`. Remove the graduation and a wrong guess hides
  a session for good, which is the monitor bug again.
- Labels longer than 16 characters are elided in the middle by
  `elide_label()`, not cut at the end. Clones differ only in their suffix.
- A held `idle_prompt` is remembered on the session, never dropped. Claude Code
  sent exactly one for the idle period that turned this up, so dropping it meant
  the row never went red. `tick()` releases it when the count retires.
- `SubagentStop` is registered for the same reason `PostToolBatch` is: it is the
  only event that recomputes `background_tasks` when a subagent ends. The count
  was otherwise refreshed only by the parent's next `Stop`, which never arrives
  if the parent has finished its turn and is waiting on the user, and a count
  stuck above zero suppresses every `idle_prompt` after it. Drop it and a
  session that used agents can sit blocked on you showing blue.
- A hook event's `agent_id` is the only thing separating a subagent's events
  from the parent's: they carry the parent's `session_id`. Never use
  `agent_type` for this, whatever it looks like it means -- it is also set on
  the main thread of a session started with `--agent`, without `agent_id`, so
  filtering on it would ignore that session's every tool call. A tool event
  clears an attention state only when its `agent_id` matches `attn_agent`, the
  one that raised the prompt; it refreshes the staleness timer either way,
  because a long subagent run is the only traffic its session produces.
- Restored sessions are ghosts until proven otherwise. `persist.load()`
  restores nothing if the machine booted after the file was saved, and
  `tick()` drops any session silent for `ghost_after_s` (`restore_max_age_s`,
  24 h) whatever its state. A Windows Update restart killed a session without
  a `SessionEnd`, and before these two checks it stayed on the display for 45
  hours. Keep both: the boot check misses Fast Startup, and the cutoff alone
  shows a ghost for a day.
- `POST /event` must reply with an empty body. Claude Code feeds some hooks' stdout back into the session as context.
- The daemon reads `host/config.local.toml` or `host/config.toml`. Both are gitignored. Do not narrow this back to one name: the other is the one people reach for, and silently ignoring it is indistinguishable from a broken daemon.
- A blank display with the daemon connected almost always means the hooks are not installed. `/health` reports `events_received` so this is one curl away.
- Anything that installs outside the repo must be removable by `scripts/uninstall.ps1`. It stops the daemon, drops the Scheduled Task, strips the hooks and clears the logs, and it must stay idempotent and leave user-authored things alone. `install-hooks.ps1` takes `-SettingsPath` so the round trip can be tested against a throwaway file.
- Neither install script writes or backs up settings.json when nothing would change, or repeat runs litter the directory with backups.

## Commands

From `host/`:

```powershell
uv sync
uv run pytest
uv run beacon-host --dry-run -v      # print snapshots, no hardware needed
uv run beacon-host --port COM4 -v    # drive the display
```

From the repository root, not `host/`:

```powershell
curl.exe -s http://127.0.0.1:47391/health   # events_received, sessions, device
./scripts/install-hooks.ps1          # Claude Code hooks; -Uninstall to remove
./scripts/install-task.ps1           # run at logon; -Uninstall to remove
./scripts/uninstall.ps1 -WhatIf      # undo everything; supports -WhatIf
```

Running the daemon in the foreground needs the port, so stop the scheduled task
first if it is installed: `Stop-ScheduledTask -TaskName SessionBeacon`, and
`Start-ScheduledTask` afterwards. Two daemons no longer share a port, so the
second one exits rather than quietly splitting the hook events.

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
