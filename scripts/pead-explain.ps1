<#
.SYNOPSIS
    1銘柄のイベントを、使った日付と価格ごと並べる（手計算との突き合わせ用）。

.DESCRIPTION
    事前登録セクション9の最後の項目「既知の3銘柄について、驚きと R+60
    リターンを手計算と突き合わせた」のためのものです。

    集計を眺めても、集計の作り方が間違っている場合には気付けません。使った
    日付と価格を全部出すので、電卓で追えます。反応日は集計と同じ関数で
    決めているので、ここに出る日付が集計で使われた日付そのものです。

    価格は分割調整後です。証券会社の画面に出る実際の株価とは、分割をまたぐと
    一致しません。分割の無い期間を選ぶか、倍率を掛けて突き合わせてください。

.PARAMETER Symbol
    銘柄コード。

.PARAMETER Period
    is | oos | all。既定は is。

.EXAMPLE
    .\scripts\pead-explain.ps1 -Symbol 7203
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d{4}$')]
    [string]$Symbol,

    [ValidateSet('is', 'oos', 'all')]
    [string]$Period = 'is'
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section "PEAD: $Symbol の計算過程を並べる ($Period)"
Write-Host ''

uv run stock-ai pead-explain $Symbol --period $Period
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。'
}

Exit-WithPause $code
