<#
.SYNOPSIS
    名簿に在って、価格が1本も無い銘柄を数えます。**穴は黙って観測を消します。**

.DESCRIPTION
    イベント型の陰性対照で、引いた 800,000 件のうち **16,677 件（2.1%）が
    「価格が1本も無い銘柄」に当たっていました**（2026-09-18、400回）。

    **乱数だからどうでもいい、という話ではありません。** 引いているのは
    `list_securities` が返す銘柄で、**説の側の候補もそこから出ます。**
    #5 の上方修正も #8 の増担保も、公表した会社の証券コードで価格を引きます。
    **そこに足が1本も無ければ、そのイベントは判定に入らないまま消えます。**

    そして**消えたことは、リターンの側からは見えません。** 平均も `t` も、
    残ったものだけで計算されます。

    足が在っても短すぎるものは別に数えます。20営業日の窓を開けるには最低
    22 本要るので、それ未満は**在っても使えません。**

    **数えるだけで、取り込みはしません。** 穴の理由は1つではない（上場前・
    プランの範囲外・取り込み失敗）ので、見てから決めます。

    **API を1回も叩きません。**

.PARAMETER Market
    数える市場。既定は `JP`。

.PARAMETER Show
    一覧に出す件数。既定は 20。

.EXAMPLE
    .\scripts\price-coverage.ps1
#>
[CmdletBinding()]
param(
    [string]$Market = '',
    [int]$Show = 0
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '名簿に在って、価格が無い銘柄'
Write-Host '説の候補もこの名簿から出ます。足が無ければ、そのイベントは黙って消えます。' -ForegroundColor Yellow
Write-Host ''

$arguments = @('run', 'stock-ai', 'price-coverage')
if ($Market) { $arguments += @('--market', $Market) }
if ($Show -gt 0) { $arguments += @('--show', $Show) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と警告だけ貼ってください。一覧は長ければ先頭だけで足ります。'
}

Exit-WithPause $code
