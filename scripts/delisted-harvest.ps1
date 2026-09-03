<#
.SYNOPSIS
    上場していた会社の名簿を、日付ごとに保存する（解約前にしかできない）。

.DESCRIPTION
    これまでの事前登録はすべて「上場廃止銘柄が入っていない」と但し書きを
    付けてきました。下げた銘柄を買うリバーサルでは、これは但し書きではなく
    主要な脅威です。下げて消えた会社こそ、いまの一覧から抜けている銘柄
    そのものだからです。

    checks\生存バイアス確認.bat で、これは直せると分かりました。名簿同士の
    差＝廃止銘柄は年 49〜106 件あり、試した5件すべてで株価も取れました。
    このスクリプトは、その結果が意味する作業を実行します。

    やることは2つです。

      1. equities/master を月1回ずつさかのぼり、その日の名簿を1ファイルずつ
         CSV に書く。
      2. 名簿にあって DB に株価が無い銘柄の株価を取り込む。

    保存するのは和集合ではなく日付ごとの名簿です。和集合しか持たないと、
    2023年に上場した会社を2021年の分位に入れられてしまいます。生存バイアスを
    直したつもりで先読みを持ち込むことになり、直す前より悪くなります。

    取得元は J-Quants に固定します（JP_PRICE_SOURCE は見ません）。立花の
    マスタは現存銘柄のみで、廃止銘柄は返らないためです。

    期限があります。J-Quants の解約予定は 2026-09-22 で、それ以降はこの
    名簿も廃止銘柄の株価も取り直せません。5年ローリング窓の外（2021-09 より
    前）は、いま実行しても取れません。

    全期間で1時間以上かかることがあります。中断しても安全です。すでに
    ファイルがある日付は取りに行かず、株価も取れている銘柄は飛ばします。

.PARAMETER Start
    最初の名簿の日付。既定 2021-09-01（5年窓の境界）。

.PARAMETER End
    最後の名簿の日付。既定は今日。

.PARAMETER StepDays
    名簿の間隔。既定 30 日。

.PARAMETER Limit
    株価を取る銘柄数の上限。まず少数で試すときに使います。

.PARAMETER NoPrices
    名簿だけを集め、株価は取りません。

.EXAMPLE
    .\scripts\delisted-harvest.ps1 -Limit 20
    .\scripts\delisted-harvest.ps1
#>
[CmdletBinding()]
param(
    [string]$Start = '2021-09-01',
    [string]$End = '',
    [int]$StepDays = 30,
    [int]$Limit = 0,
    [switch]$NoPrices
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

if (-not (Test-EnvKeySet 'JQUANTS_API_KEY')) {
    Write-Section '廃止銘柄の取り込み'
    Write-Err '.env に JQUANTS_API_KEY がありません。'
    Write-Host ''
    Write-Host 'APIキー設定.bat で設定してから実行してください。'
    Exit-WithPause 1
}

Write-Section '上場していた会社の名簿を、日付ごとに保存する'
Write-Host '保存するのは和集合ではなく日付ごとの名簿です。' -ForegroundColor DarkGray
Write-Host '和集合だと、まだ上場していない銘柄を過去の分位に入れてしまいます。' -ForegroundColor DarkGray
Write-Host '取得元は J-Quants 固定。立花のマスタには廃止銘柄が無いためです。' -ForegroundColor DarkGray
Write-Host '解約予定 2026-09-22 を過ぎると、これは二度と取れません。' -ForegroundColor Yellow
Write-Host '1時間以上かかることがあります。中断しても安全です。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @(
    'run', 'stock-ai', 'delisted-harvest',
    '--start', $Start,
    '--step-days', "$StepDays"
)
if ($End) { $arguments += @('--end', $End) }
if ($Limit -gt 0) { $arguments += @('--limit', "$Limit") }
if ($NoPrices) { $arguments += '--no-prices' }

uv @arguments
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '終わりました。名簿は data\universe_snapshots\ に日付ごとに入っています。'
    Write-Host 'これは取り直せないデータです。git にコミットして残してください。' -ForegroundColor Yellow
}

Exit-WithPause $code
