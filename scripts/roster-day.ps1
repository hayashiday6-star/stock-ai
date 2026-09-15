<#
.SYNOPSIS
    名簿の無い日が、なぜ無いのかを名指しする。

.DESCRIPTION
    **「原本に行が無い」と「行はあったが絞り込みが全部落とした」を分けます。**
    どちらも結果は「名簿の無い日」ですが、**直す場所が違います。**

      原本に行が無い          J-Quants が出していない。**こちらでは直せません**
      絞り込みが全部落とした  **こちら側の問題です**

    2026-09-15 に、立会日なのに名簿の無い日が2日見つかりました（2008-12-30、
    2009-01-05）。**範囲内の半日立会2日とぴったり同じ**です。まとめの件数だけ
    ではどちらか決まらず、1日ぶんを名指しで見るしかありませんでした。

    既定はその2日です。ほかの日を見たいときは引数に渡します。

    **API を1回も叩きません。** 名簿の原本を1周読むので数分かかります。

.EXAMPLE
    .\scripts\roster-day.ps1
    .\scripts\roster-day.ps1 2015-01-05
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Dates,
    [string]$Dir = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

if (-not $Dates -or $Dates.Count -eq 0) {
    # 既定は、立会日なのに名簿が無かった2日。**どちらも半日立会である。**
    $Dates = @('2008-12-30', '2009-01-05')
}

Write-Section '名簿の無い日を調べる'
Write-Host '原本に行が無いのか、絞り込みが全部落としたのかを分けます。' -ForegroundColor DarkGray
Write-Host ('見る日: {0}' -f ($Dates -join '  ')) -ForegroundColor DarkGray
Write-Host '名簿の原本を1周読むので数分かかります。API は叩きません。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'jquants-roster-day') + $Dates
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と、その下の判定の行を貼ってください。'
}

Exit-WithPause $code
