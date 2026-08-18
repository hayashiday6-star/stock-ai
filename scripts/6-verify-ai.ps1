<#
.SYNOPSIS
    Verify the AI and notification features - the last two that have never run.

.DESCRIPTION
    Every other subsystem in this project was checked against real data, and
    doing so found fifteen bugs that produced plausible wrong numbers rather
    than errors. AI and notifications were left out for one reason: they are
    the only ones that need a paid key, so they could not be exercised without
    spending money.

    This runs them, in the order that costs least to learn most:

      1. Preflight   - free. Key present, package installed, model selected.
      2. Notification- free. The console channel, then any webhook configured.
      3. Estimate    - free. 'ai-cost' counts tokens without generating any.
      4. Confirm     - the paid half does not start until you say so.
      5. AI          - paid. sentiment, summarize, ask, monitor; each prints
                       what it actually spent, so the estimate above can be
                       checked against the outcome.

    The paid half is bounded by max_tokens on every call: the ceiling for all
    four checks together is well under ten cents at opus rates, and less than
    two at haiku rates. Set ANTHROPIC_MODEL in .env to choose.

.PARAMETER SkipPaid
    Run only the free checks (1-3) and stop before spending anything.

.PARAMETER Channel
    Notification channel to test: console, discord, telegram, or line.
    Anything but console needs its credential in .env.

.PARAMETER Feed
    Disclosure feed used for BOTH the estimate and the monitor run:
    all, edinet, or news. They must match, or the two numbers describe
    different work and the comparison between them means nothing.

.PARAMETER Symbol
    Watchlist symbol used for the monitor check.

.EXAMPLE
    .\scripts\6-verify-ai.ps1
    .\scripts\6-verify-ai.ps1 -SkipPaid
    .\scripts\6-verify-ai.ps1 -Channel discord
