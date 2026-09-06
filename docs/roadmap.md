# Roadmap

Phases are ordered so that each one produces something visibly useful on the desk. Estimates are rough and assume Claude Code does most of the typing.

**Where this stands: phases 0 and 1 are complete and the device is in daily use.**
Most of phase 2 is done as well. What is left is listed under "still open" below.

## Phase 0: Scoping (done)

Docs, repo layout, protocol, skeletons.

## Phase 1: Blink the beacon

Goal: the screen shows real session states from real Claude Code sessions.

1. ~~Confirm hook payload shapes.~~ **Done.** Real payloads captured on this machine and committed as fixtures, with the tests replaying an actual session lifecycle. Capturing found four field names where the published schema disagrees with what this build sends. The attention events remain unobserved because they need an interactive permission prompt. See [claude-code-integration.md](claude-code-integration.md).
2. ~~Wire the display and flash `firmware/tft_smoketest/tft_smoketest.ino`.~~ **Done.** Wiring, offsets, rotation, and text metrics all check out. The panel turned out to be BGR-wired and needed a colour-order register fix, now applied in both sketches. See [hardware.md](hardware.md#panel-colour-order).
3. ~~Firmware: parse `snap`, draw header, rows, footer, "no host" screen.~~ **Done and verified on hardware** with the `demo` command.
4. ~~Host: state machine, hook server, serial link.~~ **Done.** TOML config, rotating log, `/health`, Scheduled Task installer. Verified end to end against the board.
5. ~~Hook forwarder and settings.~~ **Done.** Forwarding is `curl`, installed by `scripts/install-hooks.ps1`. Remaining: watch it with several real sessions running and confirm the exit criteria below.
6. ~~Stabilise on hardware.~~ **Done.** Fixed an unsigned-underflow timing bug that made the device alternate with "no host", and added receive counters to the heartbeat so host-side and device-side silence can be told apart.

Exit criteria: leaving a session on a permission prompt turns its row red within one second; answering it turns it blue; `Stop` turns it green.

## Phase 2: Make it a daily driver

Done:

- ~~Auto-detect COM port by VID/PID; reconnect after reflash or unplug.~~
- ~~Run at login (Task Scheduler, `pythonw`), log to a rotating file.~~ The script
  exists as `scripts/install-task.ps1`; installing it is a per-machine step.
- ~~Cost and context percent in the footer from the statusline payload.~~
- ~~Blink and stale handling polished.~~
- ~~Label overrides in config.~~ Keyed by repo root or exact directory.

Still open:

- **Last tool name per row.** The host already records it and the protocol carries a
  `tool` field; nothing draws it. There is no room on a row without giving up label
  width, so it needs a layout decision rather than plumbing.
- **Overflow indicator for more than six sessions.** The host sorts and truncates
  correctly and the header's active count reveals the overflow, but nothing says
  "there are more below".
- **Backlight on a PWM pin, dimming after some minutes of all-idle.** Needs one wire
  moved from 3V3 to a PWM-capable pin, so it is a hardware change, not just firmware.

### Account-level rate limits ~~(unbuilt)~~ **done**

The footer alternates every four seconds between the featured session's context and
the account's five-hour and seven-day usage. Both pages share the same field
positions so nothing jumps. Reset timestamps are available in the payload but not
yet shown; the percentage is the actionable number and there is no room for both.

### Enclosure ~~(TBD)~~ **done**

Three printed parts, fitted, in `enclosure/` as SolidWorks source, STEP and 3MF,
with print settings and the full bill of materials in [enclosure.md](enclosure.md).
An optional 25-degree desk stand is there too, printed and in use; the case is a
friction fit in it and nothing fastens the two together.

## Phase 3: Nice to have

- Small piezo or LED for an audible or peripheral-vision cue on `NEEDS_INPUT`. The screen alone may not be enough when looking at another monitor.
- Session history: how long each session spent waiting on you today. Would need a tiny SQLite store in the host.
- Linux host support: only the hook command line and serial device path differ.
- Hardware SPI and dirty-row rendering if flicker is noticeable.
- Show `SubagentStop` activity or a subagent count.
- Tray icon for the daemon with a "show log" and "quit" menu.
- Second page or scroll for more than six sessions, driven by a button on the device.

## Open questions

- Does the statusline payload carry context percent directly, or only token counts that need a per-model denominator? Decide in Phase 1 step 1.
- ~~Should the lifecycle hooks move to Claude Code's native `http` hook type and
  drop `curl`?~~ **Answered: no.** `statusLine` is command-only, so `curl` stays on
  the busiest path either way, and the events it would save cost under a second an
  hour in total. See
  [architecture.md](architecture.md#native-http-hooks-were-evaluated-and-not-adopted)
  for the evidence and for what would change the answer.
- Do hooks on Windows run through cmd, PowerShell, or Git Bash? Affects the `command` string quoting. Test in Phase 1 step 4.
- Should `IDLE` after a `Stop` count as "needs you"? Arguably yes after some minutes: Claude finished and you have not looked. Start with a configurable `idle_nag_s` (default off) and see what feels right.
