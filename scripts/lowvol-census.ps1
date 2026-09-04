<#
.SYNOPSIS
    低ボラティリティの母集団を、リターンを計算せずに数える。

.DESCRIPTION
    封印の前に、測れるかどうかだけを先に確かめます。#2 と #3 は封印してから
    検出力が足りないと分かりました。#6 で順序を逆にして、それは正しかった。
    同じ手順を踏みます。

    **リターンは1つも計算しません。** 数えるのは件数・月数・ボラティリティの
    分布・分位ごとの売買代金と業種だけです。

    この出力で、封印前に決めるべきことが3つ片付きます。

      1. 測定窓を 60/120/250 のどれにするか
         窓が長いほど履歴を要求するので、通る銘柄が減ります。その減り方で
         決めます。リターンを見ていないので事後的な選択にはなりません。

      2. 分位が規模に偏っていないか
         #6 では「小型に寄る」という事前の見立てが外れて平らでした。
         測ってから言います。

      3. 分位が業種に偏っていないか
         低ボラは内需・ディフェンシブに集中するという指摘があります。偏って
         いれば、測っているものの一部は業種のリターン差になるので、業種中立版を
         副次として登録します。

    月次リバランスなので、観測は銘柄×営業日ではなく銘柄×月です。#6 を苦しめた
    重なりの膨張（2.95倍）が起きないのはこのためです。

    全銘柄・全期間を走査するので数分かかります。

.PARAMETER Period
    is | oos | all。既定は all（センサスは判定に使いません）。

.PARAMETER Windows
    測定窓（営業日）。カンマ区切り。既定は 60,120,250。

.EXAMPLE
    .\scripts\lowvol-census.ps1
#>
[CmdletBinding()]
param(
    [ValidateSet('is', 'oos', 'all')]
    [string]$Period = 'all',
    [string]$Windows = '60,120,250'
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '低ボラティリティ：封印前の件数センサス'
Write-Host 'リターンは1つも計算しません。件数と分布だけです。' -ForegroundColor DarkGray
Write-Host '測定窓・規模の偏り・業種の偏りを、この出力で決めます。' -ForegroundColor DarkGray
Write-Host '数分かかります。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai lowvol-census --period $Period --windows $Windows
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。封印前に決める4点をまとめます。'
}

Exit-WithPause $code
