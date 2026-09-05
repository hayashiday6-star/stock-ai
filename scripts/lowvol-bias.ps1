<#
.SYNOPSIS
    低ボラの生存バイアスを実測する（判定の後）。

.DESCRIPTION
    判定は 2026-09-05 に済んでいます（§18）。**これは判定を変えません。**
    §11 で「判定の後に測り、結果に添える」と事前登録した数字です。

    同じ月を2回回し、変えるのは universe だけ。日付ごとの名簿（廃止銘柄を
    含む）と、いま上場している銘柄だけ。その差がバイアスです。

    #6 のリバーサルは −0.040%／20営業日でした。あちらは下落率上位を買う形で
    バイアスが最も濃く乗ります。低ボラは破綻に向かう銘柄がボラティリティを
    上げて分位5に落ちるので、**それより小さいはず**——と §11 に書きました。
    「はず」なので測ります。

    名簿は 2021-09 以降しかないので、測れるのはその範囲だけです。

.PARAMETER Beta
    封印済みの 0.542。ここで推定し直さないこと。

.EXAMPLE
    .\scripts\lowvol-bias.ps1
#>
[CmdletBinding()]
param(
    [double]$Beta = 0
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '低ボラ：生存バイアスの大きさ（判定は変わらない）'
Write-Host '§11 で事前登録した診断です。判定は §18 に記録済み。' -ForegroundColor DarkGray
Write-Host '同じ月を2回、universe だけ変えて回します。数分かかります。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'lowvol-bias')
if ($Beta -gt 0) { $arguments += @('--beta', "$Beta") }

uv @arguments
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '表だけ貼ってください。事前登録に書き写します。'
}

Exit-WithPause $code
