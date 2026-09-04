# Claude Code integration

Everything the beacon knows comes from two Claude Code features: **hooks** and the **statusline command**. Both are configured in `~/.claude/settings.json` and apply to every session on the machine, which is exactly what we want.

Verified against Claude Code 2.1.261. Field names below are from memory of the hooks documentation and must be confirmed against a real payload during Phase 1 (see the "First run" checklist). Official reference: https://docs.anthropic.com/en/docs/claude-code/hooks

## Hook events we use

Each hook is a `command` hook. Claude Code runs the command, pipes a JSON payload to stdin, and waits for exit. Every payload includes at least `session_id`, `cwd`, `hook_event_name`, and `transcript_path`.

| Event | Fires when | Beacon transition |
|-------|-----------|-------------------|
| `SessionStart` | Session opens or resumes (`source`: startup, resume, clear, compact) | create session as `STARTING` |
| `UserPromptSubmit` | You send a prompt | `WORKING` |
| `PreToolUse` | Before each tool call (matcher can filter by tool) | `WORKING`, records `tool_name` |
| `PostToolUse` | After each tool call | `WORKING` (refreshes staleness timer) |
| `Notification` | Claude Code wants your attention. `notification_type` is one of `permission_prompt`, `idle_prompt`, `auth_success`, `elicitation_dialog` | `permission_prompt` and `idle_prompt` and `elicitation_dialog` map to `NEEDS_INPUT` |
| `Stop` | Claude finished its turn | `IDLE` |
| `SubagentStop` | A subagent finished | no state change, phase 2 could show a subagent count |
| `SessionEnd` | Session closes (`reason`: exit, clear, logout, prompt_input_exit, other) | `ENDED` |
| `PreCompact` | Context about to compact | no state change, could flash a "compacting" hint |

`Notification` with `permission_prompt` is the single most important event: it is the "you left me hanging" signal. `idle_prompt` fires after 60 s of Claude waiting for input, which covers the case where Claude asked a question via `AskUserQuestion` and you did not notice.

Important timing detail: hooks run synchronously and `PreToolUse` in particular blocks the tool call until the hook exits. The forwarder must complete in well under 100 ms. It does a single HTTP POST with a 200 ms timeout and swallows all errors.

## Statusline command for cost and context

The statusline command receives a JSON payload on stdin on every refresh (roughly every 300 ms while active). It includes `session_id`, `model.display_name`, `workspace.current_dir`, `cost.total_cost_usd`, `cost.total_duration_ms`, and context window usage fields. That is our only cheap source of cost and context percent.

The beacon-hook script, when invoked with `--statusline`, forwards this payload to `POST /status` and then prints a normal one-line statusline so the terminal still shows something useful. The host daemon rate-limits what it pushes to the device.

If you already have a custom statusline, wrap it: beacon-hook can exec the existing command after forwarding. Phase 2 detail.

Fallback if the statusline payload turns out not to carry what we need: parse `transcript_path` (a JSONL file) and sum `usage` blocks. More expensive, but it is a known-good data source.

Not available from any hook: account-level rate-limit or remaining-credit figures like the ones `/usage` shows. If they become exposed in the statusline payload we can display them; otherwise "usage stats" means per-session cost and context percent.

## Example settings.json fragment

See `hooks/settings.example.json` for the full snippet. Shape:

```json
{
  "hooks": {
    "SessionStart":     [{ "hooks": [{ "type": "command", "command": "python C:/Repos/session-beacon/hooks/beacon_hook.py" }] }],
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "python C:/Repos/session-beacon/hooks/beacon_hook.py" }] }],
    "PreToolUse":       [{ "hooks": [{ "type": "command", "command": "python C:/Repos/session-beacon/hooks/beacon_hook.py" }] }],
    "PostToolUse":      [{ "hooks": [{ "type": "command", "command": "python C:/Repos/session-beacon/hooks/beacon_hook.py" }] }],
    "Notification":     [{ "hooks": [{ "type": "command", "command": "python C:/Repos/session-beacon/hooks/beacon_hook.py" }] }],
    "Stop":             [{ "hooks": [{ "type": "command", "command": "python C:/Repos/session-beacon/hooks/beacon_hook.py" }] }],
    "SessionEnd":       [{ "hooks": [{ "type": "command", "command": "python C:/Repos/session-beacon/hooks/beacon_hook.py" }] }]
  },
  "statusLine": {
    "type": "command",
    "command": "python C:/Repos/session-beacon/hooks/beacon_hook.py --statusline"
  }
}
```

Windows notes:

- Hook commands on Windows are run through a shell. Forward slashes in the path avoid escaping trouble. Use an absolute path to the Python interpreter if `python` on PATH resolves to the Microsoft Store stub.
- Measured during scoping: about 170 ms per hook invocation when launched via `uv run python` (includes uv overhead). Plain `python.exe` should be nearer 50 ms. That is fine for `Notification`, `Stop`, and session events, but `PreToolUse`/`PostToolUse` fire on every tool call, so measure with the real interpreter path before enabling them. If too slow, drop the tool-use hooks (staleness detection degrades slightly) or rewrite the forwarder as a small compiled exe.

## Session identity and labels

`session_id` is unique per session, including resumes. The label shown on screen is the basename of `cwd` by default. Override in `host/config.toml`:

```toml
[labels]
"C:/Repos/factory-dynamics-research" = "fd-research"
```

Worktrees and subagents: a subagent shares the parent's `session_id` in hook payloads, so tool calls from subagents just look like activity. Good enough for phase 1.

## First run checklist

Before trusting any of the above:

1. Add only a `Notification` hook that appends the raw stdin JSON to a file. Trigger a permission prompt. Confirm `notification_type` and its values.
2. Do the same for the statusline command. Confirm the cost and context field names.
3. Measure hook latency with `Measure-Command` around `python beacon_hook.py < sample.json`.
4. Record the confirmed payloads as fixtures under `host/tests/fixtures/` so the state machine tests use real shapes.
