# Claude Code integration

Everything the beacon knows comes from two Claude Code features: **hooks** and the **statusline command**. Both are configured in `~/.claude/settings.json` and apply to every session on the machine, which is exactly what we want.

Field names below have been checked against payloads captured on this machine, not just against the published schemas, and the two disagree. Reference: [hooks](https://code.claude.com/docs/en/hooks), [statusline](https://code.claude.com/docs/en/statusline).

## Fields every hook receives

```json
{
  "session_id": "...",
  "prompt_id": "...",
  "transcript_path": "...",
  "cwd": "...",
  "permission_mode": "default|plan|acceptEdits|auto|dontAsk|bypassPermissions",
  "hook_event_name": "...",
  "agent_id": "...",
  "agent_type": "..."
}
```

Two of these are more useful than they first look:

- **`permission_mode`** says whether a session can block on you at all. One running in `bypassPermissions` will never raise a permission prompt, so a long silence there means something different than it does elsewhere. Captured now, displayed later.
- **`agent_id`** is the field that separates a subagent's events from the parent's,
  and it is now captured. A subagent's hooks carry the *parent's* `session_id` --
  the whole run in `host/tests/fixtures/subagent_run.jsonl` is a single
  `session_id` -- so nothing else distinguishes them. The CLI's own schema is
  blunt about which field to use: "Present only when the hook fires from within a
  subagent... Absent for the main thread, even in `--agent` sessions. **Use this
  field (not `agent_type`) to distinguish subagent calls from main-thread
  calls.**"
- **`agent_type`** names the agent (`Explore`, `general-purpose`) and is *not* a
  substitute. It is also set on the main thread of a session started with
  `--agent`, without `agent_id`, so filtering on it would ignore that session's
  every tool call.

`SessionStart` carries `source`, with the value `startup` observed. `SessionEnd` carries `reason`, with `other` observed. Both differ from the published schema; see the verification section below.

## Hook events we register

| Event | Fires when | Beacon transition |
|-------|-----------|-------------------|
| `SessionStart` | Session opens, resumes, clears, compacts, or forks | Create session as `STARTING` |
| `UserPromptSubmit` | You send a prompt | `WORKING`, clears any error |
| `PostToolUse` | After each tool call, carries `tool_name` | `WORKING`, refreshes the staleness timer |
| `PostToolBatch` | Once every call in a turn's batch has resolved, carries `tool_calls` | `WORKING`, and it is what answers a prompt |
| `PermissionRequest` | Claude Code needs permission for a tool | `NEEDS_INPUT`, if not already waiting |
| `PermissionDenied` | The auto-mode classifier denied a call, not a person; see below | `WORKING`, the prompt was answered |
| `Notification` | Claude Code wants attention, carries `notification_type` | `NEEDS_INPUT` for the attention types below, if not already waiting |
| `Stop` | Claude finished its turn, carries `background_tasks` | `IDLE`, or `WORKING` if a subagent is still running |
| `SubagentStop` | A subagent ended, carries `agent_id` and a fresh `background_tasks` | Recomputes the count; `IDLE` if that was the last of it and the turn had ended |
| `StopFailure` | The turn ended on an API error, carries `error_type` | `ERROR` |
| `SessionEnd` | Session closes | `ENDED` |

**`PermissionRequest` is a dedicated event.** An earlier draft of this design watched `Notification` for `permission_prompt` instead. The dedicated event is more precise and arrives without depending on notification settings, so it is now the primary signal, with the notification kept as a second path. Both are now captured, so neither is inference.

It carries `tool_name`, a redacted `tool_input`, and `permission_suggestions`: the rule Claude Code offers to add if you choose "allow always".

```json
"permission_suggestions": [{"behavior": "allow", "destination": "localSettings",
  "rules": [{"ruleContent": "...", "toolName": "Bash"}], "type": "addRules"}]
```

`ruleContent` is built from the command line, so it echoes the text that
redacting `tool_input` exists to strip. Capture marks it, along with a
background task's `description`; see below.

**`PostToolBatch` is what tells us a prompt was answered.** Answering *yes* runs
the tool, so `PostToolUse` arrives and the row clears. Answering *no*, or
rejecting a plan with feedback, runs nothing: no `PostToolUse`, and no
`PermissionDenied` either (see below), so before this event was registered
nothing at all arrived at the moment you answered. The row stayed red for as
long as Claude then spent thinking — 68 seconds in the session that turned this
up, where a plan was rejected with feedback at 16:11:50 and the next tool call
came at 16:12:58.

`PostToolBatch` closes that gap because a rejected call still *resolves*: it
fires once the batch is done, before the next model request, carrying the
refusal text as that call's `tool_response`. It is one hook per batch rather
than per tool, so it costs less than `PreToolUse` would and buys something
`PreToolUse` cannot: `PreToolUse` fires just before the *next* tool call, which
is exactly the moment the row was already clearing itself.

**`PreToolUse` is deliberately not registered.** `PostToolUse` alone is enough for activity and staleness, and skipping `PreToolUse` halves the hook cost on the busiest event. Turn it on only if a hint of what a session is *about* to do turns out to be worth the latency. The state machine handles it either way.

**`StopFailure` closes a real gap.** Its `error_type` covers `rate_limit`, `overloaded`, `billing_error`, `authentication_failed`, and others. Without this event a session that died on a rate limit keeps looking busy until the staleness timer fires five minutes later, which reads as a crashed editor rather than as something waiting on you.

### Notification types that mean a human is needed

The documented `notification_type` values include far more than attention prompts. The beacon treats these as `NEEDS_INPUT`:

`permission_prompt`, `idle_prompt`, `elicitation_dialog`, `elicitation_url_dialog`, `agent_needs_input`

The rest are informational and only refresh the activity timer: `auth_success`, `elicitation_complete`, `elicitation_response`, `agent_completed`, and the `quota_auto_resume_*` family. `idle_prompt` fires after Claude has been waiting a while, which catches the case where you were asked a question and did not notice.

**`Stop` does not always mean you are up.** It carries `background_tasks`, and a
turn that ends with a subagent still running has handed nothing back to a human.
Observed shape:

```json
"background_tasks": [{"id": "a7c03d1af2...", "type": "subagent",
                      "agent_type": "Explore", "status": "running",
                      "description": "..."}]
```

Treating that as `IDLE` produced a real false alarm: the session went green, the
next `idle_prompt` escalated it to a pulsing red row, and there was nothing for
anyone to do. A session with a running subagent is `WORKING` instead, and
`idle_prompt` is held back while one is outstanding.

Only `idle_prompt` is held back. It means no more than "Claude has been waiting a
while", which is not the same as waiting on a person. The other attention types
are direct asks — a permission prompt raised inside a subagent still needs
answering — so they escalate regardless. A task with no `status` counts as
running, because assuming it had finished is exactly the mistake that produces
the false alarm.

### `background_tasks` is not only subagents

The captured payload above is a subagent, and reading the field as "subagents
still running" cost a session eight minutes of blue followed by amber when it had
been waiting on a human the whole time. It had no subagents at all. It had two
armed artifact comment monitors.

The list is every kind of in-flight work the session has registered. `type` is
documented in the CLI's own hook schema as a *"friendly task-type label (e.g.
'shell', 'subagent', 'monitor', 'workflow'). Falls back to the raw discriminant
for unknown types."* The full map in 2.1.267 is:

