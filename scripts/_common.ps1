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

function Show-Version {
    <#
    .SYNOPSIS
        Print the commit this working copy is on, and warn if it is behind.

    .DESCRIPTION
        A fix that has been pushed but not pulled looks exactly like a fix that
        does not work: the same traceback, from the same line number, on code
        that was changed hours ago. Printing the commit makes "you are running
        an old version" a line at the top of the output instead of an invisible
        assumption, and comparing against origin says so outright.

        Everything here is best-effort. No git, no network, or a detached
        checkout just means less information, never a failed run.
    #>
    # Every git call below is allowed to fail. Shadowing this for the duration
    # of the function keeps a non-zero exit from throwing when the caller runs
    # under -ErrorActionPreference Stop with native error propagation enabled;
    # a version banner must never be the thing that stops the run.
    $PSNativeCommandUseErrorActionPreference = $false

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return }

    $commit = (git rev-parse --short HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $commit) { return }

    $when = (git log -1 --format=%cd --date=format:'%Y-%m-%d %H:%M' 2>$null)
    Write-Host "Version : $commit  ($when)"

    $branch = (git rev-parse --abbrev-ref HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $branch -or $branch -eq 'HEAD') { return }

    # A fetch can hang on a dead network; the timeout keeps a startup check
    # from becoming the reason the script never starts.
    $env:GIT_HTTP_LOW_SPEED_LIMIT = '1000'
    $env:GIT_HTTP_LOW_SPEED_TIME = '10'
    git fetch --quiet origin $branch 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host '          (could not reach origin - version may be stale)'
        return
    }

    $behind = (git rev-list --count "HEAD..origin/$branch" 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $behind) { return }

    if ([int]$behind -gt 0) {
        Write-Host ''
        Write-Warn "This copy is $behind commit(s) BEHIND origin/$branch."
        Write-Host '  Any fix pushed since then is not in the code about to run.'
        Write-Host '  Update first:'
        Write-Host "      git pull origin $branch"
        Write-Host ''
    }
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

        The output must not fall out of the function: PowerShell would fold
        native stdout into the return value, and the caller would then get a
        non-empty array that is truthy whether the command succeeded or not -
        so every failure would read as a pass.

        It is written back out with Write-Host rather than Out-Host. Under Task
        Scheduler there is no console attached, and this is the one context
        where the transcript *is* the record: a nightly log holding the header
        and the command line but not one line of what the command said is
        indistinguishable from a job that is still running. Write-Host is
        transcribed in every host, attached console or not.
    #>
    param(
        [Parameter(Mandatory)][string]$Title,
        [Parameter(Mandatory)][string[]]$Arguments
    )

    Write-Host ''
    Write-Host "--- $Title" -ForegroundColor Cyan
    Write-Host "    uv run stock-ai $($Arguments -join ' ')" -ForegroundColor DarkGray
    Write-Host ''

    & uv run stock-ai @Arguments 2>&1 | ForEach-Object { Write-Host $_ }
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
