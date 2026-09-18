# Host to device protocol

USB CDC serial, 115200 baud, newline-delimited JSON. One message per line, UTF-8, compact (no whitespace). Host to device only in phase 1. An optional device heartbeat is described at the end.

Design goals: readable on a serial monitor, trivial to parse with ArduinoJson, and a full snapshot with 6 sessions fits in one line well under 1 KB.

## Snapshot

Sent whenever host state changes, and at least once per second regardless, so the ages on screen keep advancing. The host does not check whether anything is happening first: the snapshot is small and the device repaints only the fields that changed, so an idle desk costs a line a second and no drawing. The device replaces its entire view with each snapshot. There are no deltas.

```json
{"t":"snap","v":1,"ts":1725480000,"n":4,"cost":4.2,"sel":1,"rl":{"h5":92,"d7":28},
 "s":[
  {"id":"a1b2c3d4","l":"session-beacon","st":"work","age":120,"ctx":62,"m":"fable5.1"},
  {"id":"e5f6a7b8","l":"env_monitoring","st":"held","age":840,"ctx":41,"m":"opus"},
  {"id":"c9d0e1f2","l":"homelab","st":"idle","age":5},
  {"id":"a3b4c5d6","l":"data-pipeline","st":"stale","age":360}
 ]}
```

Top level:

| Field | Type | Meaning |
|-------|------|---------|
| `t` | string | Message type. `snap` for snapshot. |
| `v` | int | Protocol version. Device shows an error on an unknown version. |
| `ts` | int | Host unix time. Informational. |
| `n` | int | Total live sessions on host. May exceed the length of `s` if truncated. |
| `cost` | float | Sum of `total_cost_usd` across live sessions. Omitted if unknown. |
| `sel` | int | Index into `s` of the featured session shown in the footer. Host picks the most urgent. |
| `s` | array | Sessions, already sorted by the host in display order. Max 6. |
| `rl` | object | Account usage: `h5` and `d7` are used percentages for the rolling five-hour and seven-day windows. Either key may be absent. Omitted entirely when no statusline has reported in the last five minutes, because a stale percentage is worse than none. |

Session object:

| Field | Type | Meaning |
|-------|------|---------|
| `id` | string | First 8 chars of `session_id`. The device stores it and does not currently use it; rows are matched by position. Useful when reading a snapshot on a serial monitor. |
| `l` | string | Label, max 16 chars. The host shortens longer ones from the middle, keeping 9 chars from the start and 5 from the end around `..` (`inventory-service2` becomes `inventory..vice2`), because clones of one repo usually differ only in a suffix. |
| `st` | string | `start`, `work`, `need`, `held`, `look`, `wait`, `err`, `idle`, `stale`, `end`. Drives the row's dot colour. `need` and `held` fill the whole row; only `need` pulses. |
| `age` | int | Seconds in current state. Across `need`, `held` and `wait` it keeps running rather than restarting at each rung, so it is the whole time the session has been waiting on a human. A `look` row that graduates to `need` starts again at zero, because time spent in `look` was not time spent waiting on a human. |
| `ctx` | int | Context window used, percent. Optional. |
| `m` | string | Model short name, max 8 chars. Optional. Shown only for the featured session. |
| `tool` | string | Last tool name, max 10 chars. Optional. The host sends it whenever it knows one; the device does not read it yet, because a row has no space for it. See [roadmap.md](roadmap.md). |

## Other host messages

```json
{"t":"hello","v":1,"host":"IGOR-PC"}
```
Sent on connect and reconnect. It refreshes the device's last-message timestamp, which is what stops the "no host" screen appearing; the screen is actually cleared by the next snapshot, since `hello` carries nothing to draw.

```json
{"t":"bl","pct":40}
```
Backlight brightness 0 to 100. Ignored until BL is wired to a PWM pin.

## Device to host (optional)

```json
{"t":"hb","fw":"0.2.2","up":3600,"rx":142,"bad":0,"drop":0,"txdrop":0,"aflip":1,"wedge":0,"wcause":"none","wflip":0,"since":412,"render":28,"spi":"hw","rst":"power"}
```
Heartbeat every 3 s. `txdrop` and `rst` were added in firmware 0.2.0, `aflip` and `wedge` in 0.2.1, and `wcause` and `wflip` in 0.2.2; the host treats all six as optional.

| Field | Meaning |
|-------|---------|
| `fw` | Firmware version |
| `up` | Seconds since boot |
| `rx` | Lines accepted |
| `bad` | Lines that failed to parse |
| `drop` | Lines discarded for exceeding the buffer |
| `txdrop` | Outgoing lines dropped because the USB pipe was not draining while a host held the port |
| `aflip` | Times the device's view of "a host holds the port" has changed since boot. 1 after an ordinary connect. A climbing count is a flapping link |
| `wedge` | Times the device has rebooted itself to recover a dead serial return path. Kept across that reboot, so it is a lifetime count, not a since-boot one |
| `wcause` | What the last of those reboots saw, kept with `wedge`: `det` if the device believed no host held the port, `att` if it believed one did but nothing it wrote was being collected, `none` if there has been none |
| `wflip` | How many times that belief changed during the last such episode. Many means a flapping link rather than a stuck one |
| `since` | Milliseconds since the last accepted message |
| `render` | Duration of the most recent repaint, ms |
| `spi` | `hw` or `sw`, which SPI path is compiled in |
| `rst` | Why the board last reset: `power`, `sw`, `panic`, `int_wdt`, `task_wdt`, `wdt`, `brownout` or `other`. A watchdog reset or `panic` means the watchdog rescued a hung `loop()`. |

These exist because a quiet host and a device that is dropping or failing to parse lines look identical from the outside: a screen reading "no host".

The host acts on the heartbeat as well as logging it. If the port is open and no heartbeat has arrived for 15 s, counting from the later of the port opening and the last heartbeat, the device state is `silent`. `/health` then reports `"device": false`, the status line says `beacon silent`, and the daemon logs one warning. If `up` goes down between heartbeats, even across a reconnect, the host logs a reboot and its `rst`; if `wedge` goes up, it logs that the board recovered its own return path, and which of `wcause`'s two faults it was.

`silent` does not mean the display has stopped. It means only that nothing is coming back, which is equally consistent with a sketch that has stopped and with a return path that has died under a display still rendering every snapshot it is sent. The host cannot separate those and does not pretend to; the device can, and cures the second itself. See [architecture.md](architecture.md).

The device never blocks to send a heartbeat. If the pipe is not draining, lines are dropped and counted in `txdrop`.

## Rules

- Lines longer than 1024 bytes are discarded by the device, including the remainder after the overflow, and counted in the heartbeat's `drop`.
- Unknown fields are ignored on both sides. Add fields freely; bump `v` only for breaking changes.
- A new `st` value is not a breaking change. The device falls back to `start`'s grey for anything it does not recognise, so an old device driven by a new host renders the new state plainly rather than failing. `held`, `wait` and `look` were added this way: bumping `v` for them would have replaced a grey dot with a full-screen `protocol` error on every device not yet reflashed, which is the worse outcome by a wide margin. Bump `v` when the device would otherwise draw something *wrong*, not merely something dull.
- The device shows a "no host" screen after 10 s without any message.
- No ACKs. A corrupted line is fixed by the next snapshot.
