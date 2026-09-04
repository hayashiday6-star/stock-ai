<#
.SYNOPSIS
    封印済みの低ボラ検証を、OOS で一度だけ回す。

.DESCRIPTION
    docs\PREREG_LOWVOL_JP.md は 2026-09-04 に封印しました。低ボラのリターンを
    1つも見ずに、設計・合格基準・読み方の表まで決めてあります。**これはその
    設計が買った、一度きりの判定です。**

    主要指標は α（分位1 − β×ベンチマーク）です。β は判定に使わない期間
    （2002-2013）で推定した 0.542 に固定してあります。仮説が「リスク調整後で
    高い」と言っているので、測る対象を仮説に合わせました。

    **α が正でも、指数を上回ったとは限りません。** β=0.542 なので
    分位1 − 指数 ＝ α − 0.458×市場リターン。上げ相場では α が正でも指数に
    負けます。出力にはその境目と、実際の生の差を必ず併記します。

    覚えておくこと（登録 §16 の3行目）:

      α が 0.046%〜0.33%／月 で t<2.0 なら、それは「構造的な帯」です。
      不合格ですが「効果が無い」ではありません。基準は緩めず、前向きに
      貯めます。

.PARAMETER Period
    oos | is。既定は oos（判定）。

.EXAMPLE
    .\scripts\lowvol-run.ps1
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

Write-Section "低ボラティリティ：封印済みの判定（$Period）"
if ($Period -eq 'oos') {
    Write-Host 'これは一度だけの判定です。結果を見てから条件を変えません。' -ForegroundColor Yellow
}
Write-Host '主要指標は α（分位1 − β×ベンチ）。β は封印済みの 0.542。' -ForegroundColor DarkGray
Write-Host 'α が正でも指数を上回ったとは限りません。境目も併記します。' -ForegroundColor DarkGray
Write-Host '合否は §16 の表を当てはめて出ます。解釈の余地はありません。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai lowvol-run --period $Period
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
