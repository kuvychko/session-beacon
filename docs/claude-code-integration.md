# Claude Code integration

Everything the beacon knows comes from two Claude Code features: **hooks** and the **statusline command**. Both are configured in `~/.claude/settings.json` and apply to every session on the machine, which is exactly what we want.

Field names below are taken from the published hook and statusline schemas, not from memory. Reference: [hooks](https://code.claude.com/docs/en/hooks), [statusline](https://code.claude.com/docs/en/statusline). They have not yet been confirmed against a payload captured on this machine; see the first-run checklist at the end.

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
- **`agent_id` and `agent_type`** are present only inside subagents. Subagent activity is therefore distinguishable from the parent's, which contradicts an earlier note in this file claiming it was not.

There is no `source` field. `SessionStart` carries `session_start_reason` and `SessionEnd` carries `session_end_reason`.

## Hook events we register

| Event | Fires when | Beacon transition |
|-------|-----------|-------------------|
| `SessionStart` | Session opens, resumes, clears, compacts, or forks | Create session as `STARTING` |
| `UserPromptSubmit` | You send a prompt | `WORKING`, clears any error |
| `PostToolUse` | After each tool call, carries `tool_name` | `WORKING`, refreshes the staleness timer |
| `PermissionRequest` | Claude Code needs permission for a tool | `NEEDS_INPUT` |
| `PermissionDenied` | You denied it | `WORKING`, the prompt was answered |
| `Notification` | Claude Code wants attention, carries `notification_type` | `NEEDS_INPUT` for the attention types below |
| `Stop` | Claude finished its turn | `IDLE` |
| `StopFailure` | The turn ended on an API error, carries `error_type` | `ERROR` |
| `SessionEnd` | Session closes | `ENDED` |

**`PermissionRequest` is a dedicated event.** An earlier draft of this design watched `Notification` for `permission_prompt` instead. The dedicated event is more precise and arrives without depending on notification settings, so it is now the primary signal, with the notification kept as a second path.

**`PreToolUse` is deliberately not registered.** `PostToolUse` alone is enough for activity and staleness, and skipping `PreToolUse` halves the hook cost on the busiest event. Turn it on only if a hint of what a session is *about* to do turns out to be worth the latency. The state machine handles it either way.

**`StopFailure` closes a real gap.** Its `error_type` covers `rate_limit`, `overloaded`, `billing_error`, `authentication_failed`, and others. Without this event a session that died on a rate limit keeps looking busy until the staleness timer fires five minutes later, which reads as a crashed editor rather than as something waiting on you.

### Notification types that mean a human is needed

The documented `notification_type` values include far more than attention prompts. The beacon treats these as `NEEDS_INPUT`:

`permission_prompt`, `idle_prompt`, `elicitation_dialog`, `elicitation_url_dialog`, `agent_needs_input`

The rest are informational and only refresh the activity timer: `auth_success`, `elicitation_complete`, `elicitation_response`, `agent_completed`, and the `quota_auto_resume_*` family. `idle_prompt` fires after Claude has been waiting a while, which catches the case where you were asked a question and did not notice.

### Timing

Hooks run synchronously and block the flow until the command exits, so the forwarder must be fast. It does a single HTTP POST with a 200 ms timeout and swallows every error. Measured during scoping at roughly 170 ms per invocation through `uv run`, which carries uv's own startup; a direct interpreter path should be nearer 50 ms. Measure before deciding whether `PreToolUse` is affordable.

## Statusline for cost and context

The statusline command receives JSON on stdin on every refresh. The fields the beacon uses:

| Field | Use |
|-------|-----|
| `session_id` | Joins to the hook stream |
| `workspace.current_dir` | Label, preferred over the top-level `cwd` |
| `model.display_name` | Shortened to fit the footer |
| `cost.total_cost_usd` | Summed across sessions for the header |
| `context_window.used_percentage` | Footer bar, pre-calculated |
| `context_window.total_input_tokens`, `.total_output_tokens`, `.context_window_size` | Fallback when the percentage is null |

That fallback matters: `used_percentage` is documented as null early in a session and again after a compaction until the next API call. Without it the footer would blank out at exactly the moments you are most likely to be looking.

`beacon_hook.py --statusline` forwards the payload to `POST /status` and then prints a status line so the terminal still shows something useful. If you already have a custom statusline, it can be wrapped rather than replaced. That is a phase 2 detail.

Account-level rate limit and remaining credit figures, the ones `/usage` shows, are not exposed to hooks or to the statusline. "Usage stats" on this device therefore means per-session cost and context percent.

## Example settings.json fragment

See `hooks/settings.example.json` for the exact snippet to merge.

Windows notes:

- Hook commands run through a shell. Forward slashes in paths avoid escaping trouble. Use an absolute path to the interpreter if `python` on PATH resolves to the Microsoft Store stub.
- Hooks are read at session start, so a settings change needs a new session before it takes effect.

## Session identity and labels

`session_id` is unique per session, including resumes. The label is the basename of `cwd` by default, capped at 16 characters. Override in `host/config.toml`:

```toml
[labels]
"C:/Repos/factory-dynamics-research" = "fd-research"
```

## First-run checklist

The schema above is documented, not observed. Before trusting it on this machine:

1. Point the `Notification` and `PermissionRequest` hooks at a script that appends raw stdin to a file. Trigger a permission prompt in a scratch session. Confirm the event names and `notification_type` values that actually arrive.
2. Do the same for the statusline command. Confirm the context window fields are populated and watch what happens right after a `/compact`.
3. Measure hook latency with `Measure-Command` against the real interpreter path.
4. Save the captured payloads as fixtures under `host/tests/fixtures/` and point the state machine tests at them, replacing the hand-written dictionaries they use today.
