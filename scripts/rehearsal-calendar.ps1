<#
.SYNOPSIS
    日次の暦の管を校正します。**説ではありません。予算に数えません。**

.DESCRIPTION
    #13（月替わり効果）は、月次でもイベント型でもない**3本目の管**です。
    1本の系列の中で**日どうしを比べる**別の推定量なので、月次で測った 1.12 も、
    イベント型で測った 1.09 も、**ここに当てはまるとは限りません。**

    **リターンは本物のまま、窓の位置だけを乱数にします。** 月次の対照が signal
    だけを乱数にしたのと同じ形です。**偽の窓は本物の窓の外に置きます**——重ねる
    と本物の効果が漏れ込み、「何も無いときの分布」になりません。

    出た SD を `MEASURED_INFLATION_CALENDAR` に入れるのは**こちらの仕事**です。
    貼っていただければ書き込みます。**それまで #13 は封印できません。**

    **API を1回も叩きません。**

.PARAMETER Repeat
    回数。**200 以上でないと形が読めません。** 既定は 400。

.EXAMPLE
    .\scripts\rehearsal-calendar.ps1
#>
[CmdletBinding()]
param(
    [int]$Repeat = 0
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '日次の暦の対照（説ではない。予算に数えない）'
Write-Host '#13 が使う管に、窓の位置だけ乱数にして通します。' -ForegroundColor Yellow
Write-Host '月次の 1.12 も、イベント型の 1.09 も、ここに当てはまるとは限りません。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'rehearsal-calendar')
if ($Repeat -gt 0) { $arguments += @('--repeat', $Repeat) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と、その下の数行を貼ってください。'
}

Exit-WithPause $code
