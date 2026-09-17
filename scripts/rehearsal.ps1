<#
.SYNOPSIS
    陰性対照 — 乱数の signal を、端から端まで通す。**説ではありません。**

.DESCRIPTION
    **このプロジェクトには対照が1本もありませんでした。** §0 の関門も、判定の
    当てはめも、**本物のデータの上でしか試していません。**

    問いは1つ。**何も無いときに、この仕組みは合格を出すか。**

    出したら、**仕組みが壊れています。** 出ないことは、これまでの4本の
    「封印せず」より強い保証になります——**あちらは関門で止まっていて、判定
    そのものを通していません。**

    **signal だけが乱数です。** 月も universe もリターンも本物のまま。全部を
    乱数にすると、重なりも自己相関も消えて、**いちばん確かめたい部分が消えます。**

    **予算に数えません。** 世界について何も主張していないので、「当たりを引こう
    とした回数」に入りません。**§0 も通しません**——判定を消費しないものには、
    守るものがありません。

    既定は1回で、IS → 封印 → OOS → 判定まで出ます。

    **ただし1回では校正できません。** `t >= 3.02` を越える確率は 0.125% で、
    400回回しても期待値は 0.5 回です。**`-Repeat 400` を付けると `t` の分布**を
    出します——帰無なら SD は 1.00 のはずで、**1.15 なら補正が足りていません。**

    **API を1回も叩きません。** 盤面を2つ組むので数分かかります。

.PARAMETER Repeat
    種を変えて回す回数。**200 以上でないと形が読めません。** 既定は 1。

.EXAMPLE
    .\scripts\rehearsal.ps1
.EXAMPLE
    .\scripts\rehearsal.ps1 -Repeat 400
#>
[CmdletBinding()]
param(
    [int]$Repeat = 1,
    [int]$Seed = 0
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '陰性対照（説ではない。予算に数えない）'
Write-Host '乱数の signal を、本物と同じ管に通します。' -ForegroundColor Yellow
Write-Host '合格が出たら、仕組みが壊れています。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'rehearsal', '--repeat', $Repeat)
if ($Seed -gt 0) { $arguments += @('--seed', $Seed) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と、その下の行を貼ってください。'
}

Exit-WithPause $code
