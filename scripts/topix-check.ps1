<#
.SYNOPSIS
    TOPIX の指数そのものを読み、ベンチマークの ETF と引き算する。

.DESCRIPTION
    ベンチマークに **1306（TOPIX 連動 ETF）** を使っているのは、**指数が手元に
    無かったから**です。それ以上の理由はありません。

    Premium で `/indices/bars/daily/topix` が開きました。**2008-05 からの18年
    ぶんが、88 KB で入っています。**

      TOPIX   指数。信託報酬も、売買のずれもありません
      1306    それを追う ETF。信託報酬が毎日引かれ、追跡のずれが乗ります

    18年ぶん積み上がると小さくないはずですが、**それは見込みであって測った値
    ではありません。** 引き算します。

    出すもの。

      期間と日数          2008-05 からのはずです
      5日を超える穴       連休は5日までなので、それを超えたら欠けです
      指数と ETF の差     全体と、年あたりに直した値

    **差が正（ETF が指数を上回る）なら、説明が付きません。** 信託報酬は ETF を
    削る側だからです。そのときは配当か分割の扱いを疑います。

    **置き換えは判定のやり直しではありません。** 説#7 のベンチマークを替えて
    回し直せば2回目の判定になります。ここで作るのは測る道具で、使えるのはまだ
    判定を消費していない説（#5・#8）です。

    **API を1回も叩きません。** すぐ終わります。

.PARAMETER Etf
    比べる ETF。既定は 1306。

.EXAMPLE
    .\scripts\topix-check.ps1
#>
[CmdletBinding()]
param(
    [string]$Etf = '',
    [int]$Show = 0,
    [string]$Dir = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section 'TOPIX 指数とベンチマークの ETF'
Write-Host '指数そのものを読んで、1306 との差を引き算します。' -ForegroundColor DarkGray
Write-Host '信託報酬と追跡のずれが、18年でどれだけになるかを見ます。' -ForegroundColor DarkGray
Write-Host 'API は叩きません。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'jquants-topix')
if ($Etf -ne '') { $arguments += @('--etf', $Etf) }
if ($Show -gt 0) { $arguments += @('--show', "$Show") }
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と、最後の2〜3行を貼ってください。'
}

Exit-WithPause $code
