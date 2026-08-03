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

function Set-EnvKey {
    <#
    .SYNOPSIS
        Write $Name=$Value into .env, replacing any existing assignment.

    .DESCRIPTION
        Editing .env by hand is where keys go wrong: a stray quote, a trailing
        space, a second assignment further down the file that quietly wins, or a
        BOM prepended by Set-Content -Encoding UTF8. This does the write once and
        correctly.

        The value is never printed, returned, or passed on a command line. Read
        it with Read-EnvKeyValue so it does not land in PSReadLine history
        either - a pasted secret in a shell command persists in a plain-text
        history file long after the window is closed.
    #>
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Value
    )

    if (-not (Test-Path '.env')) {
        if (-not (Test-Path '.env.example')) {
            Write-Err '.env.example is missing; cannot create .env.'
            return $false
        }
        Copy-Item '.env.example' '.env'
        Write-Ok 'Created .env from .env.example.'
    }

    # Quotes and whitespace survive a paste far more often than they are meant
    # to, and an API key with a stray quote fails as though it were wrong.
    $clean = $Value.Trim().Trim('"').Trim("'").Trim()
    if ([string]::IsNullOrWhiteSpace($clean)) {
        Write-Err "No value given for $Name - .env left unchanged."
        return $false
    }

    $pattern = "^\s*$([regex]::Escape($Name))\s*="
    $lines = @(Get-Content '.env' -Encoding UTF8)
    $replaced = $false
    $result = foreach ($line in $lines) {
        if ($line -match $pattern -and -not $line.Trim().StartsWith('#')) {
            $replaced = $true
            "$Name=$clean"
        }
        else { $line }
    }
    if (-not $replaced) { $result = @($result) + "$Name=$clean" }

    # WriteAllLines writes UTF-8 *without* a BOM. Set-Content -Encoding UTF8 on
    # Windows PowerShell 5.1 adds one, and a BOM on the first line of .env is
    # read as part of the first variable's name.
    $path = (Resolve-Path '.env').Path
    [System.IO.File]::WriteAllLines($path, [string[]]@($result))

    Write-Ok "$Name written to .env ($($clean.Length) characters)."
    return $true
}

function Read-EnvKeyValue {
    <#
    .SYNOPSIS
        Prompt for a secret without echoing it or recording it in history.
    #>
    param([Parameter(Mandatory)][string]$Name)

    $secure = Read-Host "Paste $Name (input is hidden)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
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
        Running a .ps1 by double-click opens a console that closes the instant
        the script ends, taking the error message with it. Pausing only when the
        session is interactive keeps Task Scheduler runs non-blocking.

        Set STOCK_AI_NO_PAUSE to skip it. The .bat launchers do exactly that,
        because they pause themselves and asking twice is just annoying.
    #>
    param([int]$Code = 0)

    if ($Host.Name -eq 'ConsoleHost' -and -not $env:STOCK_AI_NO_PAUSE) {
        Write-Host ''
        Read-Host 'Press Enter to close'
    }
    exit $Code
}
