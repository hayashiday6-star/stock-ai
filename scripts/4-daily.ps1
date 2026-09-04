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
    Symbols whose prices to refresh. Omitting them skips the price job
    entirely and runs only the watchlist check - it does *not* refresh
    everything stored. A nightly pass over 1,500-odd stored names belongs in
    'bulk-fetch', which throttles and resumes; this job has neither and would
    spend the rate-limit budget in one burst.

.PARAMETER Provider
    AI provider for the watchlist monitor.

.PARAMETER Channel
    Notification channel for alerts. Omit to print them instead.

.PARAMETER Feed
    Disclosure feed the monitor reads: all | edinet | news.

.PARAMETER Source
    Override for Japanese listings only: jquants or tachibana. Left at the
    default, four-digit codes follow JP_PRICE_SOURCE in .env; everything else
    always goes to yfinance regardless of this flag.

.PARAMETER MaxCost
    Dollars. The monitor job is skipped, and the run reported as failed, when
    its priced worst case is above this. 0 disables the cap.

    Set it whenever -Provider is a paid one. This runs unattended: how many
    disclosures are filed on a given day is not something the schedule
    controls, and the check itself costs nothing.

.PARAMETER Limit
    Disclosures pulled per watched symbol. The news feed returns up to this
    many for every name whether or not anything happened, so it sets the size
    of the run more than the news does. 5 keeps a nightly pass small.

.PARAMETER Heartbeat
    Send a notification even when nothing cleared the threshold. Without it a
    quiet night and a job that never ran look identical from the phone.

.PARAMETER Register
    Create (or replace) the scheduled task instead of running the job now.

.PARAMETER At
    Local time for -Register, as HH:mm.

.PARAMETER Interactive
    Ask for the settings instead of taking them as parameters. Prompting here
    rather than in a .bat is deliberate: cmd interpolates a variable into the
    command line unquoted, so a pasted value containing a space silently
    becomes several arguments and lands on the wrong parameter.

.EXAMPLE
    .\scripts\4-daily.ps1 -Provider claude -Channel discord
    .\scripts\4-daily.ps1 -Register -At 18:00 -Provider claude -Channel discord -Feed edinet
    .\scripts\4-daily.ps1 -Register -Symbols 7203,6758,AAPL -Feed edinet -Channel discord
