<#
.SYNOPSIS
    Add (or remove) the Session Beacon hooks in ~/.claude/settings.json.

.DESCRIPTION
    Without these hooks Claude Code never tells the daemon anything, so the
    daemon connects to the display and reports zero sessions forever. That
    looks like a hardware fault and is not one.

    The script backs up settings.json first, is idempotent, and validates the
    result before leaving it in place. Existing settings are preserved.

    Hooks are read when a session starts, so restart your Claude Code sessions
    afterwards.

.PARAMETER WithStatusLine
    Also route the status line through the daemon, which is where cost and
    context percentage come from. Off by default because it replaces any
    status line you already have. The beacon works without it.

.EXAMPLE
    ./scripts/install-hooks.ps1
    ./scripts/install-hooks.ps1 -WithStatusLine
    ./scripts/install-hooks.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [switch]$WithStatusLine,
    [switch]$Uninstall,
    [int]$Port = 47391
)

$ErrorActionPreference = "Stop"

$settingsPath = Join-Path $env:USERPROFILE ".claude\settings.json"
$eventUrl  = "http://127.0.0.1:$Port/event"
$statusUrl = "http://127.0.0.1:$Port/status"
$curl      = "C:/Windows/System32/curl.exe"
$eventCmd  = "$curl -s --max-time 1 --data-binary @- $eventUrl"
$statusCmd = "$curl -s --max-time 1 --data-binary @- $statusUrl"

# PostToolUse but not PreToolUse: one is enough for activity and staleness,
# and skipping the other halves the cost on the busiest event.
$events = @(
    "SessionStart", "UserPromptSubmit", "PostToolUse",
    "PermissionRequest", "PermissionDenied", "Notification",
    "Stop", "StopFailure", "SessionEnd"
)

if (-not (Test-Path $settingsPath)) {
    if ($Uninstall) { Write-Host "No settings.json at $settingsPath."; return }
    New-Item -ItemType Directory -Force -Path (Split-Path $settingsPath) | Out-Null
    "{}" | Set-Content -Path $settingsPath -Encoding utf8
    Write-Host "Created $settingsPath"
}

$original = Get-Content $settingsPath -Raw
try {
    $settings = $original | ConvertFrom-Json -AsHashtable
} catch {
    throw "settings.json is not valid JSON; fix it before running this. ($_)"
}
if ($null -eq $settings) { $settings = @{} }

$backup = "$settingsPath.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
Copy-Item $settingsPath $backup
Write-Host "Backed up to $backup"

if (-not $settings.ContainsKey("hooks") -or $null -eq $settings["hooks"]) {
    $settings["hooks"] = @{}
}
$hooks = $settings["hooks"]

function Test-HasBeacon($groups) {
    foreach ($g in @($groups)) {
        foreach ($h in @($g.hooks)) {
            if ($h.command -and $h.command -like "*127.0.0.1:$Port*") { return $true }
        }
    }
    return $false
}

$changed = 0

if ($Uninstall) {
    foreach ($evt in @($hooks.Keys)) {
        $kept = @()
        foreach ($g in @($hooks[$evt])) {
            $keptHooks = @($g.hooks | Where-Object {
                -not ($_.command -and $_.command -like "*127.0.0.1:$Port*") })
            if ($keptHooks.Count -gt 0) { $g.hooks = $keptHooks; $kept += $g }
            else { $changed++ }
        }
        if ($kept.Count -gt 0) { $hooks[$evt] = $kept } else { $hooks.Remove($evt) }
    }
    if ($settings.ContainsKey("statusLine") -and
        $settings["statusLine"].command -like "*127.0.0.1:$Port*") {
        $settings.Remove("statusLine"); $changed++
        Write-Host "Removed the beacon status line."
    }
    Write-Host "Removed $changed beacon hook entries."
} else {
    foreach ($evt in $events) {
        if (-not $hooks.ContainsKey($evt) -or $null -eq $hooks[$evt]) { $hooks[$evt] = @() }
        if (Test-HasBeacon $hooks[$evt]) { continue }
        $hooks[$evt] = @($hooks[$evt]) + @(
            @{ hooks = @(@{ type = "command"; command = $eventCmd }) })
        $changed++
    }
    Write-Host "Added the beacon hook to $changed event(s); $($events.Count - $changed) already present."

    if ($WithStatusLine) {
        if ($settings.ContainsKey("statusLine") -and
            $settings["statusLine"].command -notlike "*127.0.0.1:$Port*") {
            Write-Warning "Replacing your existing status line. The old one is in $backup."
        }
        $settings["statusLine"] = @{ type = "command"; command = $statusCmd }
        Write-Host "Status line routed through the daemon."
    } else {
        Write-Host "Status line left alone. Re-run with -WithStatusLine for cost and context data."
    }
}

$json = $settings | ConvertTo-Json -Depth 12
try { $json | ConvertFrom-Json | Out-Null } catch { throw "Refusing to write invalid JSON: $_" }
$json | Set-Content -Path $settingsPath -Encoding utf8

Write-Host ""
Write-Host "Wrote $settingsPath"
Write-Host "Hooks load at session start, so restart your Claude Code sessions now."
Write-Host "Then check the daemon is seeing them:"
Write-Host "  curl.exe -s http://127.0.0.1:$Port/health"
Write-Host "'events_received' should climb as you use Claude Code."
