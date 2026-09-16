<#
.SYNOPSIS
    #8 増担保規制の件数センサス。**リターンを1つも計算しません。**

.DESCRIPTION
    事前登録 `docs/PREREG_MARGIN_JP.md` の §2 の表と、§3 の保有窓を埋めます。

    **保有窓はここで機械的に決まります。** 「規制が解けるまでの営業日数の
    中央値、上限20営業日」という §3 の一行が、そのまま関数になっています。
    **中央値を見てから窓を選び直しません。**

    出るもの。

      発動イベント数       原本の全期間
      上位1割の日の占有    同じ日に固まると独立な観測が減ります
      貸借銘柄に絞った後   空売りできない銘柄でショートを検証しません
      流動性を通した後     売買代金 1億円／日
      解除までの営業日     **窓を決めるのはこれです**

    **リターンを1つも計算しないので、判定を消費しません。**

    貸借区分は**その日の値**で引きます。「いま貸借銘柄か」で引くと、
    2026-09-08 に踏んだ形（市場区分を最後に見えた姿で引いた）と同じになります。
    **名簿が届いていない日は「貸借でない」ではなく「分からない」**として数え、
    件数を出します。

    解除日が分からないイベント（銘柄が原本から消えた場合）も数えます。
    **分からないのであって、長いのでも短いのでもありません。** 中央値は解けた
    ものだけで出すので、打ち切りが多ければ**短い側に寄ります。** その割合を
    警告に出します。

    **API を1回も叩きません。** 原本と全銘柄の日足を読むので数分かかります。

.PARAMETER Rosters
    貸借区分を引く名簿。既定は営業日ごとの名簿。

.EXAMPLE
    .\scripts\margin-census.ps1
#>
[CmdletBinding()]
param(
    [string]$Dir = '',
    [string]$Rosters = '',
    [int]$Limit = 0
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '増担保の件数センサス（#8。リターンは計算しない）'
Write-Host 'リターンを1つも計算しないので、判定を消費しません。' -ForegroundColor DarkGray
Write-Host '保有窓は式から出ます。中央値を見てから選び直しません。' -ForegroundColor Yellow
Write-Host ''

$arguments = @('run', 'stock-ai', 'margin-census')
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }
if ($Rosters -ne '') { $arguments += @('--rosters', $Rosters) }
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
