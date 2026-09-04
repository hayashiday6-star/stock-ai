<#
.SYNOPSIS
    財務（会社予想・開示時刻を含む）を一括ファイルで取り切る。

.DESCRIPTION
    銘柄ごとに叩く経路は 3,700 リクエストで、84件で 429 に当たって止まりました。
    契約は Light（1分あたり 60回）なので、既定の 0.5 秒＝120回／分は上限の
    ちょうど2倍でした。**遅くするのではなく、経路を変えます。**

    一括ファイルは **83本・約9MB**で、2021-09 〜 2026-09 の全銘柄が入ります。
    1本につき2リクエスト（URL取得と本体）なので、3,700 が 166 になります。

    会社予想（FSales/FOP/FNP/FEPS）と開示時刻（DiscTime）はこの行に乗って
    います。9/22 を過ぎると、どこからも増えません。

    **何度実行しても安全です。** 会計期をキーに上書きし、既にある値を空で
    潰しません。途中で止まっても、もう一度実行すれば続きから入ります。

    署名付きURLの寿命は5分なので、1本ずつ「取ってすぐ落とす」形で進みます。

.PARAMETER Since
    YYYY-MM。これより古いファイルを飛ばします。追加分だけ入れたいとき。

.PARAMETER Limit
    ファイル数の上限。まず数本で試すときに使います。

.PARAMETER Endpoint
    既定は /fins/summary。株価は /equities/bars/daily。

.EXAMPLE
    .\scripts\jquants-bulk-fetch.ps1 -Limit 3
    .\scripts\jquants-bulk-fetch.ps1
    .\scripts\jquants-bulk-fetch.ps1 -Since 2026-01
#>
[CmdletBinding()]
param(
    [string]$Since = '',
    [int]$Limit = 0,
    [string]$Endpoint = '/fins/summary'
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

if (-not (Test-EnvKeySet 'JQUANTS_API_KEY')) {
    Write-Section '財務を一括で取り切る'
    Write-Err '.env に JQUANTS_API_KEY がありません。'
    Write-Host ''
    Write-Host 'APIキー設定.bat で設定してから実行してください。'
    Exit-WithPause 1
}

Write-Section "財務を一括ファイルで取り切る ($Endpoint)"
Write-Host '解約後はどこからも増えません。取る判断は 9/22 まで。' -ForegroundColor Yellow
Write-Host '83本・約9MB。銘柄ごとに3,700回叩く代わりです。' -ForegroundColor DarkGray
Write-Host '何度実行しても安全です。途中で止まっても続きから入ります。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'jquants-bulk-fetch', '--endpoint', $Endpoint)
if ($Since -ne '') { $arguments += @('--since', $Since) }
if ($Limit -gt 0) { $arguments += @('--limit', "$Limit") }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
Write-Section 'いま手元にある量'
uv run stock-ai jquants-inventory

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。'
}

Exit-WithPause $code
