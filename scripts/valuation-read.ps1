<#
.SYNOPSIS
    保存した「1株あたりの指標と時価総額」を読んで、中身が正しいか確かめる。

.DESCRIPTION
    **時価総額を自前で組み立てないための読み口です。** 株価 × 発行済株式数 は、
    分割を跨ぐと尺度が変わります——このプロジェクトが繰り返し踏んでいる形です。
    J-Quants 側で組み立て済みの値を読みます。

    列は11。`EPS` `BPS` `ROE` `PER` `PBR` `MktCap` と、`Fwd` の付いた会社予想
    3つです。**実績と予想は別の列として持ちます。** 混ぜると、発表前から予想を
    知っていたことになります。

    出すもの。

      年ごとに、列がどれだけ埋まっているか
      時価総額が半分も埋まっていない年
      `PER × EPS` と `PBR × BPS` が一致するか

    最後のものが要です。**どちらも終値を指すはずの掛け算**なので、合わなければ
    列の意味がこちらの想像と違うということです。**別の原本を持ち出さずに、この
    ファイルだけで言えます。**

    公式には「2008〜2010年頃は Null が多い」とあります。**引き写さずに数えます。**
    どの列がどれだけ空なのかは、そこには書かれていません。

    **API を1回も叩きません。**

.EXAMPLE
    .\scripts\valuation-read.ps1

.EXAMPLE
    .\scripts\valuation-read.ps1 -Limit 12
#>
[CmdletBinding()]
param(
    [int]$Limit = 0,
    [string]$Dir = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '1株あたりの指標と時価総額を読む'
Write-Host '年ごとの埋まり具合と、2つの掛け算が一致するかを出します。' -ForegroundColor DarkGray
Write-Host 'API は叩きません。229本あるので数分かかります。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'jquants-valuation')
if ($Limit -gt 0) { $arguments += @('--limit', $Limit) }
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と、そのあとの数行を貼ってください。'
}

Exit-WithPause $code
