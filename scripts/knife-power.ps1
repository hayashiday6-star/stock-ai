<#
.SYNOPSIS
    #16「落ちるナイフをつかむな」の IS を測ります。**判定ではありません。**

.DESCRIPTION
    **5営業日で 20% 以上下げた**銘柄は、その後の **5営業日**でも下げ続けるか
    （事前登録 §1）。**測るのはショートの取り高です。**

    **向きは格言から取りました。#15 の結果からではありません。**
    「つかむな」は買うと損をすると言っています。出どころは
    `docs/CANDIDATES.md`（2026-09-19）で、**#15 を測る前に書かれたもの**です。

    **窓が 5営業日なのは、格言が急落直後の数日の話だから**です。検出力のため
    ではありません（事前登録 §0）。

    **入るのは D+1 の寄付き、降りるのは D+5 の終値。** 費用は往復 0.4% を
    引きます。

    **2つを外します。**

      権利落ち   **ここでは 0 に近いはず**です。配当で 20% は動きません。
                 0 でなければ別のものを拾っているので、件数を出します。
      不連続     調整漏れの分割・併合。**この事象の下見が 273%/日 と出た
                 のが、まさにこれ**でした（#6 の 8308 は 1:1000 の併合）。

    **IS は 2013-01〜2017-12 です。** `ExDate` が 2012-12 からしか無く、
    **外せる期間と外せない期間を混ぜない**ためです（事前登録 §6）。
    **OOS（2018-01〜2026-08）は件数しか数えません。**

    **線 3.30 は窓20営業日で測った値です。** §0 を通った場合だけ、
    **5営業日の対照を回してから封印します**（事前登録 §10）。

    **API を1回も叩きません。** 価格を2回なめるので時間がかかります。

.EXAMPLE
    .\scripts\knife-power.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '#16 落ちるナイフ — IS を測る（判定ではない）'
Write-Host '設計は事前登録が固定しています。ここでは何も選びません。' -ForegroundColor Yellow
Write-Host '測るのはショートの取り高です。OOS は件数しか数えません。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai knife-power
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と、その下の数行を貼ってください。'
}

Exit-WithPause $code
