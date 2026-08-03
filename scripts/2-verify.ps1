<#
.SYNOPSIS
    Check the four integrations that fail silently, and save a report.

.DESCRIPTION
    Three of this project's data sources were written against published API
    specs but never run against the live services. Every one of them fails the
    same way: not with an error, but with zero rows. That is the worst possible
    failure mode, because the pipeline downstream keeps "working" on nothing.

    So this runs each one deliberately and writes everything to
    verify-output.txt, which is the file to paste back when asking for help.

.PARAMETER Symbol
    JP security code used for the J-Quants and EDINET checks.

.EXAMPLE
    .\scripts\2-verify.ps1
    .\scripts\2-verify.ps1 -Symbol 6758
#>
[CmdletBinding()]
param(
    [string]$Symbol = '7203'
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

$report = Join-Path (Get-Location) 'verify-output.txt'
Start-Transcript -Path $report -Force | Out-Null

try {
    Write-Section 'stock-ai verification'
    Write-Host "Date    : $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
    Write-Host "Symbol  : $Symbol"
    Write-Host ''
    Write-Host 'What to look for is printed after each check.'

    if (-not (Test-UvInstalled)) { return }

    $results = [ordered]@{}

    # --- 1. yfinance dividend yield ---------------------------------------
    $results['yfinance prices'] = Invoke-Step 'yfinance: fetch prices (AAPL)' @('fetch', 'AAPL')
    $results['yfinance fundamentals'] = Invoke-Step 'yfinance: fundamentals (AAPL)' @(
        'fundamentals', 'AAPL'
    )
    Write-Host ''
    Write-Host '  >> EXPECT dividend_yield around 0.004. If it reads 0.4, the unit is wrong.'

    # --- 2. J-Quants statements -------------------------------------------
    $results['jquants statements'] = Invoke-Step "J-Quants: statements ($Symbol)" @(
        'statements', $Symbol
    )
    Write-Host ''
    Write-Host '  >> EXPECT Rows of roughly 5-20. Rows 0 means a renamed field, not an empty company.'

    # --- 3. J-Quants universe ---------------------------------------------
    $results['jquants universe'] = Invoke-Step 'J-Quants: universe (growth, 20)' @(
        'universe', '--segment', 'growth', '--limit', '20'
    )
    Write-Host ''
    Write-Host '  >> EXPECT 20 rows with names and sectors.'

    # --- 4. EDINET ---------------------------------------------------------
    $results['edinet watch'] = Invoke-Step "EDINET: add $Symbol to the watchlist" @(
        'watch', $Symbol, '--market', 'JP'
    )
    $results['edinet monitor'] = Invoke-Step 'EDINET: scan disclosures' @(
        'monitor', '--source', 'edinet', '--provider', 'dummy', '--lookback-days', '14'
    )
    Write-Host ''
    Write-Host '  >> EXPECT "Checked N" with N greater than 0. A persistent 0 means a renamed field.'

    # --- summary -----------------------------------------------------------
    Write-Section 'Summary'
    foreach ($name in $results.Keys) {
        if ($results[$name]) { Write-Ok $name } else { Write-Err $name }
    }

    $failed = @($results.Keys | Where-Object { -not $results[$_] })
    Write-Host ''
    if ($failed.Count -eq 0) {
        Write-Ok 'Every command exited cleanly.'
        Write-Host 'Still read the numbers above: a command can succeed and return nothing.'
    }
    else {
        Write-Warn "$($failed.Count) check(s) failed: $($failed -join ', ')"
    }
}
finally {
    Stop-Transcript | Out-Null
}

Write-Host ''
Write-Host "Full output saved to: $report"
Write-Host 'Paste that file when asking for help - it has everything needed to diagnose.'

Exit-WithPause 0
