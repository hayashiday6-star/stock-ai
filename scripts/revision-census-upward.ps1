<#
.SYNOPSIS
    #5 上方修正の件数センサス。**リターンを1つも計算しません。**

.DESCRIPTION
    事前登録 `docs/PREREG_REVISION_JP.md` の §0 の表を埋めます。

    **判定を一度も使っていない説です。** 2026-09-03 に「予想修正は独立した開示
    として取得できない」と書いて閉じましたが、**それが事実として誤りでした**——
    一括ファイルで数えると 13,868 件あり、78% は決算と別の日に出ていました。

    **決算と同じ日に出た修正は外します。** #2・#3 と同じ日付集合を使わない
    ためで、**それが再開の前提そのものです。**

    出るもの。

      EarnForecastRevision の行   原本にある全部
      読めなかった内訳             予想・年度末・前回予想。**0 に落としません**
      上方 / 下方 / 幅が小さい     +5% 以上が上方（既存の定数を引いています）
      決算と同じ日 / 別の日        外す側と残る側
      流動性を通した後             売買代金 1億円／日
      IS / OOS                     §0 の期数は OOS のほうです

    **読めなかったものを 0 に落としません。** まとめて「修正なし」にすると、
    **読めていないことが「修正が無かった」に化けます。** 会社予想が1件も読めな
    かった場合は、**実際に載っていた鍵の名前を出します**——「無い」のか「名前が
    違う」のかを分けるためです。

    **リターンを1つも計算しないので、判定を消費しません。**

    **API を1回も叩きません。** 原本と日足を読むので数分かかります。

.EXAMPLE
    .\scripts\revision-census-upward.ps1
#>
[CmdletBinding()]
param(
    [string]$Dir = '',
    [int]$Limit = 0
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '上方修正の件数センサス（#5。リターンは計算しない）'
Write-Host 'リターンを1つも計算しないので、判定を消費しません。' -ForegroundColor DarkGray
Write-Host '決算と同じ日の修正は外します。#2・#3 と同じ日付集合を使わないためです。' -ForegroundColor Yellow
Write-Host ''

$arguments = @('run', 'stock-ai', 'revision-census')
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }
if ($Limit -gt 0) { $arguments += @('--limit', $Limit) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '2つの表と、警告の行を貼ってください。'
}

Exit-WithPause $code
