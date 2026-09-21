<#
.SYNOPSIS
    候補6と7の材料が原本に在るかを数えます。**取りには行きません。**

.DESCRIPTION
    **どちらも「測れない」と書いてありました**（2026-09-21 に直した）。
    候補6は「心理指標を1つも持っていない」、候補7は「設計が決まっていない」。
    **どちらも原本に材料が在りました。**

    | 候補 | 材料 | 1観測 |
    |---|---|---|
    | 6 悲観の中に生まれ | オプションの予想変動率 | 1日 |
    | 7 需給はすべての材料に優先する | 投資部門別の売買差引 | 1週 |

    **予想変動率の列は、古い原本では空です。** 2008-05 の原本では
    `IV` / `BaseVol` / `UnderPx` などが全行で空で、2026-01 では埋まって
    います。**どこから埋まるのかは、年ごとに数えないと分かりません**
    ——無いことは、出力に出ません。

    **畳み方は、壁を測る前に1つに決めてあります。** 複数試して良いほうを
    採ると、その時点で #10 と同じところに落ちます。

    **壁はまだ1本も測っていません。** 「通りうる」は設計の形からの推論で
    あって、数字ではありません。`research\壁の下見.bat` が測ります。

.EXAMPLE
    .\scripts\material-coverage.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '候補6と7の材料は在るか — 「測れない」と書いてあったのを確かめる'
Write-Host '取りには行きません。原本を読むだけです。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai material-coverage
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '材料が作れませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と警告だけで足ります。'
}

Exit-WithPause $code
