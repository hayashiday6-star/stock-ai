<#
.SYNOPSIS
    バリューの t が 2.26 と 0.76 に割れた、その出どころを探す。**判定ではありません。**

.DESCRIPTION
    同じ IS（2009-01〜2017-12）のバリューを2度測って、`t` が **+2.26**
    （#9、`antivalue-estimate`）と **+0.76**（#11、`composite-gate` の脚）に
    割れました。**universe と推定量が違うだけです。**

    **2つの推定が3倍食い違っているとき、信じるべきはその不安定さのほうで、
    高いほうの数字ではありません。** どちらが効いているのかを、1つずつ動かして
    出します。

    **最初に両端を再現します。** 再現できなければ、そこで数字を読みません——
    揃っていない2つを比べたら、差は「推定量の差」ではなく「フィルタの差」に
    なります。

    表の読み方。

      横に動く   推定量（β を引くかどうか）が効いている
      縦に動く   universe（履歴の要求・フィルタ）が効いている
      両方動く   **効果が設計の選び方に対して頑健でない**

    最後のときは、それ自体が**効果に対する反証寄りの情報**です。

    表はすべて**費用引き前**にそろえてあります。費用は平均を動かすので `t` も
    動き、片方だけ引くとそれが4つ目の違いになります。

    **判定を消費しません。** 見ているのは効果ではなく、**どこで数字が動くか**です。

    **API を1回も叩きません。** 盤面を2つ組むので数分かかります。

.EXAMPLE
    .\scripts\value-reconcile.ps1
#>
[CmdletBinding()]
param(
    [string]$Valuation = '',
    [string]$Rosters = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section 'バリューの食い違い（判定ではない）'
Write-Host '同じ IS を2度測って t が 2.26 と 0.76 に割れました。' -ForegroundColor Yellow
Write-Host '信じるべきは、その不安定さのほうです。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'value-reconcile')
if ($Valuation -ne '') { $arguments += @('--valuation', $Valuation) }
if ($Rosters -ne '') { $arguments += @('--rosters', $Rosters) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '検算の2行と、表を貼ってください。'
}

Exit-WithPause $code
