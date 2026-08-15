<#
.SYNOPSIS
    Work out why EDINET refuses the API key, in one run.

.DESCRIPTION
    EDINET answers a refused request with "Access denied due to invalid
    subscription key" whether the key is wrong or merely somewhere the gateway
    does not read. The two are indistinguishable from a single failure, which is
    what makes this particular 401 so hard to chase.

    So this does not ask again in the same way. It sends the key four different
    ways in one run - as a query parameter (what a browser sends), in each
    header spelling, and all three together (what the client does) - and reports
    each result separately. Whichever way is accepted names the fix.

    Pasting the URL into a browser tests only the query-parameter form. A key
    that works there and fails here is a real and specific finding, not a
    contradiction.

.PARAMETER Date
    Day to request, YYYY-MM-DD. Defaults to today. Any date works; a weekday
    returns more filings, but an accepted request on a quiet day still counts as
    accepted.

.EXAMPLE
    .\scripts\edinet-check.ps1
    .\scripts\edinet-check.ps1 -Date 2026-08-07
#>
[CmdletBinding()]
param(
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$Date
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Write-Section 'EDINET key check'
Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Host ''
Write-Host 'Sending the key four different ways. The key is never printed -'
Write-Host 'only its length and a hash fingerprint, which are safe to paste.'

$stepArgs = @('edinet-check')
if ($Date) { $stepArgs += @('--date', $Date) }

$ok = Invoke-Step 'EDINET key placements' $stepArgs

Write-Host ''
if ($ok) {
    Write-Host 'Read the verdict under the table - it names the next step.'
}
else {
    Write-Warn 'The check itself did not finish. That is a different problem from a rejected key.'
    Write-Host 'If EDINET_API_KEY is not set yet, run: .\scripts\set-key.ps1 EDINET_API_KEY'
}

Exit-WithPause ([int](-not $ok))
