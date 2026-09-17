<#
.SYNOPSIS
    合格に要るリターンと、合格の条件を出す。**いつでも押してよい。**

.DESCRIPTION
    **数字を書き写していません。** 線が変われば要るリターンも全部変わります——
    実際 2026-09-17 に 3.02 から 3.39 に動きました。**書き写した数字は、古いまま
    もっともらしく見え続けます。** ここは測った散らばりから、そのつど計算します。

    出るのは2つ。

      合格に要るリターン   設計ごと。**いちばん甘い形で年 8%**
      合格の条件           5つの手順。専門用語なしで書いてあります

    散らばりは**どの事前登録に書いてあるか**まで出ます。出典の無い数字を
    載せないためです。

    **API を1回も叩きません。数秒で終わります。**

.PARAMETER Write
    `docs/PASSING.md` を作り直します。**あの文書は生成物です。**

.EXAMPLE
    .\scripts\passing.ps1
#>
[CmdletBinding()]
param(
    [switch]$Write
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '合格に要るリターンと、合格の条件'
Write-Host '数字は書き写さず、いまの線から計算しています。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'passing')
if ($Write) { $arguments += @('--write', 'docs/PASSING.md') }

uv @arguments
$code = $LASTEXITCODE

Exit-WithPause $code
