<#
.SYNOPSIS
    陽性対照 — 大きさの分かった効果を埋めて、判定まで通す。**説ではありません。**

.DESCRIPTION
    陰性対照は「何も無いときに合格を出さないか」だけを見ています。**在るときに
    合格を出せるか**は、これまで一度も試していませんでした。

    陰性対照と同じ乱数の signal に、要る情報比の 0・0.5・1・1.25・1.5 倍の効果を
    埋めて、本物と同じ管に通します。**線を越えた割合が予測どおりか**を見ます。
    m=1 は、合格が五分五分になる点です。

    **壊れていると言う条件は、測る前に決めてあります**（出力の赤い行）。
    予算には数えません。API を1回も叩きません。盤面を2つ組み、1,000回回すので、
    陰性対照の400回より長くかかります。

.PARAMETER Runs
    効果の大きさごとに回す回数。既定は 200。

.EXAMPLE
    .\scripts\positive-control.ps1
#>
[CmdletBinding()]
param(
    [int]$Runs = 200
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '陽性対照（説ではない。予算に数えない）'

uv run stock-ai positive-control --runs $Runs
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と、その下の行を貼ってください。'
}

Exit-WithPause $code
