<#
.SYNOPSIS
    イベント型の管を校正する。**説ではありません。予算に数えません。**

.DESCRIPTION
    1回目で、散らばりは片が付きました——`t` の SD は **0.94** で、1.00 に
    近く素直です。**問題は中心のほうでした。`t` の平均が +0.49。** 乱数で
    選んだ銘柄と日を20営業日持つだけで、指数に系統的に勝っていました。

    2回目の分解で出どころが分かりました。**銘柄側 +1.11% に対して指数側
    +0.88%、差 +0.22%。生存フィルタの押し上げは −0.00%** でした。
    `1306` は時価総額加重、イベントのバスケットは等加重——**加重の違う
    ものを引き算していた**だけで、バグではありません。

    そこで**引く相手を等加重の宇宙に替えました。** これが既定です。

      t の平均が 0 に近い    加重を揃えたことで下駄が取れた
      まだ +0.5 前後         下駄の出どころは他にもある

    **0 に近いのは、ある程度は当たり前です。** 一様に引いた銘柄の平均を
    一様に引いた銘柄から引くので、そこは近くなって当然です。**それでも
    通す意味はあります**——実装が思ったとおり効いているかは、ここでしか
    見られません。

    ここが通しているのは `event_window.event_sample` です——**#8・#5 が
    呼んでいる関数そのもの**で、別の管を作ったら何も確かめたことに
    なりません。

    **日の固まり方は本物に合わせていません。** 同じ日数・同じ件数で、中身だけ
    乱数です。**本物のほうが固まっていれば、膨張はここより大きく出ます。**

    **API を1回も叩きません。** 日足を400回ぶん読むので、**これは遅いです。**

.PARAMETER Repeat
    回数。**200 以上でないと形が読めません。** 既定は 400。

.PARAMETER Events
    1回あたりのイベント数。既定 2,000（#5 の OOS が 2,090 件）。

.EXAMPLE
    .\scripts\rehearsal-events.ps1

.PARAMETER Subtract
    引く相手。`universe`（等加重、既定）か `index`（`1306`、時価総額加重）。
    **`index` にすると、直す前の姿が見られます。**

.PARAMETER BenchmarkFraction
    引く相手を作るのに使う銘柄の割合。**診断用です。**

    等加重に替えたら `t` の SD が 0.94 から **1.09** に上がりました。出どころ
    が分かっていません。**引く相手そのものが推定値なので、その揺れが観測に
    乗っている**のかもしれません。

    `0.25` にすると引く相手の誤差が2倍になります。**日は1日も減りません**
    ので、SD が動けばその筋、動かなければ別の筋です。
#>
[CmdletBinding()]
param(
    [int]$Repeat = 0,
    [int]$Events = 0,
    [int]$Holding = 0,
    [ValidateSet('universe', 'index')]
    [string]$Subtract = '',
    [double]$BenchmarkFraction = 0
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section 'イベント型の対照（説ではない。予算に数えない）'
Write-Host '#8・#5 が呼んでいる関数そのものに、乱数を通します。' -ForegroundColor Yellow
Write-Host '引く相手は等加重の宇宙です（時価総額加重の 1306 ではありません）。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'rehearsal-events')
if ($Repeat -gt 0) { $arguments += @('--repeat', $Repeat) }
if ($Events -gt 0) { $arguments += @('--events', $Events) }
if ($Holding -gt 0) { $arguments += @('--holding', $Holding) }
if ($Subtract) { $arguments += @('--subtract', $Subtract) }
if ($BenchmarkFraction -gt 0) { $arguments += @('--benchmark-fraction', $BenchmarkFraction) }

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
