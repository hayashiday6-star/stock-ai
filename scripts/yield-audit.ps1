<#
.SYNOPSIS
    高すぎる配当利回りの中身を見ます。**取りには行きません。**

.DESCRIPTION
    利回りが、日本の上場企業がまず払わない高さで出る銘柄月があります。
    **候補14 は分位に並べるだけなので、利回りの値そのものは SD に入りません。**
    効くのは**入る分位を間違えること**で、その大きさは**その月の分位に占める
    割合**で決まります。

    **件数はここに書きません。** その回の出力が数えるものを先に書くと、
    **次に変わったときだけ古いまま残ります**（`docs/POSTMORTEMS.md`）。

    出すもの:

    - 開示から組み替えまでの**分割比**（その銘柄の調整の倍率から引く）
    - 分割か**併合**をまたいだ銘柄月の数と、扱い方ごとの代償
      （(a) 割り戻すと低すぎる側に出る形、(b) 持ち越さないと外れる割合）
    - 分割をまたいでいない行の仕分け（開示前1年の分割、開示した月の株価）

    **原因を推測で決めません。割合で見ます。**

.PARAMETER Limit
    出す行数（利回りの大きい順）。既定は 20。

.EXAMPLE
    .\scripts\yield-audit.ps1

.EXAMPLE
    .\scripts\yield-audit.ps1 -Limit 60
#>
[CmdletBinding()]
param(
    [int]$Limit = 20
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '高すぎる配当利回り — 中身を見る'
Write-Host '取りには行きません。原本と価格を読むだけです。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai yield-audit --limit $Limit
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '中身を出せませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と警告だけで足ります。'
}

Exit-WithPause $code
