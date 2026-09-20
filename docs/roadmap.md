# Roadmap

**Where this stands:** the beacon has been in daily use since early September, and
there are now two complete builds. Both run firmware 0.3.0 from one source, on the same
daemon, hooks, enclosure family and stand: the original **Arduino Nano ESP32** and a
**Waveshare RP2040-Zero**, which is smaller and cheaper and gives up nothing the project
uses.

The document goes from what is open to what is already done: the smaller open items, the
things decided against, the recent hardening work, and then the history.

## Smaller open items

These are display work. The data is already there, so each item needs a layout decision
rather than plumbing.

- **Last tool name per row.** The host records it and the protocol carries a `tool`
  field, but nothing draws it. There is no room on a row without giving up label width.
- **"More below" indicator.** With more than six sessions, the host sorts and truncates
  correctly and the header's active count reveals the overflow. Nothing on the screen
  says there are more rows.
- **Subagent count on a row.** The host already tracks how many are outstanding
  (`SubagentStop` is registered).

## Maybe later

- Session history: how long each session spent waiting on you today. This would need a
  tiny SQLite store in the host.
- Linux host support. Only the hook command line and the serial device path differ.
- Tray icon for the daemon, with "show log" and "quit".
- A second page, or scrolling, for more than six sessions, driven by a button on the
  device.

## Decided against

- **Piezo or LED cue on `NEEDS_INPUT`.** Not wanted. The red row and its decaying pulse
  are enough.
- **Backlight on a PWM pin, dimming when idle.** The panel's brightness is fine as it
  is, and this would mean moving a wire. The protocol's `bl` field stays reserved and
  ignored.
