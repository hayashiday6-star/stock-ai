<#
.SYNOPSIS
    短期リバーサルの母集団を、リターンを計算せずに数える。

.DESCRIPTION
    事前登録を書く**前**に、測れるかどうかだけを確かめます。決算ドリフトの
    2本は封印してから検出力や母集団の薄さが分かったので、順序を逆にします。

    決算ドリフトと違い、リバーサルはイベント駆動ではありません。全銘柄が
    毎営業日「直近5日でどれだけ下げたか」を持つので、観測は銘柄×営業日に
    なります。そのぶん数えるものが変わります。

    - **1営業日あたり何銘柄が条件を通るか。** 5分位に切るには1日5銘柄が要り、
      足りない日は差を取れません
    - 5日リターンの分布。分位の境目が潰れていないか
    - **分位ごとの売買代金の中央値。** リバーサルは小型・低流動性で強いことが
      知られています。端の分位だけ売買代金が小さければ、フィルタを通った後
      でも売買しにくい銘柄を並べていることになります

    リターンは1つも計算しません。全銘柄×全営業日を走査するので数分かかります。

.PARAMETER Period
    is | oos | all。既定は all（センサスは判定に使わないため）。

.PARAMETER Lookback
    「大きく下げた」を測る営業日数。既定は 5。

.PARAMETER Holding
    保有営業日数。既定は 20。

.EXAMPLE
    .\scripts\reversal-census.ps1
    .\scripts\reversal-census.ps1 -Lookback 20 -Holding 5
#>
[CmdletBinding()]
param(
    [ValidateSet('is', 'oos', 'all')]
    [string]$Period = 'all',
    [int]$Lookback = 5,
    [int]$Holding = 20,
    [double]$MinTurnover = 0
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section "短期リバーサルの件数センサス（$Lookback 日下落・$Holding 日保有）"
Write-Host '全銘柄×全営業日を走査します。数分かかります。' -ForegroundColor DarkGray
Write-Host 'リターンは計算しません。件数と分布だけです。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @(
    'run', 'stock-ai', 'reversal-census',
    '--period', $Period,
    '--lookback', $Lookback,
    '--holding', $Holding
)
if ($MinTurnover -gt 0) { $arguments += @('--min-turnover', $MinTurnover) }

uv @arguments
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。件数と分布だけで、銘柄名は出ません。'
}

Exit-WithPause $code
