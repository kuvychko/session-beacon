<#
.SYNOPSIS
    Register beacon-host to start at logon, or remove it again.

.DESCRIPTION
    Creates a Scheduled Task that launches the daemon with pythonw.exe, so it
    runs with no console window. Logon-triggered rather than a Windows service:
    the daemon only matters while you are logged in, and a task is far easier
    to inspect and remove than a service. A second trigger repeats every 5
    minutes and relaunches the daemon if anything killed it.

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

$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Relaunch within 5 minutes whatever killed it. At logon fires once per logon,
# and a restart that Windows cancels after it has already terminated us (an app
# blocking shutdown) leaves the user logged on with no daemon. -RestartCount
# does not cover that: it retries a failure to launch, and a process that is
# killed later is logged as a successful completion. No duration means the
# repetition never ends. The task runs only while the user is logged on.
$watch = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)

# IgnoreNew makes every repeat a no-op while the daemon runs. A daemon started
# outside the task is not an instance, so a repeat launches a copy that loses
# the port bind and logs the PID holding it. Never let Windows stop it for
# running too long. StartWhenAvailable fires a repeat missed during sleep.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($logon, $watch) `
    -Settings $settings -Description "Session Beacon host daemon" -Force | Out-Null

Write-Host "Registered '$TaskName' to start at logon and relaunch within 5 minutes if it dies."
Write-Host "  python : $pythonw"
Write-Host "  logs   : $logFile"
Write-Host ""
Write-Host "Start it now with:  ./scripts/restart-daemon.ps1"
Write-Host "Check it with:      curl.exe -s http://127.0.0.1:47391/health"
