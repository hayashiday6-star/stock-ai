<#
.SYNOPSIS
    #16 が権利落ちで外した急落の中身を見ます。**効果は1つも出しません。**

.DESCRIPTION
    `#16` は急落 819 件を権利落ちで外し、警告が「**中身を見ること**」と
    言いました。**言うだけで、見る道具がありませんでした**（2026-09-20）。

    **説明は3つあり、見分けはつきます。**

      `ExDate` の読み違い   権利落ち日に値が下がっていない
      特別配当              利回りが大きい
      窓が広いだけ          利回りは1〜2%。**配当を戻しても −20% を超える**

    **3つ目なら、外すべきでない急落を外しています。**

    **1つ目の表は 819 件だけでなく、読めた権利落ち全件**を並べます。
    `#15`（窓は埋まるか）も同じ列で外しているので、そちらも一緒に確かめ
    られます。**段差が 0 日目に無ければ、そこで止まります**——外す日が
    違うなら、その下を読む意味がありません。

    **3つ目の表は、外していないほう**です。除外しているのは急落の6営業日
    だけで、**保有する5営業日の中の権利落ちは見ていません。** ショートでは
    配当は払う側なので、**払っていない価格で測ると取り高が高く出ます。**

    **リターンを1つも計算しません。** 判定を先食いしないためです。

    **API を1回も叩きません。** 価格を何度もなめるので時間がかかります。

.EXAMPLE
    .\scripts\ex-date-audit.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '権利落ちの日は合っているか（#16 が外した 819 件）'
Write-Host 'リターンは1つも出しません。データを見るだけです。' -ForegroundColor Yellow
Write-Host ''

uv run stock-ai ex-date-audit
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '途中で止まりました。**止まったこと自体が結果です。** 上の出力を貼ってください。'
}
else {
    Write-Ok '表と、その下の数行を貼ってください。'
}

Exit-WithPause $code
