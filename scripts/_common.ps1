<#
.SYNOPSIS
    Shared helpers for the stock-ai setup scripts.

.DESCRIPTION
    Dot-sourced by the numbered scripts. Nothing here runs on import.

    All output is ASCII-only by design. A Windows console will happily mangle
    non-ASCII in a script whose encoding it guesses wrong, and a setup script
    that prints mojibake is worse than one that prints plain English.
#>

function Write-Section {
    <#
    .SYNOPSIS
        Print a banner so long runs are readable in the scrollback.
    #>
    param([Parameter(Mandatory)][string]$Title)

    Write-Host ''
    Write-Host ('=' * 60)
    Write-Host "  $Title"
    Write-Host ('=' * 60)
}

function Write-Ok {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "[OK]   $Message" -ForegroundColor Green
}

function Write-Warn {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Write-Err {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "[FAIL] $Message" -ForegroundColor Red
}

function Test-UvInstalled {
    <#
    .SYNOPSIS
        Whether the uv command is available, with install instructions if not.
    #>
    if (Get-Command uv -ErrorAction SilentlyContinue) { return $true }

    Write-Err 'uv is not installed (or not on PATH).'
    Write-Host '  Install it once with:'
    Write-Host '    winget install --id=astral-sh.uv -e'
    Write-Host '  then close and reopen PowerShell.'
    return $false
}

function Test-EnvKeySet {
    <#
    .SYNOPSIS
        Whether .env assigns a non-empty value to $Name.

    .DESCRIPTION
        Reads the file rather than the process environment: the point is to
        check what the user filled in, not what happens to be exported. The
        value itself is never printed or returned - only whether it is present.
    #>
    param([Parameter(Mandatory)][string]$Name)

    if (-not (Test-Path '.env')) { return $false }

    foreach ($line in Get-Content '.env' -Encoding UTF8) {
        $trimmed = $line.Trim()
        if ($trimmed.StartsWith('#')) { continue }
        if ($trimmed -notmatch "^$([regex]::Escape($Name))\s*=") { continue }

        $value = $trimmed.Substring($trimmed.IndexOf('=') + 1)
        # Strip an inline comment, then quotes, then whitespace.
        $value = ($value -split '\s+#')[0].Trim().Trim('"').Trim("'").Trim()
        return -not [string]::IsNullOrWhiteSpace($value)
    }
    return $false
}

function Invoke-Step {
    <#
    .SYNOPSIS
        Run a stock-ai command, echoing it first and reporting the outcome.

    .DESCRIPTION
        Returns $true on success. Failures are reported and returned, never
        thrown: a verification run should attempt every check rather than stop
        at the first one, because knowing which three of four failed is the
        whole point.

        The command's output is piped to Out-Host rather than left to fall out
        of the function. Without that pipe PowerShell folds native stdout into
        the return value, which breaks this twice over: the user never sees the
        output, and the caller gets a non-empty array that is truthy whether
        the command succeeded or not - so every failure reads as a pass.
    #>
    param(
        [Parameter(Mandatory)][string]$Title,
        [Parameter(Mandatory)][string[]]$Arguments
    )

    Write-Host ''
    Write-Host "--- $Title" -ForegroundColor Cyan
    Write-Host "    uv run stock-ai $($Arguments -join ' ')" -ForegroundColor DarkGray
    Write-Host ''

    & uv run stock-ai @Arguments 2>&1 | Out-Host
    $code = $LASTEXITCODE

    if ($code -eq 0) {
        Write-Ok $Title
        return $true
    }
    Write-Err "$Title (exit code $code)"
    return $false
}

function Exit-WithPause {
    <#
    .SYNOPSIS
        Exit, pausing first if the window would otherwise vanish.

    .DESCRIPTION
        Double-clicking a .ps1 opens a console that closes the instant the
        script ends, taking the error message with it. Pausing only when the
        session is interactive keeps Task Scheduler runs non-blocking.
    #>
    param([int]$Code = 0)

    if ($Host.Name -eq 'ConsoleHost' -and -not $env:STOCK_AI_NO_PAUSE) {
        Write-Host ''
        Read-Host 'Press Enter to close'
    }
    exit $Code
}
