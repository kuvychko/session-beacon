# Roadmap

**Where this stands:** the beacon has been in daily use since early September. The Nano
ESP32 build is complete: firmware 0.3.0, host daemon, hooks, enclosure and stand. The
**Waveshare RP2040-Zero** build now runs the same firmware from the same source, on the
same daemon; what is left is living with it for a day and a photo.

The document goes from what comes next to what is already done: the next build, the
smaller open items, the things decided against, the recent hardening work, and then
the history.

## Next: RP2040-Zero edition

**Why.** Most of what the Nano ESP32 costs is its Wi-Fi and Bluetooth, and this project
uses neither on purpose ([non-goals](architecture.md#non-goals-for-now)). The
RP2040-Zero is far smaller and cheaper, has USB-C, and uses 3.3 V logic, so the display
still needs no level shifting. The port notes written before this was planned are in
[enclosure.md](enclosure.md#two-builds).

**Ground rules.**

- The protocol does not change. The host should not care which board is on the other end.
- The firmware stays dumb. The port moves code between boards and adds no behaviour.
- The Nano ESP32 build stays supported. It is the reference, and every fix learned on it
  must carry over.

**Work.** Each item came from ESP32-specific code or hardware already in the tree:

- [x] **Wiring.** All eight wires on one edge, the display on SPI1 (`14` SCK, `15` MOSI)
  with CS, DC and RST on `28`, `27` and `26`. Documented in
  [hardware.md](hardware.md#rp2040-zero) and checked on the bench with the smoke test:
  geometry, text and the layout mock all correct at 24 MHz, and the panel needs the same
  BGR `applyPanelColorOrder()` write as the Nano's.
- [x] **USB serial layer.** Re-checked rather than assumed, and the RP2040 core differs
  in three ways that are now in [hardware.md](hardware.md#usb-serial-notes): its
  `write()` gives up after a second instead of blocking forever, `Serial` as a boolean
  is `tud_cdc_connected()` (the predicate the Nano build spells out by hand), and there
  is no `setRxBufferSize()`. Both builds keep the tx queue.
- [x] **Display bus.** `Adafruit_ST7735(&SPI1, ...)`, with `SPI1.setSCK()`/`setTX()`
  before the library's `begin()`. Pins are named by their silkscreen numbers, since the
  Nano's `D10`-style names do not exist here. `tft_smoketest` builds for both boards too.
- [x] **Watchdog and reset survival.** `rp2040.wdt_begin(5000)` with the sketch feeding
  it at the end of `loop()`, verified with `hang`: reboot in 5.0 s, reported as `wdt`.
  The counters live in the watchdog's scratch registers 0 to 3, which survive the reboot
  that writes them. `rp2040.reboot()` is itself a watchdog reboot, so a cure marks
  itself and reports `sw`; without that mark every cure read `wdt`.
- [x] **Host detection.** `find_port()` matches either board, and the heartbeat's new
  `board` field names the build that answered. The RP2040's bootloader trigger is a
  1200-baud open, not a DTR/RTS pattern; the daemon never changes the baud rate, and the
  rule that the host never tries to revive a board through the port is unchanged.
- [x] **Build and flash.** FQBN `rp2040:rp2040:waveshare_rp2040_zero`, uploaded as a
  `.uf2`. Commands are in `CLAUDE.md`.
- [x] **Enclosure.** `mid-rp2040-v0` and `back-rp2040-v0`, printed and test-fitted.
  They share `front-v0`, the stand, the M2 x 16 screws and the 40 x 60 x 19 mm envelope
  with the Nano build. See [enclosure.md](enclosure.md#two-builds).
- [ ] **Docs.** A photo of the finished unit in its stand, next to the Nano's, and a BOM
  line for where the board was bought. The hardware table, wiring, toolchain, USB notes
  and the build-stage photos in [enclosure.md](enclosure.md#two-builds) are written.
- [ ] **A full working day** on the RP2040 build, which is what "done" means below.

**Settled:** one sketch, not a second `firmware/beacon_rp2040/`. Almost every hard-won
rule in `CLAUDE.md` lives in the shared rendering and parsing code, and two copies would
let the fixes drift apart. Only four things are behind
`#if defined(ARDUINO_ARCH_RP2040)`: the pins and SPI bus, `hostAttached()`, the platform
block (reset reason, reboot, loop watchdog) and where the wedge counters live.

**Done when:** an RP2040-Zero in its own case runs a full working day next to the Nano,
fed by the same daemon, with no difference in behaviour. The watchdogs are already
verified on it: `hang` rebooted it in 5.0 s, and `scripts/wedge-bench.py` passed with
both `stall` (`wcause=att`) and `wedge` (`wcause=det`), each rebooting 30.2 s in with the
count surviving.

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

## Open questions

- **RP2040: one sketch or two?** See [the open decision](#next-rp2040-zero-edition)
  above.
- **The null context case is untested on real data.** `used_percentage` is null early in
  a session and after `/compact`. The token-count fallback that covers it has only been
  exercised by unit tests. See
  [claude-code-integration.md](claude-code-integration.md#still-to-confirm).

Settled since the last revision:
- The statusline carries the context percentage directly.
- Hooks run fine through the `curl` command lines on Windows.
- `idle_nag_s` was never needed. Claude Code's own `idle_prompt` arrives a few minutes
  into an idle wait and climbs the attention ladder, and `idle_stale_s` handles a window
  that was closed instead.
