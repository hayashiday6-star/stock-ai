<#
.SYNOPSIS
    Serve the dashboard to other devices on this network (phone, tablet).

.DESCRIPTION
    The normal launcher binds to localhost, which is why a phone cannot reach
    it. This binds to every interface and prints the address to type on the
    phone.

    Read this before using it: the dashboard has **no login**. Anyone on the
    same network can open it, browse the database, trigger data fetches, and
    send notifications - and if an AI key is configured, spend money through it.
    On a home Wi-Fi that is usually fine. On a cafe, hotel, office, or shared
    network it is not, which is why this is a separate script rather than a
    switch on the normal one.

.PARAMETER Port
    Port to listen on.

.EXAMPLE
    .\scripts\dashboard-lan.ps1
    .\scripts\dashboard-lan.ps1 -Port 8600
#>
[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8501
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Write-Section 'stock-ai dashboard (network access)'
Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

# --- find the address a phone should type ----------------------------------

function Get-LanAddress {
    <#
    .SYNOPSIS
        Return this machine's LAN IPv4 address, or $null if it cannot be found.
    #>
    $PSNativeCommandUseErrorActionPreference = $false
    try {
        $candidates = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object {
                $_.IPAddress -notlike '127.*' -and
                $_.IPAddress -notlike '169.254.*' -and
                $_.PrefixOrigin -ne 'WellKnown'
            }
        # Prefer a normal private range; a Hyper-V or WSL adapter is reachable
        # from this machine but not from a phone.
        $preferred = $candidates | Where-Object {
            $_.InterfaceAlias -notmatch 'vEthernet|WSL|Loopback|VirtualBox|VMware'
        }
        $chosen = if ($preferred) { $preferred } else { $candidates }
        return ($chosen | Select-Object -First 1).IPAddress
    }
    catch {
        return $null
    }
}

$address = Get-LanAddress

Write-Host ''
Write-Warn 'This dashboard has NO login.'
Write-Host '  Anyone on this network can open it, read the database, start data'
Write-Host '  fetches, and send notifications. Only use this on a network you'
Write-Host '  trust - your home Wi-Fi, not a cafe or hotel.'
Write-Host '  Close this window when you are finished.'
Write-Host ''

if ($address) {
    Write-Ok "On your phone, open:  http://${address}:$Port"
    Write-Host '  (the phone must be on the same Wi-Fi as this PC)'
}
else {
    Write-Warn 'Could not detect this PC address automatically.'
    Write-Host '  Run  ipconfig  in another window and look for "IPv4 Address"'
    Write-Host "  under your Wi-Fi adapter, then open  http://THAT-ADDRESS:$Port"
}
Write-Host ''
Write-Host 'Windows may ask to allow network access the first time. Allow it for'
Write-Host 'Private networks only.'
Write-Host ''
Write-Host 'To stop: press Ctrl+C in this window.'
Write-Host ''

uv run --no-sync streamlit run src/stock_ai/dashboard/app.py `
    --server.address 0.0.0.0 `
    --server.port $Port `
    --server.headless true

Exit-WithPause 0