| `type` | What it is |
|--------|------------|
| `subagent` | A backgrounded `Agent` call |
| `shell` | A `run_in_background` command |
| `monitor` | An MCP or websocket watch — **an armed artifact comment monitor is one of these** |
| `workflow` | A background dynamic workflow |
| `MCP task` | A backgrounded MCP tool call |
| `teammate` | An in-process teammate |
| `cloud session` | A remote agent |
| `dream`, `auto-mode scan` | Internal background work |

`status` is `running` or `pending`; the list is pre-filtered to work that is
actually in flight, so a finished task never appears in it.

**Only `subagent` is counted**, and the reason is not that the others are less
real. It is that the beacon has machinery for exactly one of them: `SubagentStop`
retires a subagent, and a subagent's own hook events carry an `agent_id` that
refreshes the freshness stamp. Nothing announces the end of any other type, so
counting one is a guess that can only ever mute the display — and a `monitor`
never ends at all. That is the failure that was observed: the count could only go
up, and a session that had used the artifact tooling could never be shown as
waiting on you again for the rest of its life.

The two unknown cases therefore break in opposite directions, deliberately. An
unrecognised `status` counts as running, because assuming a task had finished is
what produces a false red. An unrecognised `type` is ignored, because the failures
are not symmetric: a wrongly ignored task shows a red row that de-escalates in ten
minutes and clears on the next tool call, while a wrongly counted one that never
ends silences the session for good and says nothing about why.

