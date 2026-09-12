<#
.SYNOPSIS
    予想修正が、決算発表と別の日に出ているかを数える。取得はしない。

.DESCRIPTION
    **説#5 を閉じた理由そのものを測ります。** 記録にはこうあります。

        予想修正は**独立した開示として取得できない**。イベント日が決算発表日
        と重なる。「決算とは独立」という前提が崩れる。

    公式の書類種別一覧には `EarnForecastRevision`（業績予想の修正）が**独立
    した種別として載っています。** ただし、載っていることと、別の日に出る
    ことは別です——**後者を数えます。**

      決算と同じ日  → 独立イベントにならない（閉じた理由のとおり）
      単独の日      → 独立イベントになる（閉じた理由が誤り）

    保存済みの `fins/summary` を読むだけです。取得はしません。

.EXAMPLE
    .\scripts\revision-independence.ps1
#>
[CmdletBinding()]
param(
    [string]$Dir = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '予想修正は独立した開示か（取得はしない）'
Write-Host '説#5 を閉じた理由が、いまも事実として正しいかを見ます。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'jquants-revision-census')
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '最後の2行をそのまま貼ってください。'
}

Exit-WithPause $code
