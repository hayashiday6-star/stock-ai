<#
.SYNOPSIS
    The daily job: refresh prices, then check the watchlist.

.DESCRIPTION
    Written to be run by Task Scheduler rather than by hand, so it never
    pauses, never prompts, and always writes a dated log under logs\daily\.

    Use -Register once to create the scheduled task; after that Windows runs it.
    Scheduling it through Windows rather than leaving a PowerShell window open
    matters because the blocking mode has no catch-up if the machine sleeps.

.PARAMETER Symbols
    Symbols whose prices to refresh. Omit to refresh everything already stored.

.PARAMETER Provider
    AI provider for the watchlist monitor.

.PARAMETER Channel
    Notification channel for alerts. Omit to print them instead.

.PARAMETER Register
    Create (or replace) the scheduled task instead of running the job now.

.PARAMETER At
    Local time for -Register, as HH:mm.

.EXAMPLE
    .\scripts\4-daily.ps1 -Provider claude -Channel discord
    .\scripts\4-daily.ps1 -Register -At 18:00 -Provider claude -Channel discord
#>
[CmdletBinding()]
param(
    [string[]]$Symbols = @(),
    [string]$Provider = 'claude',
    [string]$Channel,
    [switch]$Register,
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$At = '18:00'
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

. "$PSScriptRoot\_common.ps1"

# --- registration ----------------------------------------------------------

if ($Register) {
    Write-Section 'Register the daily task'

    $taskName = 'stock-ai daily'
    $arguments = @(
        '-NoProfile'
        '-ExecutionPolicy', 'Bypass'
        '-File', "`"$PSCommandPath`""
        '-Provider', $Provider
    )
    if ($Channel) { $arguments += @('-Channel', $Channel) }
    if ($Symbols.Count -gt 0) { $arguments += @('-Symbols', ($Symbols -join ',')) }

    try {
        # Built inside the try: the ScheduledTasks module is Windows-only, so
        # on anything else these throw before Register-ScheduledTask is reached.
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
            -Argument ($arguments -join ' ') -WorkingDirectory $root
        $trigger = New-ScheduledTaskTrigger -Daily -At $At
        # StartWhenAvailable is the point of scheduling this at all: it runs the
        # job late after the machine was asleep, instead of skipping the day.
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
            -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
            -Settings $settings -Description 'stock-ai: refresh prices and check the watchlist' `
            -Force | Out-Null
        Write-Ok "Registered '$taskName' to run daily at $At."
        Write-Host 'Manage it in Task Scheduler (taskschd.msc).'
        Write-Host "Run it now with:  Start-ScheduledTask -TaskName '$taskName'"
    }
    catch {
        Write-Err "Could not register the task: $($_.Exception.Message)"
        Write-Host 'Registering a scheduled task usually needs an elevated PowerShell'
        Write-Host '(right-click PowerShell -> Run as administrator), and Windows.'
        Exit-WithPause 1
    }
    Exit-WithPause 0
}

# --- the job itself --------------------------------------------------------

$logDir = Join-Path $root 'logs\daily'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$log = Join-Path $logDir "$(Get-Date -Format 'yyyy-MM-dd').log"

Start-Transcript -Path $log -Append | Out-Null
try {
    Write-Section "stock-ai daily  $(Get-Date -Format 'yyyy-MM-dd HH:mm')"

    if (-not (Test-UvInstalled)) { return }

    $arguments = @('daily', '--once', '--provider', $Provider)
    if ($Channel) { $arguments += @('--channel', $Channel) }
    # A comma-joined -Symbols survives Task Scheduler's argument flattening.
    foreach ($symbol in ($Symbols -split ',' | Where-Object { $_ })) {
        $arguments += $symbol.Trim()
    }

    $ok = Invoke-Step 'Daily pipeline' $arguments
    if ($ok) { Write-Ok 'Finished.' } else { Write-Err 'Finished with errors.' }
}
finally {
    Stop-Transcript | Out-Null
}

# No pause: this is meant to run unattended.
exit 0
