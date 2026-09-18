<#
.SYNOPSIS
    Restart the beacon-host daemon under its Scheduled Task, and prove it worked.

.DESCRIPTION
    The one supported way to restart the daemon. Do not start it by hand with
    Start-Process: a daemon the Scheduled Task did not launch is one the task
    cannot stop, so the next Stop-ScheduledTask does nothing and the task's own
    fresh copy dies on the port bind. That happened once, and the only symptom
    was /health's uptime refusing to reset.

    In order, this:
      1. ends the task's current run,
      2. stops every remaining beacon-host process, whoever started it,
      3. waits for the HTTP port to come free,
      4. starts the task, and
      5. waits for /health to answer from a process that started after step 4.
    Anything short of that exits 1 with the tail of the daemon log.

    -StopOnly does steps 1 to 3 and leaves the daemon down, for work that needs
    the COM port or the HTTP port to itself: flashing firmware, or running the
    daemon in the foreground. Run the script again without it afterwards.

.EXAMPLE
    ./scripts/restart-daemon.ps1
    ./scripts/restart-daemon.ps1 -StopOnly    # then flash, then run it again
#>
[CmdletBinding()]
param(
    [switch]$StopOnly,
    [string]$TaskName = "SessionBeacon",
    [int]$Port = 47391,
    [int]$TimeoutSec = 30
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$logFile = Join-Path $env:LOCALAPPDATA "session-beacon\beacon-host.log"

function Fail([string]$msg) {
    Write-Host "FAILED: $msg" -ForegroundColor Red
    if (Test-Path $logFile) {
        Write-Host "Last lines of ${logFile}:"
        Get-Content $logFile -Tail 8 | ForEach-Object { Write-Host "  $_" }
    }
    exit 1
}

function Test-PortFree {
    # A listener only. TIME_WAIT leftovers do not stop the next bind.
    -not (Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

$haveTask = Test-BeaconTask -TaskName $TaskName
if (-not $haveTask -and -not $StopOnly) {
    Fail "no Scheduled Task named '$TaskName'. Run ./scripts/install-task.ps1 first, or run the daemon in the foreground: cd host; uv run beacon-host"
}

# ---- 1. End the task's run --------------------------------------------------
$before = Get-BeaconHealth -Port $Port
if ($haveTask -and (Get-BeaconTaskStatus -TaskName $TaskName) -eq "Running") {
    [void](Invoke-Schtasks /End /TN $TaskName)
    Write-Host "Ended the '$TaskName' task's run."
}

# ---- 2. Stop whatever is left -----------------------------------------------
# Not only the stragglers of step 1: a daemon started outside the task is the
# case this script exists for, and ending the task never touches it.
$deadline = (Get-Date).AddSeconds(5)
do {
    $procs = @(Get-BeaconDaemonProcess)
    foreach ($p in $procs) {
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            Write-Host "Stopped PID $($p.ProcessId) ($($p.Name))."
        } catch [Microsoft.PowerShell.Commands.ProcessCommandException] {
            # Already gone: the launcher takes its child down with it.
        }
    }
    if ($procs.Count) { Start-Sleep -Milliseconds 300 }
} while ($procs.Count -and (Get-Date) -lt $deadline)
$left = @(Get-BeaconDaemonProcess)
if ($left.Count) {
    Fail "beacon-host processes survived Stop-Process: $($left.ProcessId -join ', ')"
}

# ---- 3. Wait for the port ----------------------------------------------------
$deadline = (Get-Date).AddSeconds(10)
while (-not (Test-PortFree) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 250 }
if (-not (Test-PortFree)) {
    $owner = (Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen).OwningProcess
    Fail "port $Port is still held, by PID $owner, which is not a beacon-host process."
}

if ($StopOnly) {
    Write-Host "Daemon stopped; COM port and port $Port are free." -ForegroundColor Green
    Write-Host "Start it again with: ./scripts/restart-daemon.ps1"
    exit 0
}

# ---- 4. Start the task ------------------------------------------------------
$started = Get-Date
if ((Invoke-Schtasks /Run /TN $TaskName) -ne 0) {
    Fail "schtasks /Run /TN $TaskName failed."
}

# ---- 5. Prove the new daemon is the one answering ---------------------------
# uptime_s is the proof: a reply from a daemon that predates step 4 would have
# the old uptime, which is how the out-of-band daemon was caught.
$deadline = $started.AddSeconds($TimeoutSec)
$health = $null
while ((Get-Date) -lt $deadline) {
    $h = Get-BeaconHealth -Port $Port
    if ($h -and $h.uptime_s -le ((Get-Date) - $started).TotalSeconds + 1) { $health = $h; break }
    Start-Sleep -Milliseconds 500
}
if (-not $health) {
    Fail "no fresh daemon answered /health within ${TimeoutSec}s."
}
$status = Get-BeaconTaskStatus -TaskName $TaskName
if ($status -ne "Running") {
    Fail "a daemon answers, but the '$TaskName' task says '$status', so the task does not own it."
}

$was = if ($before) { "PID $($before.pid), up $($before.uptime_s)s" } else { "not running" }
Write-Host "Restarted under the '$TaskName' task." -ForegroundColor Green
Write-Host "  before : $was"
Write-Host "  now    : PID $($health.pid), up $($health.uptime_s)s, device $($health.device_state), $($health.sessions) session(s)"
