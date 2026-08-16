<#
.SYNOPSIS
    Store an API key in .env without it appearing on screen or in history.

.DESCRIPTION
    Run this instead of editing .env by hand. It prompts for the value with the
    input hidden, strips the quotes and whitespace a paste usually brings along,
    replaces any existing assignment rather than appending a second one, and
    writes UTF-8 without a BOM.

    Hiding the input is the point. A key typed into a normal command line is
    written to the PSReadLine history file in plain text and stays there.

.PARAMETER Name
    The variable to set, e.g. EDINET_API_KEY.

.EXAMPLE
    .\scripts\set-key.ps1 EDINET_API_KEY
    .\scripts\set-key.ps1 -Name JQUANTS_API_KEY
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet(
        'JQUANTS_API_KEY',
        'EDINET_API_KEY',
        'ANTHROPIC_API_KEY',
        'OPENAI_API_KEY',
        'GEMINI_API_KEY',
        'DISCORD_WEBHOOK_URL',
        'LINE_CHANNEL_ACCESS_TOKEN',
        'TELEGRAM_BOT_TOKEN',
        'TELEGRAM_CHAT_ID'
    )]
    [string]$Name = 'EDINET_API_KEY'
)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Write-Section "Set $Name"
Write-Host '.env is git-ignored, so this never reaches GitHub.'
Write-Host ''

$value = Read-EnvKeyValue -Name $Name
if (-not (Set-EnvKey -Name $Name -Value $value)) { Exit-WithPause 1 }

Write-Host ''
Write-Host 'Verify it took effect with:'
Write-Host '  uv run stock-ai info'

Exit-WithPause 0
