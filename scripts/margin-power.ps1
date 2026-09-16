<#
.SYNOPSIS
    #8 増担保規制の散らばりを測る。**IS だけ。判定ではありません。**

.DESCRIPTION
    §0 の最後の空欄を埋めます——**1イベントあたりのSD**、**重なりの膨張**、
    そして**見込み**です。

    見るのは IS（原本が覆う期間の前半、2017-07-25 まで）だけで、**OOS には
    1日も触れません。** 判定は封印してから1回だけです。

    入り方は事前登録 §4 のとおり。**公表日 D の翌営業日の寄付き**で入り、
    §3 の窓（14営業日）ぶん持って終値で降ります。**D の引けには間に合いません**
    ——公表は 16:30 頃です。ショートなので符号を反転し、往復 0.4% を引きます。

    **測る前にコミットした線があります。** 「IS の1イベントあたり平均超過
    リターンの片側95%下限が **1.2%** を下回ったら封印しない」——往復費用の
    3倍です。下回ればそこで終わります。**線は動かしません。**

    費用を超えるだけの線にしないのは、#7 がその帯に入って落ちたからです。
    帯に入った判定は「無い」と「あると言い切れない」を分けられないまま、
    **判定を1回消費します。**

    **API を1回も叩きません。** 原本と日足を読むので数分かかります。

.PARAMETER Holding
    保有窓の上書き。**既定は §3 の式から出る窓です。上書きしたまま封印しません。**

.EXAMPLE
    .\scripts\margin-power.ps1
#>
[CmdletBinding()]
param(
    [string]$Dir = '',
    [string]$Rosters = '',
    [int]$Holding = 0
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '増担保の散らばり（#8 の §0。判定ではない）'
Write-Host 'OOS には1日も触れません。判定は封印してから1回だけです。' -ForegroundColor Yellow
Write-Host '線（片側95%下限で 1.2%）は測る前にコミット済みです。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'margin-power')
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }
if ($Rosters -ne '') { $arguments += @('--rosters', $Rosters) }
if ($Holding -gt 0) { $arguments += @('--holding', $Holding) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と、その下の2〜3行を貼ってください。'
}

Exit-WithPause $code
