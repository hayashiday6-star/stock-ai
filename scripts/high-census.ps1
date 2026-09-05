<#
.SYNOPSIS
    52週高値更新の件数と、その固まり具合を数える。**判定ではありません。**

.DESCRIPTION
    候補1（値幅制限）と2（売買停止明け）は 2026-09-05 に閉じました。
    事前に決めてあった順序で、3番に進みます。

    **ここで見るのは件数ではありません。固まり具合です。**

    52週高値の更新は上昇局面に集中します。同じ日に何十件も出れば、その日の
    銘柄は同じ市場の動きを共有しているので、独立な観測は件数よりずっと少なく
    なります。**件数だけ見て「1,000件ある」と読むと、検出できる差を実際より
    小さく見積もります。**

    見るところは3つです。

      1. 日数            件数ではなくこちらが独立観測です
      2. 上位1割の日の占有  均等に散っていれば 10%。50% を超えていれば、
                         半分の件が1割の日に乗っています
      3. 執行            翌日始値のギャップ。値幅制限はここで落ちました
                         （中央値 +5.37%）

    リターンは1つも計算しないので、判定を消費しません。

    全銘柄の日足を読むので数分かかります。

.PARAMETER MinTurnover
    流動性の下限（円）。既定は他の説と同じ1億円。

.EXAMPLE
    .\scripts\high-census.ps1
#>
[CmdletBinding()]
param(
    [double]$MinTurnover = 100000000
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '52週高値更新の件数と固まり具合（判定ではない）'
Write-Host 'リターンは計算しません。判定は消費しません。' -ForegroundColor Yellow
Write-Host '件数ではなく、日数と固まり具合を見ます。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai event-census --what high --min-turnover $MinTurnover
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '表だけ貼ってください。'
}

Exit-WithPause $code
