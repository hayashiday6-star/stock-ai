<#
.SYNOPSIS
    封印済みの短期リバーサル検証を、OOS で一度だけ回す。

.DESCRIPTION
    docs\PREREG_REVERSAL_JP.md は 2026-09-04 に封印しました。リバーサルの
    リターンを1つも見ずに、設計・合格基準・読み方の表まで決めてあります。
    **これはその設計が買った、一度きりの判定です。**

    判定はここで書きません。人が読んで解釈もしません。§8 の表を当てはめる
    だけです。前回（SUE版）は結果を見てから基準が動きかけました。「惜しかった」
    と思うのが事前登録のいちばんの敵なので、当てはめをコードにしてあります。

    universe は日付ごとの名簿なので、後に上場廃止になった会社も入っています。
    実測した生存バイアスは 20営業日あたり -0.040%。名簿を使わないと、その分だけ
    結果が実際より良く見えます。

    覚えておくこと（登録 §8 の3行目）:

      点推定が 0.40%〜0.85% で t<2.0 なら、それは「構造的な帯」です。
      不合格ですが「効果が無い」ではありません。基準は緩めず、前向きに
      貯めます。

.PARAMETER Period
    oos | is。既定は oos（判定）。

.EXAMPLE
    .\scripts\reversal-run.ps1
    .\scripts\reversal-run.ps1 -Period is
#>
[CmdletBinding()]
param(
    [ValidateSet('oos', 'is')]
    [string]$Period = 'oos'
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section "短期リバーサル：封印済みの判定（$Period）"
if ($Period -eq 'oos') {
    Write-Host 'これは一度だけの判定です。結果を見てから条件を変えません。' -ForegroundColor Yellow
}
Write-Host 'universe は日付ごとの名簿。廃止された会社も入っています。' -ForegroundColor DarkGray
Write-Host '合否は §8 の表を当てはめて出ます。解釈の余地はありません。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai reversal-run --period $Period
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。事前登録に判定として書き写します。'
}

Exit-WithPause $code
