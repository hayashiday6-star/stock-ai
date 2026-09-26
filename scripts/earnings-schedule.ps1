<#
.SYNOPSIS
    保存した決算発表予定日の原本が、予定の履歴になっているかを数えます。**取りには行きません。**

.DESCRIPTION
    候補17（決算発表日を避ける）の材料が在るかを数えます。**効果は何も計算しません。**

    「API が直近しか返さない」ことと、「手元に歴史が無い」ことは別です。
    `PubDate`（予定が公表された日）と `SchDate`（予定日）が入っていれば、
    保存した原本そのものが「その日に何が予定されていたか」になります。

    **件数はここに書きません。** その回の出力が数えます（`docs/POSTMORTEMS.md`）。

.EXAMPLE
    .\scripts\earnings-schedule.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '決算発表予定日 — 履歴になっているか'
Write-Host '取りには行きません。原本を読むだけです。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai earnings-schedule
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '数えられませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と警告だけで足ります。'
}

Exit-WithPause $code