**The other types still soften an `idle_prompt`, and nothing else.** A second
count, `bg_other`, takes every live entry that is neither a `subagent` nor one of
the types that never end (`monitor`, `dream`, `auto-mode scan`). An unrecognised
type counts here. While `bg_other` is nonzero, an `idle_prompt` turns the row
cyan (`look`) instead of red, and after `look_s` it goes red anyway. This was
added after an orchestrator waiting on a `workflow` task was shown the full
alarm. The count plays no part in whether a row is `WORKING`, and the direct-ask
notification types are not softened. See
[the soft rung](architecture.md#the-soft-rung-needs_look).

`Stop` and `SubagentStop` also carry `session_crons` — the `/loop`, `CronCreate`
and `ScheduleWakeup` entries that will wake the session later. Nothing reads it
yet. It is redacted at capture time because each entry carries the prompt text.

**Two things used to break this, and both are fixed.** They are worth spelling out
because between them a session blocked on a human could show `WORKING` for as long
as you left it, which is the one case the device exists for.

*A subagent's tool calls were counted as the parent's.* They carry the parent's
`session_id`, and the state machine set `WORKING` on every one, so a genuinely red
row was repainted blue each time a subagent touched a tool. The red only appeared
if an `idle_prompt` happened to land in a quiet gap. A tool event now ends a wait
only when its `agent_id` matches the one that raised it -- which still lets a
`PostToolBatch` clear a rejected prompt, and still lets a subagent's own
`PostToolBatch` clear a prompt that subagent raised. It refreshes the staleness
timer either way: a long subagent run is the only traffic its session produces, so
ignoring the events outright would send an actively working parent to `STALE`.

*The count could latch on forever.* It was recomputed only on the next `Stop` or
`UserPromptSubmit`. A turn that has already ended and is waiting on you produces
neither, so a count left positive suppressed every `idle_prompt` the session would
ever send and the row never went red at all. `SubagentStop` now recomputes it the
moment a subagent ends. As a backstop the hold also expires: a count nothing has
confirmed within `bg_quiet_s` (default 180 s) is retired outright, and a running
subagent refreshes that constantly with its own events.

*A held `idle_prompt` used to be a lost one.* Dropping it assumed a later one
would get through. Claude Code sends them "after Claude has been waiting a while",
which sounds like a repeat and is not a promise of one: for the idle period that
turned this up it sent **exactly one**, 183 seconds after the turn ended, against
a 180-second hold that had re-armed itself at the `Stop`. Three seconds, and the
row's whole behaviour on either side of them. A held notification is now
remembered on the session and delivered when the hold expires or the count
retires, so it no longer matters which side of the window it lands on, and nothing
depends on a second one arriving.

**A repeat notification does not restart the alarm.** `idle_prompt` fires again and again while nobody answers, so a session already on the attention ladder only has its activity timer refreshed; the rung it has reached is left alone. Without that the display would re-arm the pulse every few minutes and a parked session would blink indefinitely, which is what it used to do. See [the attention ladder](architecture.md#the-attention-ladder).

### Timing

Hooks run synchronously and block the flow until the command exits, so the forwarder must be fast. It does a single HTTP POST with a 200 ms timeout and swallows every error. Measured during scoping at roughly 170 ms per invocation through `uv run`, which carries uv's own startup; a direct interpreter path should be nearer 50 ms. Measure before deciding whether `PreToolUse` is affordable.

## Statusline for cost and context

The statusline command receives JSON on stdin on every refresh. It is a *command*
and only a command: `statusLine` accepts `"type": "command"` and no other kind in
2.1.261, unlike hooks, which also take `http`, `prompt`, `mcpTool` and `agent`. That
is why the beacon keeps a `curl` dependency; see
[architecture.md](architecture.md#native-http-hooks-were-evaluated-and-not-adopted).
The fields the beacon uses:

| Field | Use |
|-------|-----|
| `session_id` | Joins to the hook stream |
| `workspace.current_dir` | Label, preferred over the top-level `cwd` |
| `model.display_name` | Shortened to fit the footer |
| `cost.total_cost_usd` | Summed across sessions for the header |
| `context_window.used_percentage` | Footer bar, pre-calculated |
| `context_window.total_input_tokens`, `.total_output_tokens`, `.context_window_size` | Fallback when the percentage is null |

That fallback matters: `used_percentage` is documented as null early in a session and again after a compaction until the next API call. Without it the footer would blank out at exactly the moments you are most likely to be looking.

### What it looks like

The installed status line is not a script. `curl` POSTs the payload to `/status`
and the daemon composes the reply, which curl prints; that keeps a second Python
interpreter off a path that runs on every status refresh. `beacon_hook.py
--statusline` does the same job and remains as a fallback for machines without
curl, but it prints a shorter line of its own.

![The status line with the beacon connected](../photos/beacon_status_connected.png)

Five fields, joined by two spaces, every one but the last omitted when it is
missing: model, the working directory's basename, context percentage, total cost,
and the marker. Note the basename — this is the one place the label is *not* the
enclosing repository, so a session working in `host/` reads as `host` here while
its row on the device still reads `session-beacon`.

![The same status line with no device attached](../photos/beacon_status_disconnected.png)

`beacon?` is the daemon saying it is running but cannot see the device, which is
the difference between "the hooks are broken" and "the USB cable is out". It is
worth having in the terminal because the failure it reports is the one you cannot
diagnose by looking at the beacon.

That shot also happens to show the null-context case: there is no `ctx` segment
because `used_percentage` is null early in a session and after a compaction, and
the token fallback had nothing to work from either. A missing `ctx` is not a
second fault.

If you already have a custom statusline, `install-hooks.ps1` saves it and
`uninstall.ps1` puts it back.

### Account-level usage is available after all

An earlier version of this file said account-level rate limit figures were not exposed
to hooks or the statusline. That was wrong, and a captured payload disproves it. The
statusline carries:

```json
"rate_limits": {
  "five_hour":  { "used_percentage": 92, "resets_at": 1788571200 },
  "seven_day":  { "used_percentage": 28, "resets_at": 1788800400 }
}
```

Both a percentage and a reset time, for the rolling five-hour and seven-day windows.
That is the "high-level usage stats" this project was scoped around and assumed it
could not have. The footer now alternates every four seconds between the featured
session's context window and these account figures; see
[architecture.md](architecture.md#the-footer-alternates). Only the percentages are
forwarded to the device. The reset timestamps are in the payload and are not used,
because a 26-character line has no room for both and the percentage is the number
that changes what you do.

The same payload also carries `version`, `exceeds_200k_tokens`, `session_name`,
`fast_mode`, `output_style`, `thinking`, `prompt_cache`, and richer `cost` fields
including `total_lines_added` and `total_lines_removed`.

Observed values confirm the context fields work as documented:
`context_window.used_percentage` was 54 against a `context_window_size` of 1000000,
and `model.display_name` was `Opus 5`.

## Example settings.json fragment

See `hooks/settings.example.json` for the exact snippet to merge.

Windows notes:

- Hook commands run through a shell. Forward slashes in paths avoid escaping trouble. Use an absolute path to the interpreter if `python` on PATH resolves to the Microsoft Store stub.
- Hooks are read at session start, so a settings change needs a new session before it takes effect.

## Session identity and labels

`session_id` is unique per session, including resumes. The label is the name of the git repository enclosing `cwd`, capped at 16 characters, not the `cwd` basename. `cwd` moves as a session works, so labelling from it directly showed this repo as `host` whenever anything ran in `host/`. Override in `host/config.toml`, keyed by repo root or exact directory:

```toml
[labels]
"C:/Repos/long-project-name" = "fd-research"
```

## How much of this is verified

Four different levels, and the difference matters.

**Observed on this machine**, captured by running the daemon with `--capture` against
Claude Code 2.1.261 and saved to `host/tests/fixtures/hook_payloads.jsonl`:
`SessionStart`, `UserPromptSubmit`, `PermissionRequest`, `PostToolUse`,
`Notification` (`notification_type: "idle_prompt"`, carrying a `message`),
`Stop`, `SessionEnd`.

**Observed on 2.1.263**: a `PostToolBatch` for a call that was refused rather
than run. Captured by pointing a throwaway settings file's hooks
at a second daemon on a spare port and driving a headless `claude -p` whose
`Bash` call hit an `ask` rule it could not surface — the same resolution a
rejected prompt produces, and the payload confirms it: `tool_calls` carries the
call with the refusal as its `tool_response`, and no `PostToolUse` accompanies
it.

**Also observed**: a `Stop` carrying a populated `background_tasks`, captured by
ending a turn with a subagent still running and saved to
`host/tests/fixtures/stop_with_background_tasks.json`. The empty list had been
seen long before, which showed the field existed but not the shape of an entry.

**Also observed**: a complete session that spawned an `Explore` subagent, saved to
`host/tests/fixtures/subagent_run.jsonl`. It is kept as a sequence rather than
folded into `hook_payloads.jsonl` because the order is the evidence: the subagent's
`PostToolUse` and `PostToolBatch` events arrive under the parent's `session_id`
with an `agent_id` set, and the parent's own `PostToolUse` for the `Agent` call
lands only *after* `SubagentStop`. `SubagentStart` and `SubagentStop` both fire,
and `SubagentStop` carries `background_tasks` alongside `agent_id`, `agent_type`
and `agent_transcript_path`.

What that capture does *not* show is a populated `background_tasks` on a
`SubagentStop`: the agent ran in the foreground, so it was never registered as
background work, and backgrounding one cannot be driven headlessly. So it is still
unknown whether an agent appears in its own stop payload. The host excludes it by
`id` rather than assuming, which is correct either way.

**Read out of the CLI binary rather than observed**: the `background_tasks`
type vocabulary in the table above, and the schema line that documents `type` as
a friendly label falling back to the raw internal name. The binary carries the
hook schemas and the task registry's own internal-to-friendly map, which is what
identifies an armed artifact comment monitor as a `monitor_ws` task reaching a
hook as `type: "monitor"` — the binary's own summary string for that family is
"1 Artifact comment monitor". This is a level below a capture and a level above
the published docs, which do not describe the field at all. What would settle it
is one `--capture` on a session with an armed monitor.

**Present in the CLI binary but not yet seen firing**: `StopFailure`,
`PostToolUseFailure`, and the `notification_type` values `permission_prompt` and
`agent_needs_input`.

**Registered, triggered, and did not fire**: `PermissionDenied`. Denying a Bash
command at the interactive prompt produced a `PermissionRequest` and nothing
else, with `PermissionDenied` present in `~/.claude/settings.json` at the time.

That is no longer a mystery. The CLI's own description of the event is "after
auto mode classifier denies a tool call", and the dispatch is guarded on the
denial having come from the auto-mode classifier: a person answering no takes
the other branch, which writes the tool result and fires nothing. So the state
machine's `PermissionDenied` branch is dead code on this build for every denial
a human makes, and `PostToolBatch` — not `PermissionDenied` — is the event that
marks a prompt as answered. It is kept because it is correct if a classifier
denial ever arrives.

`idle_prompt` has left this list. It was the load-bearing one, because it is the
path by which an ordinary session that finished its turn ends up asking for
attention, and it is now captured. Two of them arrived for the same session
minutes apart with no answer in between, which turned "a repeated notification
would re-arm the pulse forever" from a plausible explanation of a thirteen-hour
red row into an observed mechanism.

**Contradicted by observation**: the published schema this project was first built
against does not match this build. The corrections are below.

### Where the published schema was wrong

| Published | Actually sent | Used by the beacon |
|-----------|---------------|--------------------|
| `session_start_reason` | `source` | no |
| `session_end_reason` | `reason` | no |
| `user_prompt` | `prompt` | no |
| `tool_output` | `tool_response` | no |

None of these are fields the state machine reads, so nothing behaved incorrectly, but
anyone extending this from the documentation alone would have written code against
names that do not exist. `session_start_reason` does not appear anywhere in the CLI
binary, so the published page describes a different version rather than being simply
mistaken.

Fields observed that the published schema did not mention: `prompt_id`,
`effort.level`, `duration_ms` and `scratchpad_dir` on tool events, and
`stop_hook_active`, `background_tasks` and `session_crons` on `Stop`.

### Capturing more

The daemon records payloads itself, which is better than a second hook: no extra
process per event, and it records exactly what the state machine sees.

```powershell
uv run beacon-host --capture "$env:TEMP/beacon-payloads.jsonl"
```

Conversation content is stripped as it is written. Prompts, tool arguments, tool
responses and assistant messages become markers like `<str len=22>`, and every path
except `cwd` is reduced to its last segment because the others carry a username.

Four fields hide free text *inside* a structure rather than at the top level,
and all are walked recursively: `permission_suggestions`, whose `ruleContent` is
derived from the command line; `background_tasks`, whose `description` names a
running task and whose `command` is the full command line of a `shell` one;
`session_crons`, whose `prompt` is the text of a `/loop` or a scheduled wake-up;
and `tool_calls`, which is the whole of a `PostToolBatch` payload
and nests a `tool_input` and a `tool_response` per call — the command line and
its output, one level below the top-level names that were already covered. The
last two were added after the type vocabulary above turned up what else those
lists can carry; every committed fixture happens to have an empty `session_crons`
and no `shell` task, so the guard had never seen either. Their shape survives — a task's `status`, a suggestion's `behavior` and `toolName`, a
call's `tool_name` — because that is what the state machine and the tests read.
Field names and shapes survive, which is all a fixture needs. A test asserts the
committed fixtures contain no usernames or unredacted text.

## Still to confirm

1. ~~Confirm how attention arrives.~~ **Done.** Both paths are captured and committed: a `Notification` carrying `notification_type: "idle_prompt"`, and a dedicated `PermissionRequest`. What a permission prompt does *not* produce, at least once, is a `PermissionDenied` on being refused; see above.
2. ~~Capture a statusline payload.~~ **Done.** Captured and confirmed: `used_percentage` 54 against a `context_window_size` of 1000000, `model.display_name` `Opus 5`, and `rate_limits` present. What is still unobserved is the null case: `used_percentage` is documented as null early in a session and after a `/compact`, and the input-token fallback that covers it has only ever been exercised by unit tests.
3. Watch a `StopFailure` land, most easily during a rate limit.
