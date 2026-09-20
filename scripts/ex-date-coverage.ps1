<#
.SYNOPSIS
    権利落ち日が原本にどれだけ入っているかを数えます。**取りには行きません。**

.DESCRIPTION
    #9「窓は埋まる」の設計が、これに掛かっています。

    **前日終値から −3% の下窓は、権利落ちがまさにそう見えます。** 外せなければ、
    事象の定義が配当を拾ってしまう。候補表の「確認する点」にも
    「配当落ち、株式分割、取引停止、時間外情報、スプレッドを除外・調整」と
    書いてあります。

    `/fins/dividend` は **Premium のエンドポイント**なので、解約後は原本に
    在るものがすべてです。**1バイトも取りに行きません。**

    **列ごとに独立に数えます。** 行が読めたことと、`ExDate` が埋まっている
    ことは別です。

.EXAMPLE
    .\scripts\ex-date-coverage.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '権利落ちは在るか — #9 の設計を決めるために数える'
Write-Host '取りには行きません。原本を読むだけです。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai ex-date-coverage
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '外す材料が足りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と、その上の1行を貼ってください。'
}

Exit-WithPause $code
