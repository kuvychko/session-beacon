<#
.SYNOPSIS
    Register beacon-host to start at logon, or remove it again.

.DESCRIPTION
    Creates a Scheduled Task that launches the daemon with pythonw.exe, so it
    runs with no console window. Logon-triggered rather than a Windows service:
    the daemon only matters while you are logged in, and a task is far easier
    to inspect and remove than a service.

.EXAMPLE
    ./scripts/install-task.ps1
    ./scripts/install-task.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [switch]$Uninstall,
    [string]$TaskName = "SessionBeacon"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

if ($Uninstall) {
    # schtasks.exe, not Get-ScheduledTask: see common.ps1 for why.
    if (Test-BeaconTask -TaskName $TaskName) {
        if ((Invoke-Schtasks /Delete /TN $TaskName /F) -ne 0) {
            throw "schtasks /Delete /TN $TaskName failed."
        }
        Write-Host "Removed scheduled task '$TaskName'."
    } else {
        Write-Host "No scheduled task named '$TaskName'."
    }
    return
}

$repo    = Split-Path -Parent $PSScriptRoot
$hostDir = Join-Path $repo "host"
$pythonw = Join-Path $hostDir ".venv\Scripts\pythonw.exe"
$logFile = Join-Path $env:LOCALAPPDATA "session-beacon\beacon-host.log"

if (-not (Test-Path $pythonw)) {
    throw "No virtual environment at $pythonw. Run 'uv sync' in $hostDir first."
}
New-Item -ItemType Directory -Force -Path (Split-Path $logFile) | Out-Null

$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument "-m beacon_host.main --log-file `"$logFile`"" `
    -WorkingDirectory $hostDir

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Restart if it dies, and never let Windows stop it for running too long.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Session Beacon host daemon" -Force | Out-Null

Write-Host "Registered '$TaskName' to start at logon."
Write-Host "  python : $pythonw"
Write-Host "  logs   : $logFile"
Write-Host ""
Write-Host "Start it now with:  ./scripts/restart-daemon.ps1"
Write-Host "Check it with:      curl.exe -s http://127.0.0.1:47391/health"
