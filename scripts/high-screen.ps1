<#
.SYNOPSIS
    52週高値の効果を IS だけで推定し、封印済みの線に当てはめる。

.DESCRIPTION
    §0 の段2 です。同じ設計の文献値が無いので、見込みの下限を自分の IS から
    推定します。

    **封印しない線は、推定を1つも見ないうちに置いてあります。**

      IS（2021-09-30 まで）の20日窓の推定値が 1.26% を下回れば、封印しない。
      #3 はそこで閉じる。再測定はしない。

    1.26% ＝ OOS 1,197日での検出できる差 0.86% ＋ 費用 0.40%。

    **この線は厳しいです。** 候補文書が「現実的な水準」と書いた 1.0% より上に
    あります。段2 を採った代償で、選別に半分以上を使ったぶん判定に残る期数が
    減りました。通らない見込みが薄いと分かったうえで測ります。

    **OOS には触れません。** IS の終わりより後を渡すと終了コード2で止まります。
    渡し間違えたときに黙って OOS を混ぜないための関門で、**混ざったことは
    数字を見ても分かりません。**

    出る t は合否ではありません。IS は選別に使う期間で、判定は OOS で一度だけ
    行います。ここでの t は推定の精度を読むためだけのものです。

    全銘柄の日足を読むので数分かかります。

.PARAMETER MinTurnover
    流動性の下限（円）。既定は他の説と同じ1億円。

.EXAMPLE
    .\scripts\high-screen.ps1
#>
[CmdletBinding()]
param(
    [double]$MinTurnover = 100000000
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '52週高値：IS の推定と、封印するかどうか'
Write-Host '線は推定を見る前に置いてあります。測定後に変更しません。' -ForegroundColor Yellow
Write-Host 'IS だけを使います。OOS を渡すとそこで止まります。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai high-screen --min-turnover $MinTurnover
$code = $LASTEXITCODE

if ($code -eq 2) {
    Write-Host ''
    Write-Err 'OOS を混ぜようとしました。上の出力を貼ってください。'
}
elseif ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '表と最後の判定だけ貼ってください。当てはめはコードが済ませています。'
}

Exit-WithPause $code
