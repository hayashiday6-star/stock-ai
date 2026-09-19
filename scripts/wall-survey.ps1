<#
.SYNOPSIS
    まだ登録していない説について、**検出できる差だけ**を出します。

.DESCRIPTION
    事前登録を書く前に、**壁の高さ**を見るための下見です。

    検出できる差は `線 × SD ÷ √n` で、**散らばりと観測数だけで決まります。**
    効果（平均）は式に入らないので、**これを見ても答えを先に見たことには
    なりません。** `Wall` に平均の欄を作っていないのも同じ理由です。

    測るのは4つ。

      3  Sell in May（冬 − 夏）        年1観測の暦
      5  新値には黙ってつけ            月次・分位ロングショート
      9  窓は埋まる（下窓）            イベント型
      10 落ちるナイフ（急落）          イベント型

    **材料が無くて測れない候補も表に出します**（6・7・8）。無いことは、
    出力に出ないからです。

    **IS（〜2017-12）のリターンだけを読みます。** イベントの**件数**だけは
    OOS も数えます——判定に使える観測数がそこで決まるためで、件数は効果では
    ありません。

    **イベント型の窓は 20営業日に揃えてあります**（#5 と同じ物差し）。
    事前登録が別の窓を選ぶなら、そこで測り直します。

    **API を1回も叩きません。** 価格を4回なめるので時間がかかります。

    結果は `docs\WALL.md` にも書き出します。**生成物です。手で直さないこと。**

.PARAMETER NoWrite
    `docs\WALL.md` を書き換えません。画面に出すだけになります。

.EXAMPLE
    .\scripts\wall-survey.ps1
#>
[CmdletBinding()]
param(
    [switch]$NoWrite
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '壁の下見 — 事前登録を書く前に、検出できる差だけを見る'
Write-Host '効果（平均）は1つも出しません。壁の高さだけです。' -ForegroundColor Yellow
Write-Host '価格を4回なめます。時間がかかります。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'wall-survey')
if (-not $NoWrite) { $arguments += @('--write', 'docs/WALL.md') }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '2つの表と、その下の警告を貼ってください。'
}

Exit-WithPause $code
