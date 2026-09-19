<#
.SYNOPSIS
    #12「トレンドは友」の IS を測ります。**判定ではありません。**

.DESCRIPTION
    上がっている銘柄は上がり続けるのか。過去12ヶ月（直近1ヶ月を除く）で
    並べて5分位に分け、上位から下位を引きます。

    **設計は事前登録 §3 で固定してあります**——形成12ヶ月・直近1ヶ月スキップ・
    保有1ヶ月・5分位・等加重・月次。**動かす引数を置いていません。**
    動かせると、試して良いものを選ぶことになり、**選んだこと自体が多重検定に
    なります。** #10 はそれで IS の `t` が 2.8倍振れ、封印できませんでした。

    出すのは4つ。

      1期あたりのSD    検出できる差を決める
      入れ替わり率     費用を決める。**#9 の値は写しません。測ります**
      効果の推定       封印するかどうかを決める
      裾               急反転でどれだけ持っていかれるか

    **生の差と α（β を引いた）の両方を出します。** ロング・ショートでも β は
    0 ではなく、散らばりが桁で変わりえます。#9 は §5 に「α も併記する」と
    書きながら §0 を生の差だけで埋めました。

    **IS は 2009-01〜2017-12 で、OOS（2018-01〜2026-08）には1日も触れません。**

    **API を1回も叩きません。**

.PARAMETER IsEnd
    IS の最終日。既定は 2017-12-31。**事前登録 §6 の値です。**

.PARAMETER OosPeriods
    判定に使える月数。既定 104（OOS の月数）。**全期間ではありません。**

.EXAMPLE
    .\scripts\momentum-power.ps1
#>
[CmdletBinding()]
param(
    [string]$IsEnd = '',
    [int]$OosPeriods = 0
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '#12 トレンドは友 — IS の散らばりを測る（判定ではない）'
Write-Host '設計は事前登録が固定しています。ここでは何も選びません。' -ForegroundColor Yellow
Write-Host 'OOS には1日も触れません。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'momentum-power')
if ($IsEnd) { $arguments += @('--is-end', $IsEnd) }
if ($OosPeriods -gt 0) { $arguments += @('--oos-periods', $OosPeriods) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '2つの表と、その下の数行を貼ってください。'
}

Exit-WithPause $code
