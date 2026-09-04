# Roadmap

Phases are ordered so that each one produces something visibly useful on the desk. Estimates are rough and assume Claude Code does most of the typing.

## Phase 0: Scoping (done)

Docs, repo layout, protocol, skeletons. This is where the repo stands now.

## Phase 1: Blink the beacon

Goal: the screen shows real session states from real Claude Code sessions.

1. Confirm hook payload shapes (see the first-run checklist in [claude-code-integration.md](claude-code-integration.md)). Save fixtures.
2. ~~Wire the display and flash `firmware/tft_smoketest/tft_smoketest.ino`.~~ **Done.** Wiring, offsets, rotation, and text metrics all check out. The panel turned out to be BGR-wired and needed a colour-order register fix, now applied in both sketches. See [hardware.md](hardware.md#panel-colour-order).
3. Firmware: parse `snap`, draw header, six rows, footer, "no host" screen. Test with snapshot lines pasted into the Arduino serial monitor before the host exists.
4. Host: `state.py` with unit tests against the fixtures. `hook_server.py` and `serial_link.py`. Run from a terminal with `--port COMx`.
5. Hook forwarder and settings snippet. Install, open three VS Code windows, watch the beacon.

Exit criteria: leaving a session on a permission prompt turns its row red within one second; answering it turns it blue; `Stop` turns it green.

## Phase 2: Make it a daily driver

- Auto-detect COM port by VID/PID; reconnect after reflash or unplug.
- Run at login (Task Scheduler, `pythonw`), log to a rotating file.
- Cost and context percent in the footer from the statusline payload.
- Last tool name per row.
- Blink and stale handling polished; overflow count when more than six sessions.
- Label overrides in config.
- Backlight on PWM pin, dim after N minutes of all-idle.

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
- Do hooks on Windows run through cmd, PowerShell, or Git Bash? Affects the `command` string quoting. Test in Phase 1 step 4.
- Should `IDLE` after a `Stop` count as "needs you"? Arguably yes after some minutes: Claude finished and you have not looked. Start with a configurable `idle_nag_s` (default off) and see what feels right.
