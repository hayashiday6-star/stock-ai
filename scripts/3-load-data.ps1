<#
.SYNOPSIS
    Load the symbol universe, then backfill prices and financial statements.

.DESCRIPTION
    The long one. TSE Prime is around 1,600 names and each dataset costs one
    request per symbol, so a full run takes tens of minutes over a network that
    may well drop in the middle.

    That is fine: interrupting is safe. Symbols already up to date are skipped
    without a request, so re-running after a Ctrl-C or a dropped connection
    picks up where it left off, and re-running after failures retries only
    those.

    Start with -Segment growth -Limit 20 to confirm the pipeline works before
    committing to Prime. Noticing a problem at 20 symbols is much cheaper than
    noticing it at 1,600.

.PARAMETER Segment
    Which market segment to load.

.PARAMETER Limit
    Cap the symbol count. 0 means no cap.

.PARAMETER Lookback
    Days of price history to backfill for a symbol that has none.

.PARAMETER SkipStatements
    Load prices only.

.PARAMETER Symbols
    Load these codes instead of a whole segment, e.g. 7203,6758. Use this when
    the listings endpoint is not in your J-Quants plan: listings, prices, and
    statements are separate endpoints, so being unable to enumerate the market
    says nothing about whether you can load one.

.PARAMETER AsOf
    Snapshot date (YYYY-MM-DD) for the universe request, for a plan that serves
    data on a delay.

.EXAMPLE
    .\scripts\3-load-data.ps1 -Segment growth -Limit 20
    .\scripts\3-load-data.ps1 -Segment prime -Lookback 1500
    .\scripts\3-load-data.ps1 -Symbols 7203,6758,9984
#>
[CmdletBinding()]
param(
    [ValidateSet('prime', 'standard', 'growth', 'all')]
    [string]$Segment = 'prime',

    [ValidateRange(0, 10000)]
    [int]$Limit = 0,

    [ValidateRange(30, 10000)]
    [int]$Lookback = 1500,

    [switch]$SkipStatements,

    [string[]]$Symbols = @(),

    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$AsOf
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Write-Section "stock-ai data load ($Segment)"
Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

if (-not (Test-EnvKeySet -Name 'JQUANTS_API_KEY')) {
    Write-Err 'JQUANTS_API_KEY is not set in .env. Run the step-1 .bat first.'
    Exit-WithPause 1
}

Write-Host 'Interrupting is safe - re-run this script to resume.'
$started = Get-Date

# --- 1. decide which symbols to load ---------------------------------------

# An explicit list skips the universe request entirely. Otherwise ask for the
# segment, and if that is refused, keep going with whatever is already stored:
# the listings endpoint sits on a different J-Quants plan tier from prices and
# statements, so losing it must not cost the two that still work.

$named = @($Symbols | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } |
    Where-Object { $_ })

if ($named.Count -gt 0) {
    Write-Host ''
    Write-Host "Loading $($named.Count) named symbol(s); skipping the universe request."
    $selector = @('--symbols', ($named -join ','))
}
else {
    $universeArgs = @('universe', '--segment', $Segment)
    if ($Limit -gt 0) { $universeArgs += @('--limit', "$Limit") }
    if ($AsOf) { $universeArgs += @('--as-of', $AsOf) }

    if (Invoke-Step "Universe: $Segment" $universeArgs) {
        $selector = @('--segment', 'stored')
    }
    else {
        $universeFailed = $true
        $selector = @('--segment', 'stored')
        Write-Warn 'The universe request failed. Continuing with symbols already stored.'
        Write-Host 'If the next steps report "No symbols to process", the database is'
        Write-Host 'empty and there is nothing to fall back to. Name the codes instead -'
        Write-Host 'prices and statements are separate endpoints and may well work:'
        Write-Host '    .\scripts\3-load-data.ps1 -Symbols 7203,6758,9984'
    }
}

# --- 2. prices -------------------------------------------------------------

$priceArgs = @('bulk-fetch', '--what', 'prices', '--lookback', "$Lookback") + $selector
if ($Limit -gt 0) { $priceArgs += @('--limit', "$Limit") }

$pricesOk = Invoke-Step 'Prices' $priceArgs

# --- 3. statements ---------------------------------------------------------

$statementsOk = $true
if ($SkipStatements) {
    Write-Host ''
    Write-Host 'Skipping statements (-SkipStatements).'
}
else {
    $statementArgs = @('bulk-fetch', '--what', 'statements') + $selector
    if ($Limit -gt 0) { $statementArgs += @('--limit', "$Limit") }
    $statementsOk = Invoke-Step 'Statements' $statementArgs
}

# --- summary ---------------------------------------------------------------

Write-Section 'Done'
Write-Host ("Elapsed: {0:hh\:mm\:ss}" -f ((Get-Date) - $started))

if ($pricesOk -and $statementsOk) {
    Write-Ok 'Everything loaded.'
}
else {
    Write-Warn 'Some symbols failed. Re-run this script to retry only those.'
}

if ($universeFailed) {
    Write-Host ''
    Write-Warn 'The symbol list is whatever was already stored - it was not refreshed.'
    Write-Host 'The message from J-Quants above says why. If it points at the date'
    Write-Host 'rather than the endpoint, your plan serves delayed data; retry with:'
    Write-Host "    .\scripts\3-load-data.ps1 -Segment $Segment -AsOf 2025-01-31"
}

Write-Host ''
Write-Host 'Next:'
Write-Host '  Dashboard    :  uv run streamlit run src/stock_ai/dashboard/app.py'
Write-Host '  Check a score:  uv run stock-ai factor-test 2024-06-28 --preset tenbagger'

Exit-WithPause 0