#>
[CmdletBinding()]
param(
    [string[]]$Symbols = @(),
    [string]$Provider = 'claude',
    [string]$Channel,
    [ValidateSet('all', 'edinet', 'news')]
    [string]$Feed = 'all',
    [ValidateSet('yfinance', 'jquants', 'tachibana')]
    [string]$Source = 'yfinance',
    [double]$MaxCost = 0,
    [ValidateRange(1, 100)]
    [int]$Limit = 5,
    [switch]$Heartbeat,
    [switch]$Register,
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$At = '18:00',
    [switch]$Interactive
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

. "$PSScriptRoot\_common.ps1"

# --- interactive settings --------------------------------------------------

function Read-Setting {
    <#
    .SYNOPSIS
        Ask until the answer is usable, showing what was wrong each time.
    #>
    param(
        [Parameter(Mandatory)][string]$Prompt,
        [string]$Default = '',
        [string]$Pattern,
        [string]$Hint
    )
    while ($true) {
        $suffix = if ($Default) { " [$Default]" } else { ' (optional)' }
        # Trim: a pasted answer usually brings trailing spaces, and a value
        # with a space in it is what broke this in the first place.
        $answer = (Read-Host "$Prompt$suffix").Trim()
        if (-not $answer) { return $Default }
        if (-not $Pattern -or $answer -match $Pattern) { return $answer }
        Write-Warn "'$answer' is not valid here. $Hint"
    }
}

if ($Interactive) {
    Write-Section 'Daily job settings'
    Write-Host 'Press Enter to accept the value in brackets.'
    Write-Host ''

    $At = Read-Setting -Prompt 'Time to run (24h)' -Default $At `
        -Pattern '^([01]\d|2[0-3]):[0-5]\d$' -Hint 'Use HH:MM, e.g. 18:00.'

    Write-Host ''
    Write-Host 'Symbols whose prices to refresh, comma separated. Four-digit'
    Write-Host 'codes follow JP_PRICE_SOURCE in .env and the rest go to'
    Write-Host 'yfinance, so 7203,6758,AAPL is fine. Leave empty to only check'
    Write-Host 'the watchlist.'
    $symbolText = Read-Setting -Prompt 'Symbols' `
        -Pattern '^[A-Za-z0-9.,\-]+$' -Hint 'Letters, digits, dots and commas only.'
    if ($symbolText) { $Symbols = $symbolText -split ',' | Where-Object { $_ } }

    Write-Host ''
    Write-Host 'Disclosure feed:  all = EDINET + news,  edinet = JP filings,  news = wire'
    $Feed = Read-Setting -Prompt 'Feed' -Default 'all' `
        -Pattern '^(all|edinet|news)$' -Hint 'Type all, edinet or news.'

    Write-Host ''
    Write-Host 'Notification channel. Empty prints alerts instead of sending them.'
    $Channel = Read-Setting -Prompt 'Channel' `
        -Pattern '^(console|discord|telegram|line)$' `
        -Hint 'Type console, discord, telegram or line.'

    Write-Host ''
    Write-Host 'AI provider that rates each disclosure. "dummy" needs no key and'
    Write-Host 'costs nothing; the others need their key in .env and bill per run.'
    Write-Host 'A provider whose key is missing fails the monitor job every day,'
    Write-Host 'so dummy is the safe default until a key is confirmed.'
    $Provider = Read-Setting -Prompt 'Provider' -Default 'dummy' `
        -Pattern '^(dummy|claude|openai|gemini)$' `
        -Hint 'Type dummy, claude, openai or gemini.'

    if ($Provider -ne 'dummy') {
        Write-Host ''
        Write-Host 'A paid provider bills every night with nobody watching, and the'
        Write-Host 'number of disclosures filed on a given day is not up to you. The'
        Write-Host 'cap below stops the run when the priced worst case is above it;'
        Write-Host 'checking costs nothing, and a skipped day is retried the next.'
        Write-Host 'Measured: about $0.07 for 60 items on haiku, $0.10 on opus.'
        Write-Host 'The cap is compared against a worst case, which runs several'
        Write-Host 'times the real figure - set it well above what you expect to pay.'
        $capText = Read-Setting -Prompt 'Daily cap in USD (0 = no cap)' -Default '1.00' `
            -Pattern '^\d+(\.\d+)?$' -Hint 'Type a number, e.g. 1.00'
        $MaxCost = [double]$capText
    }

    Write-Host ''
    Write-Host 'Disclosures pulled per watched symbol. The news feed returns this'
    Write-Host 'many for every name whether or not anything happened, so it sets'
    Write-Host 'the size of the run more than the news does.'
    $limitText = Read-Setting -Prompt 'Per-symbol limit' -Default "$Limit" `
        -Pattern '^([1-9]|[1-9]\d|100)$' -Hint 'Type a number from 1 to 100.'
    $Limit = [int]$limitText

    Write-Host ''
    Write-Host 'Notify even when nothing clears the threshold? Without this, a quiet'
    Write-Host 'night and a job that never ran look identical from the phone.'
    $beatText = Read-Setting -Prompt 'Send a heartbeat (y/n)' -Default 'n' `
        -Pattern '^[yn]$' -Hint 'Type y or n.'
    $Heartbeat = ($beatText -eq 'y')

    Write-Host ''
    $summary = "daily at $At, feed $Feed, provider $Provider, limit $Limit"
    if ($MaxCost -gt 0) { $summary += ", cap `$$MaxCost" }
    if ($Heartbeat) { $summary += ', heartbeat on' }
    if ($Symbols.Count -gt 0) { $summary += ", symbols $($Symbols -join ',')" }
    if ($Channel) { $summary += ", notifying $Channel" }
    Write-Host "Registering: $summary"
    $Register = $true
}

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
    $arguments += @('-Feed', $Feed, '-Source', $Source)
    if ($MaxCost -gt 0) { $arguments += @('-MaxCost', $MaxCost) }
    $arguments += @('-Limit', $Limit)
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
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
            -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
            -Settings $settings -Description 'stock-ai: refresh prices and check the watchlist' `
            -Force | Out-Null
        Write-Ok "Registered '$taskName' to run daily at $At."
        Write-Host 'Manage it in Task Scheduler (taskschd.msc).'
        Write-Host "Run it now with:  Start-ScheduledTask -TaskName '$taskName'"
        Write-Host ''
        # No -Principal is set, so the task inherits the default: it runs as
        # this user and only while that user is logged on. Saying so matters -
        # "daily" is otherwise read as "even when the machine is at the login
        # screen", and the job would appear to have silently stopped.
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

$logDir = Join-Path $root 'logs\daily'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$log = Join-Path $logDir "$(Get-Date -Format 'yyyy-MM-dd').log"

Start-Transcript -Path $log -Append | Out-Null
try {
    Write-Section "stock-ai daily  $(Get-Date -Format 'yyyy-MM-dd HH:mm')"

    if (-not (Test-UvInstalled)) { return }

    $arguments = @('daily', '--once', '--provider', $Provider, '--feed', $Feed, '--source', $Source)
    if ($Channel) { $arguments += @('--channel', $Channel) }
    if ($MaxCost -gt 0) { $arguments += @('--max-cost', $MaxCost) }
    $arguments += @('--limit', $Limit)
    if ($Heartbeat) { $arguments += '--heartbeat' }
    # A comma-joined -Symbols survives Task Scheduler's argument flattening.
    foreach ($symbol in ($Symbols -split ',' | Where-Object { $_ })) {
        $arguments += $symbol.Trim()
    }

    $ok = Invoke-Step 'Daily pipeline' $arguments

    # 月に1枚だけ、上場銘柄の名簿を残す。
    #
    # 立花のマスタは現存銘柄しか返さないが、**毎月1枚ずつ残せば差分が
    # 「その月に消えた銘柄」になる**。J-Quants の名簿が 2026-09 で止まっても、
    # それ以降の生存バイアスは自前で直せる。
    #
    # 毎日呼んでよい。ファイル名が月なので、その月の分があれば書かない。
    # **「毎月やる」と覚えておく必要のある運用は、いずれ忘れられる。**
    Invoke-Step '月次の銘柄名簿' @('universe-snapshot') | Out-Null

    if ($ok) { Write-Ok 'Finished.' } else { Write-Err 'Finished with errors.' }
}
finally {
    Stop-Transcript | Out-Null
}

# No pause: this is meant to run unattended.
exit 0
