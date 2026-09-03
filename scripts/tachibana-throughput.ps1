<#
.SYNOPSIS
    立花で全銘柄を回したときの所要時間と転送量を、数銘柄の実測から見積もる。

.DESCRIPTION
    `docs/TACHIBANA.md` の「まだ確認していないこと」に挙げたままの宿題です。

    `CLMMfdsGetMarketPriceHistory` には日付範囲の指定が無く、**毎回25年分の
    全期間が返ります。** 1銘柄あたりの転送量が大きいので、3,600銘柄を回すと
    何時間・何ギガになるのかが分かっていません。

    **J-Quants を解約してから「全銘柄は現実的でない」と分かるのが、いちばん
    困る形です。** 解約期限（2026-09-22）より前に実測します。

    数銘柄を実際に取得して、1銘柄あたりの秒数とバイト数を測り、全銘柄に
    線形で外挿します。価格そのものは保存しません。件数・バイト数・秒だけです。

.PARAMETER Symbols
    計測する銘柄。カンマ区切り。既定は大型・小型・ETF を混ぜた5銘柄。

.PARAMETER Universe
    外挿先の銘柄数。既定は本番DBに入っている 3607。

.PARAMETER Pause
    要求と要求の間隔（秒）。マニュアルにも公式サンプルにも上限の記載を
    見つけていないので、既定は 0 です。断られるようなら増やしてください。

.EXAMPLE
    .\scripts\tachibana-throughput.ps1
    .\scripts\tachibana-throughput.ps1 -Symbols 6501,7203 -Pause 1
#>
[CmdletBinding()]
param(
    [string]$Symbols = '6501,7203,6758,4847,1306',
    [int]$Universe = 3607,
    [double]$Pause = 0,
    [switch]$Demo,
    [switch]$Fresh,
    [string]$PrivateKey = 'tachibana_private.pem'
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

if (-not (Test-Path $PrivateKey)) {
    Write-Err "秘密鍵が見つかりません: $PrivateKey"
    Write-Host ''
    Write-Host '先に checks\立花API確認.bat を実行して鍵を作り、登録してください。'
    Exit-WithPause 1
}

Write-Section "立花: 全銘柄を回したときの見積もり（$Symbols で実測）"
Write-Host '価格そのものは保存しません。件数・バイト数・秒だけを測ります。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @(
    'run', 'python', 'tools\tachibana_probe.py', 'throughput',
    '--symbols', $Symbols,
    '--universe', $Universe,
    '--pause', $Pause,
    '--private', $PrivateKey
)
if ($Demo) { $arguments += '--demo' }
if ($Fresh) { $arguments += '--fresh' }

uv @arguments
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。銘柄コード以外は出ません。'
}

Exit-WithPause $code
