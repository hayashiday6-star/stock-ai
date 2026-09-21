<#
.SYNOPSIS
    高すぎる配当利回りの中身を見ます。**取りには行きません。**

.DESCRIPTION
    利回りが、日本の上場企業がまず払わない高さで出る銘柄月があります。
    **外していない**ので、**それが SD に入ったまま壁が出ています**
    ——外れ値は SD に効くので、**件数の小ささは理由になりません。**

    **件数はここに書きません。** その回の出力が数えるものを先に書くと、
    **次に変わったときだけ古いまま残ります**（`CLAUDE.md`）。

    3つのどれかです。

    | 見え方 | どれか |
    |---|---|
    | **予想 ÷ 実績 が 10 や 100** | **訂正前の誤記**（2131 の `5600 → 56`） |
    | どちらも大きい | **株価か単位** |
    | 実績が空 | **無配への訂正前**か、予想しか出していない |

    **原因を推測で決めません。** 比が 100 に揃っていても「訂正はいつも
    100倍」にはなりません——**割合で見ます。**

.PARAMETER Limit
    出す行数（利回りの大きい順）。既定は 20。

.EXAMPLE
    .\scripts\yield-audit.ps1

.EXAMPLE
    .\scripts\yield-audit.ps1 -Limit 60
#>
[CmdletBinding()]
param(
    [int]$Limit = 20
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '高すぎる配当利回り — 中身を見る'
Write-Host '取りには行きません。原本と価格を読むだけです。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai yield-audit --limit $Limit
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '中身を出せませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と警告だけで足ります。'
}

Exit-WithPause $code
