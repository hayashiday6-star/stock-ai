<#
.SYNOPSIS
    連続する決算短信の会社予想を比べて、予想修正が何件検出できるかを数える。

.DESCRIPTION
    予想修正は別文書としては取れません。実測で fins/summary の開示種類の
    99.2%が決算短信で、予想修正は上位12種類に1件も現れませんでした。

    唯一の経路は、決算短信に毎回載る当期通期の予想です。前回の短信と
    突き合わせれば、修正開示を取らなくても修正が検出できます。

    **候補2（予想修正後のドリフト）が成立するかは、ここで出る件数が決めます。**
    年に数百件しか出ないなら、事前登録を書く前に設計を見直す必要があります。

    リターンは計算しません。件数だけです。

.PARAMETER Field
    比較する項目。revenue | operating_income | net_income | eps。既定は net_income。

.PARAMETER MinChange
    修正とみなす最小の変化幅。既定は 0.05（5%）。

.EXAMPLE
    .\scripts\revision-census.ps1
    .\scripts\revision-census.ps1 -Field operating_income
#>
[CmdletBinding()]
param(
    [ValidateSet('revenue', 'operating_income', 'net_income', 'eps')]
    [string]$Field = 'net_income',
    [double]$MinChange = 0.05
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section "予想修正の件数センサス ($Field)"
Write-Host '予想フィールドは後から足した列です。財務を取り直していないDBでは' -ForegroundColor DarkGray
Write-Host '「予想が入っておらず比較できず」に全件落ちます。その場合は' -ForegroundColor DarkGray
Write-Host '11-開示時刻の取り込み.bat を先に実行してください。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai revision-census --field $Field --min-change $MinChange
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。件数だけで、銘柄名は出ません。'
}

Exit-WithPause $code
