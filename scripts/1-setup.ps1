<#
.SYNOPSIS
    Install dependencies and prepare .env.

.DESCRIPTION
    Run this once, first. It installs everything stock-ai needs, creates a .env
    from the template if you do not have one, and then reports which API keys
    are still missing so you know exactly what to fill in.

    Messages are ASCII-only on purpose: this project has already been bitten
    once by garbled Japanese in a Windows console, and a setup script that
    prints mojibake is worse than one that prints plain English.

.EXAMPLE
    .\scripts\1-setup.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Write-Section 'stock-ai setup'
Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Host 'Installing dependencies (this can take a few minutes the first time)...'
uv sync --all-extras
if ($LASTEXITCODE -ne 0) {
    Write-Err 'uv sync failed. See the messages above.'
    Exit-WithPause 1
}
Write-Ok 'Dependencies installed.'

# --- .env -----------------------------------------------------------------

if (-not (Test-Path '.env')) {
    if (Test-Path '.env.example') {
        Copy-Item '.env.example' '.env'
        Write-Ok 'Created .env from .env.example.'
    }
    else {
        Write-Err '.env.example is missing; cannot create .env.'
        Exit-WithPause 1
    }
}
else {
    Write-Host '.env already exists - leaving it alone.'
}

# --- which keys are still blank -------------------------------------------

Write-Section 'API keys'

$required = [ordered]@{
    'JQUANTS_API_KEY'   = 'JP prices, financials, and the symbol universe'
    'EDINET_API_KEY'    = 'JP statutory disclosures (watchlist)'
    'ANTHROPIC_API_KEY' = 'AI summaries, importance rating, natural-language search'
}

$missing = @()
foreach ($name in $required.Keys) {
    if (Test-EnvKeySet -Name $name) {
        Write-Ok "$name is set"
    }
    else {
        Write-Warn "$name is EMPTY  --  $($required[$name])"
        $missing += $name
    }
}

Write-Host ''
if ($missing.Count -gt 0) {
    Write-Host 'Next: store each key above. Double-click the API key .bat in this'
    Write-Host 'folder, or run:'
    foreach ($name in $missing) {
        Write-Host "  .\scripts\set-key.ps1 $name"
    }
    Write-Host ''
    Write-Host 'That hides the value as you paste it, so the key does not end up in'
    Write-Host 'the PowerShell history file. Editing .env by hand also works.'
    Write-Host ''
    Write-Host 'Then run the step-2 .bat in this folder.'
}
else {
    Write-Ok 'All keys present.'
    Write-Host 'Next: run the step-2 .bat in this folder.'
}

Exit-WithPause 0
