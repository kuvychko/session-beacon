<#
.SYNOPSIS
    Helpers shared by the install, uninstall and restart scripts. Dot-source it.

.DESCRIPTION
    Task lookups go through schtasks.exe, not the ScheduledTasks module.
    Get-ScheduledTask enumerates every task on the machine and fails outright
    ("The task XML contains a value which is incorrectly formatted or out of
    range") when any one of them is malformed, even with -TaskName. Called with
    -ErrorAction SilentlyContinue, as the uninstaller did, that failure reads as
    "no such task", so the uninstaller reported SessionBeacon absent and left it
    registered. schtasks.exe asks for the one task by name and is not affected.

    Keep this file compatible with Windows PowerShell 5.1.
#>

function Invoke-Schtasks {
    <# Run schtasks.exe quietly. Returns its exit code; output is discarded.
       Native stderr under $ErrorActionPreference = "Stop" is a terminating
       error in Windows PowerShell 5.1, and "task not found" is stderr, so
       relax the preference for the call rather than trust every caller. #>
    param([Parameter(ValueFromRemainingArguments)][string[]]$Arguments)
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & schtasks.exe @Arguments *> $null
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $old
    }
}

function Test-BeaconTask {
    param([string]$TaskName = "SessionBeacon")
    return (Invoke-Schtasks /Query /TN $TaskName) -eq 0
}

function Get-BeaconTaskStatus {
    <# "Ready", "Running", "Disabled"... or $null if the task does not exist. #>
    param([string]$TaskName = "SessionBeacon")
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $csv = & schtasks.exe /Query /TN $TaskName /FO CSV 2> $null
        if ($LASTEXITCODE -ne 0 -or -not $csv) { return $null }
        return ($csv | ConvertFrom-Csv | Select-Object -First 1).Status
    } finally {
        $ErrorActionPreference = $old
    }
}

function Get-BeaconDaemonProcess {
    <# Every beacon-host process, however it was started: the Scheduled Task's
       pythonw.exe (a venv launcher plus the interpreter it spawns), a
       foreground `uv run beacon-host`, or a hand-rolled Start-Process. #>
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "beacon-host.exe" -or
        ($_.CommandLine -and $_.CommandLine -like "*beacon_host*" -and $_.Name -like "py*")
    })
}

function Get-BeaconHealth {
    <# The daemon's /health as an object, or $null if nothing answers. #>
    param([int]$Port = 47391)
    try {
        return Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
    } catch {
        return $null
    }
}