- **Native `http` hooks in place of `curl`.** `statusLine` is command-only, so `curl`
  would stay on the busiest path either way. The events it would save cost under a
  second an hour in total. See
  [architecture.md](architecture.md#native-http-hooks-were-evaluated-and-not-adopted)
  for the evidence and for what would change the answer.
- **Wi-Fi, MQTT, cloud.** USB only, by design. See
  [non-goals](architecture.md#non-goals-for-now).

## Recent hardening (7–18 September)

Once the beacon was in daily use, it began showing the wrong thing in ways a test bench
never would. Every fix below started from a real observation or a captured payload, and
each is guarded by a test, a fixture or a rule in `CLAUDE.md`.

| Date | What was seen | Cause | Fix |
|---|---|---|---|
| 09-07 | Two sessions pulsed red for thirteen hours | The alarm never decayed | [The attention ladder](architecture.md#the-attention-ladder): two minutes of pulse, eight of red, then amber sorted below the working sessions |
| 09-07 | A turn waiting on a subagent went red | `Stop` was read as idle even with background work pending | `background_tasks` keeps it `WORKING` and holds back `idle_prompt` |
| 09-07 | A prompt answered "no" left the row red | A rejection runs no tool, so no `PostToolUse` arrives | `PostToolBatch` registered: it is the only event that arrives when a human answers |
| 09-07 | Waiting sessions vanished on every daemon restart | State lived only in memory, and a parked session sends nothing | Written to `sessions.json` atomically, restored at start |
| 09-08 | A session blocked on you showed blue | A subagent's events carry the parent's `session_id` | Tell them apart by `agent_id`. `SubagentStop` recomputes the count |
| 09-09 | A finished session showed blue, then amber, never red | An armed artifact comment monitor counted as work that never ends | Count only `subagent` tasks ([details](claude-code-integration.md#background_tasks-is-not-only-subagents)) |
| 09-10 | An orchestrator waiting on a workflow went full red | One severity for two different messages | [A cyan `look` rung](architecture.md#the-soft-rung-needs_look) for "quiet a while", which graduates to red after `look_s` |
| 09-10 | A session killed by Windows Update stayed on screen for 45 hours | Restored from disk as though it were parked | Nothing is restored across a reboot, and anything silent for 24 hours is dropped |
| 09-16 | The display froze eight times in eight days while `/health` said ok | The heartbeat's `printf` blocked forever on a USB pipe that had stopped draining | A non-blocking tx queue and the loop watchdog. The host reports `silent` ([details](architecture.md#usb-writes-must-never-wait)) |
| 09-17 | "Replug" shown for two hours against a board that was working | Only the return path was dead | `txWatchdog()` reboots the board to re-enumerate ([details](architecture.md#a-silent-board-is-not-a-frozen-one)) |
| 09-18 | A window closed after its turn stayed green for a day | Only `WORKING` had a staleness timer | `IDLE` with no event for `idle_stale_s` (10 min) goes stale |
| 09-18 | A daemon restart after flashing silently did nothing | A daemon started by hand was invisible to the Scheduled Task | `scripts/restart-daemon.ps1` is the only way to restart it, and `/health` reports the pid |
| 09-18 | No heartbeat for five hours while the board rendered every snapshot, and `txWatchdog()` never fired | It trusted `hostAttached()`, which read true over a FIFO that never drained, or kept flickering back to true | The watchdog trusts bytes written instead. Each cure records `wcause` and `wflip`, and `stall` tests the new path. Firmware 0.2.2 ([details](architecture.md#a-silent-board-is-not-a-frozen-one)) |

## How we got here

**Phase 0: scoping (4 Sep).** Docs, repo layout, protocol, skeletons.

**Phase 1: blink the beacon.** Captured real hook payloads, which disagreed with the
published schema in four places, and committed them as fixtures. Brought up the panel,
which turned out to be BGR-wired. Built the firmware renderer, the host state machine,
the hook server and the serial link. Hooks forward with `curl`. The first hardware bug
was an unsigned `millis()` underflow that painted "no host" over every frame.

Exit criteria, which still define correct behaviour:
- leaving a session on a permission prompt turns its row red within one second
- answering it turns it blue
- `Stop` turns it green
- left unanswered, the pulse stops after two minutes and the red after ten

**Phase 2: make it a daily driver.**
- COM port auto-detected by VID/PID, with reconnect after a reflash or unplug.
- Runs at logon as a Scheduled Task and logs to a rotating file.
- Hardware SPI at 24 MHz, with in-place field drawing instead of clear-then-draw, which
  ended a once-a-second flash.
- Sessions labelled by git repository, with overrides in config.
- The footer alternates between session context and cost, and the account's five-hour
  and seven-day usage ([details](architecture.md#the-footer-alternates)).
- A three-part printed enclosure and a 25-degree stand ([enclosure.md](enclosure.md)).
- An uninstall script that undoes everything the installers did.

**Phase 3** is the list in "Recent hardening" above. None of it was planned; all of it
came from living with the device.

## The RP2040-Zero edition

**Why.** Most of what the Nano ESP32 costs is its Wi-Fi and Bluetooth, and this project
uses neither on purpose ([non-goals](architecture.md#non-goals-for-now)). The
RP2040-Zero is far smaller and cheaper, has USB-C, and is 3.3 V logic, so the display
still needs no level shifting. The ground rules were that the protocol does not change,
the firmware stays dumb, and the Nano build stays supported as the reference.

**One sketch, not two.** Almost every hard-won rule in `CLAUDE.md` lives in the shared
rendering and parsing code, and two copies would let the fixes drift apart. Only four
things sit behind `#if defined(ARDUINO_ARCH_RP2040)`: the pins and SPI bus,
`hostAttached()`, the platform block (reset reason, reboot, loop watchdog) and where the
wedge counters live.

**What the port turned up**, none of it assumed from the ESP32 findings:

- The RP2040 core's `write()` gives up after a second rather than blocking forever, so
  the fault that froze the Nano eight times in eight days cannot happen here. Both
  builds keep the tx queue anyway: a second is still most of a frame.
- `Serial` as a boolean is `tud_cdc_connected()`, the same predicate the Nano build
  spells out by hand, so `hostAttached()` can use it. The warning against `if (Serial)`
  is about the ESP32 core's separate flag and does not carry over.
- There is no RTC memory, so the counters that outlive a cure live in the watchdog's
  scratch registers 0 to 3. Registers 4 to 7 hold what the SDK leaves the bootrom.
- `rp2040.reboot()` is itself a watchdog reboot and `getResetReason()` cannot tell it
  from the loop watchdog firing, so a cure marks itself and `resetReason()` reads and
  clears that mark at boot. Found on the board, where every cure reported `rst` as
  `wdt`.
- The bootloader trigger is a 1200-baud open, not a DTR/RTS pattern. The daemon never
  changes the baud rate, so the rule that the host never revives a board through the
  port is unchanged.

**Verified on the assembled unit**: the smoke test's geometry, text and layout mock at
24 MHz, with the same BGR fix the Nano's panel needs; `hang` rebooting it in 5.0 s; and
`scripts/wedge-bench.py` passing both `stall` (`wcause=att`) and `wedge` (`wcause=det`),
each rebooting 30.2 s in with the count surviving. The same three tests pass unchanged
on the Nano, which is the evidence that one sketch for two boards cost the reference
build nothing. The daemon finds either board by USB id, and the heartbeat's `board`
field names the one that answered.

The enclosure is `mid-rp2040-v0` and `back-rp2040-v0`, sharing `front-v0`, the stand,
the M2 x 16 screws and the 40 x 60 x 19 mm envelope with the Nano build. The two cases
are identical from the outside, which is why there is no separate photo of the finished
unit; the build-stage photos are in [enclosure.md](enclosure.md#two-builds).

## Open questions

- **The null context case is untested on real data.** `used_percentage` is null early in
  a session and after `/compact`. The token-count fallback that covers it has only been
  exercised by unit tests. See
  [claude-code-integration.md](claude-code-integration.md#still-to-confirm).

Settled since the last revision:
- **RP2040: one sketch or two?** One, with a thin per-board layer. See
  [the RP2040-Zero edition](#the-rp2040-zero-edition).
- The statusline carries the context percentage directly.
- Hooks run fine through the `curl` command lines on Windows.
- `idle_nag_s` was never needed. Claude Code's own `idle_prompt` arrives a few minutes
  into an idle wait and climbs the attention ladder, and `idle_stale_s` handles a window
  that was closed instead.
