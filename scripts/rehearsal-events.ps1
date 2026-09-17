<#
.SYNOPSIS
    イベント型の管を校正する。**説ではありません。予算に数えません。**

.DESCRIPTION
    月次の対照は `t` の SD が **1.12**（1.00 のはず）で返り、判定の線は設計より
    3倍甘いと分かりました。**あれは月次の盤面で測った値です。**

    **#8（増担保）と #5（上方修正）は、その管を使っていません。** 系列がイベント
    日ごとで、Newey-West のラグも保有日数に取ってあります。**別の管には別の
    数字がありえます。**

    ここは乱数で選んだ日と銘柄を `event_window.event_returns` に通します——
    **#8・#5 が呼んでいる関数そのもの**です。別の管を作ったら、本物について
    何も確かめたことになりません。

      SD が 1.12 に近い    月次の値をそのまま当てられる
      大きく違う           イベント型には別の数字が要る

    **日の固まり方は本物に合わせていません。** 同じ日数・同じ件数で、中身だけ
    乱数です。**本物のほうが固まっていれば、膨張はここより大きく出ます。**

    **API を1回も叩きません。** 日足を400回ぶん読むので、**これは遅いです。**

.PARAMETER Repeat
    回数。**200 以上でないと形が読めません。** 既定は 400。

.PARAMETER Events
    1回あたりのイベント数。既定 2,000（#5 の OOS が 2,090 件）。

.EXAMPLE
    .\scripts\rehearsal-events.ps1
#>
[CmdletBinding()]
param(
    [int]$Repeat = 0,
    [int]$Events = 0,
    [int]$Holding = 0
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section 'イベント型の対照（説ではない。予算に数えない）'
Write-Host '#8・#5 が呼んでいる関数そのものに、乱数を通します。' -ForegroundColor Yellow
Write-Host '月次で測った 1.12 が、ここにも当てはまるとは限りません。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'rehearsal-events')
if ($Repeat -gt 0) { $arguments += @('--repeat', $Repeat) }
if ($Events -gt 0) { $arguments += @('--events', $Events) }
if ($Holding -gt 0) { $arguments += @('--holding', $Holding) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と、その下の1〜2行を貼ってください。'
}

Exit-WithPause $code
