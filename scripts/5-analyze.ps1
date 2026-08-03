<#
.SYNOPSIS
    What to run once the data is loaded: fill any gaps, screen, then test the score.

.DESCRIPTION
    Step 3 loads data. This is what makes it answer questions.

    It runs in the order that matters. The statement catch-up comes first
    because both later steps read what it writes, and a database loaded before
    the snapshot fix has statements but no valuation figures - which makes a
    screen return nothing and look like a market with no cheap stocks in it.

    Nothing here costs a request for data already stored, so re-running is safe
    and cheap.

.PARAMETER AsOf
    Formation date for the factor test. Must sit inside the loaded price
    history, with enough time after it to measure a forward return.

.PARAMETER Preset
    Which factor set to test.

.PARAMETER SkipCatchUp
    Skip the statement catch-up and go straight to screening.

.EXAMPLE
    .\scripts\5-analyze.ps1
    .\scripts\5-analyze.ps1 -AsOf 2024-12-30 -Preset default
#>
[CmdletBinding()]
param(
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$AsOf = '2024-06-28',

    [ValidateSet('tenbagger', 'default')]
    [string]$Preset = 'tenbagger',

    [switch]$SkipCatchUp
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Write-Section 'stock-ai analysis'
Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

# --- 1. fill in anything the loader could not write ------------------------

if ($SkipCatchUp) {
    Write-Host ''
    Write-Host 'Skipping the statement catch-up (-SkipCatchUp).'
}
else {
    Write-Host ''
    Write-Host 'Filling in any missing valuation snapshots. Symbols that already'
    Write-Host 'have one are skipped without a request, so this is cheap to repeat.'
    if (-not (Invoke-Step 'Statements catch-up' @(
                'bulk-fetch', '--what', 'statements', '--segment', 'stored'))) {
        Write-Warn 'The catch-up did not finish. The steps below may see partial data.'
    }
}

# --- 2. screen -------------------------------------------------------------

$screenOk = Invoke-Step 'Screen: growing and not expensive' @(
    'screen', '--min-revenue-growth', '0.1', '--min-profit-growth', '0.1', '--max-per', '25'
)
Write-Host ''
Write-Host '  >> 0 matches is a real answer, not necessarily a fault: these are'
Write-Host '     strict thresholds. "Matched N" followed by an empty table is a'
Write-Host '     fault, and the command says so when it happens.'

# --- 3. is the score worth anything? ---------------------------------------

$factorOk = Invoke-Step "Factor test: $Preset as of $AsOf" @(
    'factor-test', $AsOf, '--preset', $Preset
)

Write-Section 'Done'
if ($screenOk) { Write-Ok 'Screen ran.' } else { Write-Err 'Screen failed.' }
if ($factorOk) { Write-Ok 'Factor test ran.' } else { Write-Err 'Factor test failed.' }

Write-Host ''
Write-Host 'Read the factor test by its t-statistic, not its excess return.'
Write-Host 'A universe of pure random returns produced a +5.4% "edge" in testing;'
Write-Host 'its t was +1.39, which is how you could tell it was noise. Below 2,'
Write-Host 'the score has not earned a place in a decision.'
Write-Host ''
Write-Host 'Dashboard:  uv run streamlit run src/stock_ai/dashboard/app.py'

Exit-WithPause 0
