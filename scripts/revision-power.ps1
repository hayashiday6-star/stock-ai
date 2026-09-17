<#
.SYNOPSIS
    #5 上方修正の散らばりを測る。**IS だけ。判定ではありません。**

.DESCRIPTION
    §0 の最後の空欄を埋めます——**1イベントあたりのSD**、**重なりの膨張**、
    そして**見込み**です。

    見るのは IS（原本が覆う期間の前半、2017-08-24 まで）だけで、**OOS には
    1日も触れません。** 判定は封印してから1回だけです。

    入り方は事前登録 §4 のとおり。**開示日の翌営業日の寄付き**で入り、
    **20営業日**持って終値で降ります。**開示時刻に賭けません**——引け後が多い
    ですが、多いことと全部であることは別です。ロングなので符号は反転せず、
    往復 0.4% を引きます。

    窓の 20営業日は #3・#6 と揃えたものです。**#8 のように機構から窓が出る説
    ではない**ので、既存と揃えるのがいちばん自由度の少ない決め方でした。
    **リターンを見て決めていません。**

    **測る前にコミットした線があります。** 「IS の1イベントあたり平均超過
    リターンの片側95%下限が **1.2%** を下回ったら封印しない」——往復費用の
    3倍です。下回ればそこで終わります。**線は動かしません。**

    上位1%を除いたSDも併記します。**外れ値で膨らんでいないかを見るため**です。

    **API を1回も叩きません。** 原本と日足を読むので数分かかります。

.PARAMETER Holding
    保有窓。**既定の 20 は事前登録が固定した値です。変えたまま封印しません。**

.EXAMPLE
    .\scripts\revision-power.ps1
#>
[CmdletBinding()]
param(
    [string]$Dir = '',
    [int]$Holding = 0
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '上方修正の散らばり（#5 の §0。判定ではない）'
Write-Host 'OOS には1日も触れません。判定は封印してから1回だけです。' -ForegroundColor Yellow
Write-Host '線（片側95%下限で 1.2%）は測る前にコミット済みです。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'revision-power')
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }
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
