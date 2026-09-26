<#
.SYNOPSIS
    note に貼る記事の下書きと図（PNG）を作る。

.DESCRIPTION
    正本は docs/PUBLIC.md だけです。出力は reports/note/ に入り、git には
    載りません（作った PC のフォントで図の字形が変わるため）。

    検査に1つでも当たると、1本も書きません。内部の言葉、見出しの欠け、
    上場廃止と手数料の扱いの書き忘れ、出典の無い図、表（note は表を表示
    できません）。

    本文の「［図n をここに挿入］」の場所に、同じ名前の PNG を入れてください。

.EXAMPLE
    .\scripts\note-articles.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section 'note の下書きを作る'

uv run stock-ai note-articles
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '書けませんでした。上の問題の一覧を貼ってください。'
}
else {
    Write-Ok 'reports\note を開きます。'
    Invoke-Item 'reports\note'
}

Exit-WithPause $code
