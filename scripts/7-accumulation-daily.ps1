<#
.SYNOPSIS
    Run the accumulation screen once a day and push the result to Discord.

.DESCRIPTION
    Written for Task Scheduler rather than for hands: it never pauses, never
    prompts, and writes a dated log under logs\accumulation\.

    Use -Register once to create the scheduled task. Scheduling it through
    Windows rather than leaving a window open matters here for one specific
    reason: the screen reads the *latest* daily bar, so a run that silently
    did not happen leaves yesterday's picture looking like today's.

    Timing. US regular trading closes 16:00 ET, which is early morning in
    Japan, and the daily bar shows up after that. The 07:00 default is chosen
    to sit after the close rather than to be a round number. Running earlier
    reads the previous session; the screen says so at the top of its output
    when the newest bar is two or more sessions old.

.PARAMETER Symbols
    Screen only these. Omit them to scan the whole market, which is the point
    of the screen and takes several minutes.

.PARAMETER Channel
    Notification channel. 'discord' needs DISCORD_WEBHOOK_URL in .env.
    Use 'console' to see the message without sending it anywhere.

.PARAMETER Heartbeat
    Send a message even on a day nothing passed. Without it a quiet day and a
    job that never ran look identical from the phone; with it, most days are a
    "nothing today" message. Neither is wrong - pick which mistake you prefer.

.PARAMETER Limit
    Rows in the phase-1 table.

.PARAMETER Deep
    How many of them get the phase-2/3 deep dive. moomoo caps funding-flow
    calls at 30 per 30 seconds, so this is what keeps a run inside that.

.PARAMETER Register
    Create (or replace) the scheduled task instead of running the screen now.

.PARAMETER At
    Local time for -Register, as HH:mm.

.EXAMPLE
    .\scripts\7-accumulation-daily.ps1 -Channel console
    .\scripts\7-accumulation-daily.ps1 -Register -At 07:00 -Channel discord
    .\scripts\7-accumulation-daily.ps1 -Register -Channel discord -Heartbeat
#>
[CmdletBinding()]
param(
    [string[]]$Symbols = @(),
    [ValidateSet('console', 'discord', 'telegram', 'line')]
    [string]$Channel = 'discord',
    [switch]$Heartbeat,
    [ValidateRange(1, 50)]
    [int]$Limit = 10,
    [ValidateRange(1, 20)]
    [int]$Deep = 5,
    [switch]$Register,
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$At = '07:00'
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

. "$PSScriptRoot\_common.ps1"

# --- registration ----------------------------------------------------------

if ($Register) {
    Write-Section 'Register the daily accumulation screen'

    if ($Channel -eq 'discord' -and -not (Test-EnvKeySet -Name 'DISCORD_WEBHOOK_URL')) {
        Write-Warn 'DISCORD_WEBHOOK_URL is not set in .env.'
        Write-Host '  The task will register, but every run will fail to deliver.'
        Write-Host '  Set it first with:  .\scripts\set-key.ps1 DISCORD_WEBHOOK_URL'
        Write-Host ''
    }

    $taskName = 'stock-ai accumulation'
    $arguments = @(
        '-NoProfile'
        '-ExecutionPolicy', 'Bypass'
        '-File', "`"$PSCommandPath`""
        '-Channel', $Channel
        '-Limit', $Limit
        '-Deep', $Deep
    )
    if ($Heartbeat) { $arguments += '-Heartbeat' }
    if ($Symbols.Count -gt 0) { $arguments += @('-Symbols', ($Symbols -join ',')) }

    try {
        # Built inside the try: the ScheduledTasks module is Windows-only, so
        # on anything else these throw before Register-ScheduledTask is reached.
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
            -Argument ($arguments -join ' ') -WorkingDirectory $root
        $trigger = New-ScheduledTaskTrigger -Daily -At $At
        # StartWhenAvailable is the point of scheduling this at all: it runs the
        # job late after the machine was asleep, instead of skipping the day.
        $taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
            -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
            -Settings $taskSettings -Force `
            -Description 'stock-ai: daily US accumulation screen, notified to a channel' | Out-Null
        Write-Ok "Registered '$taskName' to run daily at $At."
        Write-Host 'Manage it in Task Scheduler (taskschd.msc).'
        Write-Host "Run it now with:  Start-ScheduledTask -TaskName '$taskName'"
        Write-Host ''
        # No -Principal is set, so the task inherits the default: it runs as
        # this user and only while that user is logged on. Saying so matters -
        # "daily" is otherwise read as "even at the login screen", and the job
        # would appear to have silently stopped.
        Write-Host 'This runs as you, and only while you are logged on. It'
        Write-Host 'catches up after sleep (StartWhenAvailable), but a day spent'
        Write-Host 'logged out is a day it does not run. To change that, open'
        Write-Host 'Task Scheduler and tick "Run whether user is logged on or'
        Write-Host 'not" - Windows will ask for your password to store it.'
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

$logDir = Join-Path $root 'logs\accumulation'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$log = Join-Path $logDir "$(Get-Date -Format 'yyyy-MM-dd').log"

Start-Transcript -Path $log -Append | Out-Null
try {
    Write-Section "stock-ai accumulation  $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
    Show-Version

    if (-not (Test-UvInstalled)) { return }

    $arguments = @('accumulation')
    if ($Symbols.Count -gt 0) { $arguments += $Symbols }
    $arguments += @('--channel', $Channel, '--limit', "$Limit", '--deep', "$Deep")
    if ($Heartbeat) { $arguments += '--heartbeat' }

    $ok = Invoke-Step 'US accumulation screen' $arguments

    Write-Section 'Summary'
    if ($ok) {
        Write-Ok "Candidates found and sent to $Channel."
    }
    else {
        # A non-zero exit here is "nothing passed the screen", which is the
        # normal case for this shape and not a failure of the job. The log says
        # which filter rejected what, so the two are still distinguishable.
        Write-Warn 'No candidates today (or the run failed). See the output above.'
    }
}
finally {
    Stop-Transcript | Out-Null
}

Write-Host ''
Write-Host "Log: $log"
