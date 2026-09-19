<#
.SYNOPSIS
    #15「窓は埋まる」の IS を測ります。**判定ではありません。**

.DESCRIPTION
    前日終値から当日始値が **3% 以上下に開いた**銘柄は、その後**20営業日**で
    等加重の宇宙を上回るか（事前登録 §1）。

    **入るのは D+1 の寄付き、降りるのは D+20 の終値。** 窓は寄付きで開くので、
    D の寄付きには間に合いません。費用は往復 0.4% を引きます。

    **2つを外します。** どちらも 3% の下窓にそう見えるものです。

      権利落ち   配当の落ちは機械的な値下がりで、埋まらない
      不連続     調整漏れの分割・併合（#6 で 8308 の 1:1000 を実際に見た）

    **外した件数は別々に出します。** まとめると、どちらで落ちたのか分からなく
    なります。**権利落ちで外した件数が 0 なら、原本が無いか、調整後の価格が
    既に配当を抜いているかのどちらか**です。

    **権利落ちは、その日より前に公表されたものだけで外します。** 権利落ち日は
    前もって分かる情報ですが、**後から出た訂正を使えば先読み**になります。

    **IS は 2013-01〜2017-12 です。** 2009年からではありません——`ExDate` が
    2012-12 からしか無く、**外せる期間と外せない期間を混ぜない**ためです
    （事前登録 §6）。**OOS（2018-01〜2026-08）は件数しか数えません。**

    **API を1回も叩きません。** 価格を2回なめるので時間がかかります。

.EXAMPLE
    .\scripts\gap-fill-power.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '#15 窓は埋まる — IS を測る（判定ではない）'
Write-Host '設計は事前登録が固定しています。ここでは何も選びません。' -ForegroundColor Yellow
Write-Host 'OOS は件数しか数えません。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai gap-fill-power
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と、その下の数行を貼ってください。'
}

Exit-WithPause $code
