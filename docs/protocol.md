# Host to device protocol

USB CDC serial, 115200 baud, newline-delimited JSON. One message per line, UTF-8, compact (no whitespace). Host to device only in phase 1. An optional device heartbeat is described at the end.

Design goals: readable on a serial monitor, trivial to parse with ArduinoJson, and a full snapshot with 6 sessions fits in one line well under 1 KB.

## Snapshot

Sent whenever host state changes, and at least once per second while any session is not `idle` so on-device timers stay honest. The device replaces its entire view with each snapshot. There are no deltas.

```json
{"t":"snap","v":1,"ts":1725480000,"n":4,"cost":4.2,"sel":1,
 "s":[
  {"id":"a1b2c3d4","l":"session-beacon","st":"work","age":120,"ctx":62,"m":"fable"},
  {"id":"e5f6a7b8","l":"env_monitoring","st":"need","age":840,"ctx":41,"m":"opus"},
  {"id":"c9d0e1f2","l":"homelab","st":"idle","age":5},
  {"id":"a3b4c5d6","l":"factory-dyn","st":"stale","age":360}
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

Session object:

| Field | Type | Meaning |
|-------|------|---------|
| `id` | string | First 8 chars of `session_id`. Used on device only for change detection. |
| `l` | string | Label, max 14 chars, truncated by host. |
| `st` | string | `start`, `work`, `need`, `idle`, `stale`, `end`. |
| `age` | int | Seconds in current state. |
| `ctx` | int | Context window used, percent. Optional. |
| `m` | string | Model short name, max 8 chars. Optional. |
| `tool` | string | Last tool name, max 10 chars. Optional, phase 2. |

## Other host messages

```json
{"t":"hello","v":1,"host":"IGOR-PC"}
```
Sent on connect and reconnect. Device clears the "no host" screen.

```json
{"t":"bl","pct":40}
```
Backlight brightness 0 to 100. Ignored until BL is wired to a PWM pin.

## Device to host (optional)

```json
{"t":"hb","fw":"0.1.0","up":3600}
```
Heartbeat every 10 s. Host logs it and uses its absence to detect a wedged device.

## Rules

- Lines longer than 1024 bytes are discarded by the device.
- Unknown fields are ignored on both sides. Add fields freely; bump `v` only for breaking changes.
- The device shows a "no host" screen after 10 s without any message.
- No ACKs. A corrupted line is fixed by the next snapshot.
