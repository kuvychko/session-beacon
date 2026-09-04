<#
.SYNOPSIS
    Undo everything Session Beacon installed on this machine.

.DESCRIPTION
    Setting this up touches things outside the repository: a Scheduled Task, the
    hooks in your Claude Code settings, a log directory, and a background daemon
    holding a COM port. Deleting the repo would leave all of that behind, so the
    undo path is a script rather than a paragraph in a README.

    Everything here is safe to run twice, and safe to run when only some of it
    was ever installed. Nothing the beacon did not create is removed: your own
    hooks, your other settings, and a status line the beacon replaced are all
    kept or restored.

    Run with -WhatIf first to see exactly what it would touch.

.PARAMETER KeepLogs
    Leave the log directory in place.

.PARAMETER RemoveConfig
    Also delete host/config.local.toml and host/config.toml. Off by default:
    you wrote those, the installer did not.

.EXAMPLE
    ./scripts/uninstall.ps1 -WhatIf
    ./scripts/uninstall.ps1
    ./scripts/uninstall.ps1 -RemoveConfig
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$KeepLogs,
    [switch]$RemoveConfig,
    [int]$Port = 47391,
    [string]$TaskName = "SessionBeacon",
    [string]$SettingsPath = (Join-Path $env:USERPROFILE ".claude\settings.json"),
    [string]$LogDir = (Join-Path $env:LOCALAPPDATA "session-beacon")
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$done = [System.Collections.Generic.List[string]]::new()
$skipped = [System.Collections.Generic.List[string]]::new()

Write-Host "Session Beacon uninstall" -ForegroundColor Cyan
Write-Host ""

# ---- 1. Stop the daemon -----------------------------------------------------
# It holds the COM port and would keep running after everything else is gone.
$procs = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "beacon-host.exe" -or
    ($_.CommandLine -and $_.CommandLine -like "*beacon_host*" -and $_.Name -like "py*")
})
if ($procs.Count -gt 0) {
    foreach ($p in $procs) {
        if ($PSCmdlet.ShouldProcess("PID $($p.ProcessId) ($($p.Name))", "Stop process")) {
            try { Stop-Process -Id $p.ProcessId -Force; $done.Add("stopped daemon PID $($p.ProcessId)") }
            catch { $skipped.Add("could not stop PID $($p.ProcessId): $_") }
        }
    }
} else {
    $skipped.Add("no daemon process running")
}

# ---- 2. Scheduled task ------------------------------------------------------
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    if ($PSCmdlet.ShouldProcess($TaskName, "Unregister scheduled task")) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        $done.Add("removed scheduled task '$TaskName'")
    }
} else {
    $skipped.Add("no scheduled task named '$TaskName'")
}

# ---- 3. Claude Code hooks ---------------------------------------------------
# Delegated, so the JSON handling lives in one place. That script keeps your own
# hooks, keeps unrelated settings, and restores a status line it replaced.
if (Test-Path $SettingsPath) {
    if ($PSCmdlet.ShouldProcess($SettingsPath, "Remove beacon hooks")) {
        $sha = { (Get-FileHash $SettingsPath -Algorithm SHA256).Hash }
        $wasHash = & $sha
        & (Join-Path $PSScriptRoot "install-hooks.ps1") -Uninstall -Port $Port -SettingsPath $SettingsPath |
            ForEach-Object { Write-Host "  $_" }
        if ((& $sha) -ne $wasHash) {
            $done.Add("removed beacon hooks from settings.json")
        } else {
            $skipped.Add("no beacon hooks in settings.json")
        }
    }
} else {
    $skipped.Add("no settings.json at $SettingsPath")
}

# ---- 4. Logs ----------------------------------------------------------------
if ($KeepLogs) {
    $skipped.Add("log directory kept on request")
} elseif (Test-Path $LogDir) {
    if ($PSCmdlet.ShouldProcess($LogDir, "Remove log directory")) {
        Remove-Item $LogDir -Recurse -Force
        $done.Add("removed logs at $LogDir")
    }
} else {
    $skipped.Add("no log directory at $LogDir")
}

# ---- 5. Local config, only when asked --------------------------------------
$configs = @("config.local.toml", "config.toml") |
    ForEach-Object { Join-Path $repo "host\$_" } | Where-Object { Test-Path $_ }
if (-not $RemoveConfig) {
    if ($configs) { $skipped.Add("kept your config: $($configs -join ', ')  (use -RemoveConfig)") }
} else {
    foreach ($c in $configs) {
        if ($PSCmdlet.ShouldProcess($c, "Remove config")) {
            Remove-Item $c -Force
            $done.Add("removed $c")
        }
    }
}

# ---- Summary ----------------------------------------------------------------
Write-Host ""
Write-Host "Done:" -ForegroundColor Green
if ($done.Count) { $done | ForEach-Object { Write-Host "  - $_" } } else { Write-Host "  - nothing to do" }
if ($skipped.Count) {
    Write-Host "Left alone:" -ForegroundColor Yellow
    $skipped | ForEach-Object { Write-Host "  - $_" }
}

Write-Host ""
Write-Host "Not removed, because this script cannot:" -ForegroundColor Yellow
Write-Host "  - The firmware on the board. It keeps running and will show 'no host'."
Write-Host "    Flash a different sketch if you want the display back."
Write-Host "  - The repository, and host/.venv."
Write-Host "  - Timestamped settings.json.bak-* files, kept deliberately as a safety net."
Write-Host ""
Write-Host "Restart your Claude Code sessions so the hook removal takes effect."
