<#
.SYNOPSIS
    Check that moomoo OpenD is installed, logged in, and reaching your account.

.DESCRIPTION
    moomoo has no API key. Authentication is a gateway program - OpenD - that
    runs on this PC, that you log into with your moomoo securities account, and
    that everything else then talks to over a local socket.

    That makes the failures unusually hard to read, because they all arrive as
    the same thing: a command that never comes back. OpenD not installed, OpenD
    installed but closed, OpenD open but sitting on a verification-code prompt,
    logged in but the wrong entity for this account - from Python, all four look
    identical.

    So this checks them one at a time and stops at the first break, and reports
    the process side of it too: whether an OpenD process is running at all is
    something PowerShell can see and Python cannot.

    Output is written to moomoo-output.txt. Account numbers are masked and
    balances are withheld unless -ShowAssets is given, because that file is the
    one that gets pasted when asking for help.

.PARAMETER Real
    Check the live-money account instead of the paper (SIMULATE) one. No order
    is ever placed either way.

.PARAMETER Unlock
    Also test the 6-digit trading PIN stored in MOOMOO_TRADE_PASSWORD. Live
    account only, and the account is locked again immediately afterwards.

.PARAMETER ShowAssets
    Print the balances rather than only confirming that they came back.

.PARAMETER Port
    Override the OpenD port. Defaults to MOOMOO_OPEND_PORT in .env, then 11111.

.EXAMPLE
    .\scripts\moomoo-check.ps1
    .\scripts\moomoo-check.ps1 -Real
    .\scripts\moomoo-check.ps1 -Real -Unlock
#>
[CmdletBinding()]
param(
    [switch]$Real,
    [switch]$Unlock,
    [switch]$ShowAssets,
    [int]$Port
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

$report = Join-Path (Get-Location) 'moomoo-output.txt'
Start-Transcript -Path $report -Force | Out-Null

try {
    Write-Section 'moomoo OpenD check'
    Write-Host "Date    : $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
    Write-Host "Account : $(if ($Real) { 'REAL (live money)' } else { 'SIMULATE (paper)' })"
    Show-Version
    Write-Host ''

    # --- is the gateway even running? ---------------------------------
    # Python can only see the port. "Nothing is listening" and "OpenD was
    # never installed" are the same fact from there, and a very different
    # next step here.
    $opend = @(Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessName -like '*OpenD*' })

    if ($opend.Count -gt 0) {
        Write-Ok "OpenD process running: $(($opend | ForEach-Object { $_.ProcessName }) -join ', ')"
    }
    else {
        Write-Warn 'No OpenD process is running on this PC.'
        Write-Host '  Start moomoo OpenD and log in, then run this again.'
        Write-Host '  Not installed yet? Download it (login required) from:'
        Write-Host '    https://www.moomoo.com/jp/support/topic7_476'
        Write-Host '  Step-by-step instructions: docs\MOOMOO_OPEND.md'
        Write-Host ''
        Write-Host '  Continuing anyway - the check below says the same thing more precisely.'
    }

    if (-not (Test-UvInstalled)) { return }

    # --- the actual check ---------------------------------------------
    $stepArgs = @('moomoo-check')
    if ($Real) { $stepArgs += @('--env', 'REAL') }
    if ($Port) { $stepArgs += @('--port', "$Port") }
    if ($Unlock) { $stepArgs += '--unlock' }
    if ($ShowAssets) { $stepArgs += '--show-assets' }

    Write-Host ''
    if ($Unlock -and -not $Real) {
        Write-Warn '-Unlock only applies to the live account; add -Real to use it.'
    }
    if ($Unlock -and -not (Test-EnvKeySet -Name 'MOOMOO_TRADE_PASSWORD')) {
        Write-Warn 'MOOMOO_TRADE_PASSWORD is not set in .env, so the PIN cannot be tested.'
        Write-Host '  Store it (hidden, never echoed) with:'
        Write-Host '    .\scripts\set-key.ps1 MOOMOO_TRADE_PASSWORD'
        Write-Host '  It is the 6-digit trading PIN, NOT your moomoo login password.'
    }

    $ok = Invoke-Step 'moomoo OpenD and account' $stepArgs

    Write-Section 'Summary'
    if ($ok) {
        Write-Ok 'OpenD is up and the account answered through it.'
        Write-Host 'Authentication is done. Nothing else is needed to read this account.'
    }
    else {
        Write-Err 'The chain broke. The line under the table names which link and what to do.'
        Write-Host 'Read that one line first - the steps after it were never attempted.'
    }
}
finally {
    Stop-Transcript | Out-Null
}

Write-Host ''
Write-Host "Full output saved to: $report"
Write-Host 'Account numbers are masked in that file, so it is safe to paste when asking for help.'

Exit-WithPause 0