#>
[CmdletBinding()]
param(
    [switch]$SkipPaid,
    [ValidateSet('console', 'discord', 'telegram', 'line')]
    [string]$Channel = 'console',
    [ValidateSet('all', 'edinet', 'news')]
    [string]$Feed = 'all',
    [string]$Symbol = '7203'
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

$report = Join-Path (Get-Location) 'verify-ai-output.txt'
Start-Transcript -Path $report -Force | Out-Null

try {
    Write-Section 'stock-ai: AI and notification verification'
    Write-Host "Date    : $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
    Write-Host "Channel : $Channel"
    Write-Host "Feed    : $Feed"
    Write-Host "Symbol  : $Symbol"
    Show-Version

    if (-not (Test-UvInstalled)) { return }

    $results = [ordered]@{}

    # The AI checks send Japanese text, so this file is UTF-8 *with a BOM* -
    # the only encoding Windows PowerShell 5.1 reads correctly without being
    # told. Every other script here is deliberately ASCII for that reason.
    #
    # If the BOM is ever lost - an editor "cleaning" the file, a copy through a
    # tool that rewrites encodings - these literals decode to mojibake, and the
    # run would then pay to have Claude rate garbage and return a perfectly
    # plausible answer about it. So they are checked against their real code
    # points before a single billed call is made.
    $sampleSentiment = '通期の営業利益予想を上方修正しました。'
    $sampleSummary = '当社は本日、2026年3月期第1四半期の連結業績を発表しました。' +
        '売上高は前年同期比12.4%増の1,240億円、営業利益は同18.2%増の156億円となりました。' +
        '半導体関連需要の回復と価格改定の効果によるものです。通期見通しは据え置きます。'
    $sampleQuestion = 'PER15倍以下でROE10%以上の日本株'

    # U+901A 通 and U+5F53 当 open the first two; U+672C 本 is the second-to-last
    # character of the question (its last is 株).
    $encodingOk = ([int][char]$sampleSentiment[0] -eq 0x901A) -and
        ([int][char]$sampleSummary[0] -eq 0x5F53) -and
        ([int][char]$sampleQuestion[$sampleQuestion.Length - 2] -eq 0x672C)

    # --- 1. Preflight (free) ----------------------------------------------
    Write-Section '1. Preflight (nothing is spent here)'

    $hasKey = Test-EnvKeySet -Name 'ANTHROPIC_API_KEY'
    if ($hasKey) {
        Write-Ok 'ANTHROPIC_API_KEY is set in .env.'
    }
    else {
        Write-Warn 'ANTHROPIC_API_KEY is NOT set in .env.'
        # Named by path, not by the .bat's Japanese filename: this line prints
        # to a cp932 console that would render the name as mojibake.
        Write-Host '  Add it with: powershell -File scripts\set-key.ps1'
        Write-Host '  (that is what the API key .bat on the desktop runs).'
        Write-Host '  Without it the free checks below still run; the paid ones cannot.'
    }

    # 'info' prints which model the AI commands will call. That line decides
    # the bill and is otherwise invisible until the invoice arrives.
    $results['info'] = Invoke-Step 'configuration (model, SDK, key fingerprints)' @('info')

    # The key is only half of what an AI call needs, and this preflight checked
    # only that half. A run then asked to confirm spending money and failed
    # four checks on a missing package - the exact thing a preflight exists to
    # catch. 'uv sync' is cheap and makes the answer current rather than
    # remembered.
    Write-Host ''
    Write-Host '--- checking the environment is complete' -ForegroundColor Cyan
    Write-Host '    uv sync' -ForegroundColor DarkGray
    & uv sync 2>&1 | Out-Host
    & uv run python -c "import anthropic" 2>&1 | Out-Null
    $hasSdk = ($LASTEXITCODE -eq 0)
    if ($hasSdk) {
        Write-Ok 'The anthropic SDK is installed.'
    }
    else {
        Write-Err 'The anthropic SDK is NOT installed, so no AI command can run.'
        Write-Host '  Every AI check below would fail on the package, not on your key.'
        Write-Host '  Fix: git pull (the fix is in pyproject.toml), then uv sync.'
    }

    # --- 2. Notifications (free unless a webhook is configured) -----------
    Write-Section '2. Notifications'

    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm'
    $results["notify ($Channel)"] = Invoke-Step "notify: send a test message via $Channel" @(
        'notify', "stock-ai notification test - $stamp", '--channel', $Channel
    )
    if ($Channel -eq 'console') {
        Write-Host ''
        Write-Host '  >> EXPECT the message printed above. That proves the notifier'
        Write-Host '     path works; it does not prove a webhook does. To check a real'
        Write-Host '     one, put DISCORD_WEBHOOK_URL in .env and re-run with'
        Write-Host '     -Channel discord.'
    }
    else {
        Write-Host ''
        Write-Host "  >> EXPECT the message to arrive in $Channel within a few seconds."
        Write-Host '     A clean exit here means the service accepted the POST, not that'
        Write-Host '     you saw it - go and look.'
    }

    # --- 3. Estimate (free) ------------------------------------------------
    Write-Section '3. What the monitor run would cost (no model calls)'

    # The same --feed as the monitor below. The first run of this script priced
    # 'all' and then ran 'edinet', so the estimate counted two disclosures and
    # the run judged one - which reads exactly like a broken estimate, and was
    # only this script asking two different questions.
    $results['ai-cost'] = Invoke-Step 'ai-cost: price the next monitor run' @(
        'ai-cost', '--feed', $Feed
    )
    Write-Host ''
    Write-Host '  >> EXPECT two rows, a floor and a ceiling. Input tokens are counted,'
    Write-Host '     not guessed. If it says nothing is pending, the monitor has'
    Write-Host '     already seen everything and the run below will cost nothing.'

    # --- 4. Confirm --------------------------------------------------------
    # Every reason to stop is decided here rather than with an early return, so
    # the summary and the transcript path still print. A verification run that
    # exits silently is the one people re-run blind.
    $runPaid = $true
    Write-Section '4. The next four checks are billed'

    if ($SkipPaid) {
        Write-Warn 'Skipping them (-SkipPaid). Nothing will be spent.'
        $runPaid = $false
    }
    elseif (-not $hasKey) {
        Write-Err 'No ANTHROPIC_API_KEY in .env, so the AI commands cannot run.'
        $runPaid = $false
    }
    elseif (-not $hasSdk) {
        Write-Err 'The anthropic SDK is missing, so every check below would fail on that.'
        Write-Host '  Not asking you to confirm spending on calls that cannot be made.'
        $runPaid = $false
    }
    elseif (-not $encodingOk) {
        Write-Err 'This script file lost its UTF-8 BOM; the Japanese samples are corrupt.'
        Write-Host '  Sending them would pay Claude to analyse mojibake and get back a'
        Write-Host '  confident answer about nothing. Restore the file with:'
        Write-Host '      git checkout -- scripts/6-verify-ai.ps1'
        $runPaid = $false
    }
    else {
        Write-Host 'They call Claude for real. Every call has a max_tokens ceiling, so'
        Write-Host 'the worst case for all four together is under ten cents at opus'
        Write-Host 'rates - but it is not zero, and it lands on your card.'
        Write-Host ''
        $answer = Read-Host 'Type yes to run them (anything else stops here)'
        if ($answer.Trim().ToLower() -ne 'yes') {
            Write-Warn 'Stopped at your request. Nothing was spent.'
            $runPaid = $false
        }
    }

    # --- 5. AI (paid) ------------------------------------------------------
    # Cheapest first. sentiment caps at 8 output tokens, so if the key is
    # rejected or the model name is wrong, that is learned for a fraction of a
    # cent rather than at the end of a full monitor run.
    if ($runPaid) {
        Write-Section '5. AI features'

        $results['sentiment'] = Invoke-Step 'sentiment: classify a sentence' @(
            'sentiment', $sampleSentiment, '--provider', 'claude'
        )
        Write-Host ''
        Write-Host '  >> EXPECT positive / neutral / negative, then a "spent:" line.'

        $results['summarize'] = Invoke-Step 'summarize: condense an IR excerpt' @(
            'summarize', $sampleSummary, '--provider', 'claude', '--max-words', '60'
        )
        Write-Host ''
        Write-Host '  >> EXPECT a short summary IN JAPANESE that keeps the numbers.'
        Write-Host '     English out means the language rule in the prompt stopped working;'
        Write-Host '     invented figures are the worse failure to look for.'

        $results['ask'] = Invoke-Step 'ask: screen from a plain-language question' @(
            'ask', $sampleQuestion, '--provider', 'claude'
        )
        Write-Host ''
        Write-Host '  >> EXPECT an "Understood as:" line naming per<=15 and roe>=10 BEFORE'
        Write-Host '     any results. That line is the check: the model fills in fixed'
        Write-Host '     criteria and never writes a query, so if the interpretation is'
        Write-Host '     wrong you can see it is wrong rather than trusting the table.'

        $results['watch'] = Invoke-Step "watch: make sure $Symbol is watched" @(
            'watch', $Symbol, '--market', 'JP'
        )
        $results['monitor'] = Invoke-Step 'monitor: rate real disclosures' @(
            'monitor', '--provider', 'claude', '--feed', $Feed, '--lookback-days', '7'
        )
        Write-Host ''
        Write-Host '  >> EXPECT the "spent:" line to land at or below the ceiling that'
        Write-Host '     step 3 predicted. Above it means the estimate is wrong, which is'
        Write-Host '     worth reporting - a cost preview nobody can trust is worse than'
        Write-Host '     none, because it gets believed.'
    }

    # --- summary -----------------------------------------------------------
    Write-Section 'Summary'
    foreach ($name in $results.Keys) {
        if ($results[$name]) { Write-Ok $name } else { Write-Err $name }
    }

    $failed = @($results.Keys | Where-Object { -not $results[$_] })
    Write-Host ''
    if ($failed.Count -eq 0) {
        Write-Ok 'Every command exited cleanly.'
        Write-Host 'Still read the output: an AI command can succeed and be wrong.'
    }
    else {
        Write-Warn "$($failed.Count) check(s) failed: $($failed -join ', ')"
    }
}
finally {
    Stop-Transcript | Out-Null
    Write-Host ''
    Write-Host "Full output saved to: $report"
    Write-Host 'Paste that file back - it has the spend lines and the estimate together.'
}

Exit-WithPause 0
