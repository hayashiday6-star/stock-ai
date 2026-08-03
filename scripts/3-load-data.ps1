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

.EXAMPLE
    .\scripts\3-load-data.ps1 -Segment growth -Limit 20
    .\scripts\3-load-data.ps1 -Segment prime -Lookback 1500
#>
[CmdletBinding()]
param(
    [ValidateSet('prime', 'standard', 'growth', 'all')]
    [string]$Segment = 'prime',

    [ValidateRange(0, 10000)]
    [int]$Limit = 0,

    [ValidateRange(30, 10000)]
    [int]$Lookback = 1500,

    [switch]$SkipStatements
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Write-Section "stock-ai data load ($Segment)"

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

if (-not (Test-EnvKeySet -Name 'JQUANTS_API_KEY')) {
    Write-Err 'JQUANTS_API_KEY is not set in .env. Run .\scripts\1-setup.ps1 first.'
    Exit-WithPause 1
}

Write-Host 'Interrupting is safe - re-run this script to resume.'
$started = Get-Date

# --- 1. universe -----------------------------------------------------------

$universeArgs = @('universe', '--segment', $Segment)
if ($Limit -gt 0) { $universeArgs += @('--limit', "$Limit") }

if (-not (Invoke-Step "Universe: $Segment" $universeArgs)) {
    Write-Err 'Could not load the universe; nothing else can run without it.'
    Exit-WithPause 1
}

# --- 2. prices -------------------------------------------------------------

$priceArgs = @('bulk-fetch', '--what', 'prices', '--segment', 'stored', '--lookback', "$Lookback")
if ($Limit -gt 0) { $priceArgs += @('--limit', "$Limit") }

$pricesOk = Invoke-Step 'Prices' $priceArgs

# --- 3. statements ---------------------------------------------------------

$statementsOk = $true
if ($SkipStatements) {
    Write-Host ''
    Write-Host 'Skipping statements (-SkipStatements).'
}
else {
    $statementArgs = @('bulk-fetch', '--what', 'statements', '--segment', 'stored')
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

Write-Host ''
Write-Host 'Next:'
Write-Host '  Dashboard    :  .\dashboard.bat   (or: uv run streamlit run src/stock_ai/dashboard/app.py)'
Write-Host '  Check a score:  uv run stock-ai factor-test 2024-06-28 --preset tenbagger'

Exit-WithPause 0
