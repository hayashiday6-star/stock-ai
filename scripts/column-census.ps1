<#
.SYNOPSIS
    原本の列そのものを数えます。**何が在り、いつから埋まっているか。**

.DESCRIPTION
    **読み口が捨てている列は、読み口からは見えません。** #5 で `FS` の中の
    鍵をいくら並べても答えにならなかったのと同じ形なので、**読み口を通さずに
    原本の列を数えます。**

    そして**列が在ることと、値が埋まっていることは別です。** 候補6 の `IV`
    は 2008-05 の原本に列が在るのに、全行で空でした。**「在る」で先に進むと、
    IS が 359 日しかない設計になります。**

    `checks\この原本の列を全部見る.bat` との違いは、そこです。あちらは
    **1ファイルの列名**を出します。こちらは**全ファイルを通して、列ごとに
    どれだけ埋まっていて、いつから在るか**を出します。

    既定では、次の候補を組むのに要る4つを見ます。

    | エンドポイント | 何のため |
    |---|---|
    | `/fins/summary` | 高配当利回り。**`/equities/valuation` に利回りの列は無い** |
    | `/markets/short-ratio` | 空売り比率。**業種別です**——指数の1本ではない |
    | `/markets/margin-interest` | 信用買い残 |
    | `/equities/valuation` | 小型・低位・節目。**時価総額はここ** |

    **取りには行きません。** 原本を読むだけです。

.PARAMETER Endpoint
    1つだけ見るとき、その綴り（`/fins/summary` など）。

.PARAMETER ShowEmpty
    1行も埋まっていない列も出します。既定では伏せます。

.EXAMPLE
    .\scripts\column-census.ps1

.EXAMPLE
    .\scripts\column-census.ps1 -Endpoint /markets/short-ratio -ShowEmpty
#>
[CmdletBinding()]
param(
    [string]$Endpoint = '',
    [switch]$ShowEmpty
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '原本の列 — 何が在り、いつから埋まっているか'
Write-Host '取りには行きません。原本を読むだけです。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'column-census')
if ($Endpoint) { $arguments += @('--endpoint', $Endpoint) }
if ($ShowEmpty) { $arguments += '--show-empty' }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '列を数えられませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と警告だけで足ります。'
}

Exit-WithPause $code
