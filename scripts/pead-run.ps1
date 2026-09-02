<#
.SYNOPSIS
    封印した事前登録どおりに PEAD を計算する（docs/PREREG_PEAD_JP.md）。

.DESCRIPTION
    期間の指定が必須です。既定を置いていません。合否判定はOOSで一度だけ
    行うもので、「とりあえず全部出す」が既定だと、その一度が失われます。

    OOS を走らせるには -ReadyForOos が要ります。実装の確認とIS期間の確認が
    終わるまで、OOSは見ません。

    ベンチマークを指定すると市場対比の超過リターンになります。指定しない
    場合は素のリターンですが、**上位分位と下位分位の差ではベンチマークが
    相殺される**ので、合否に使う主要指標は成立します。ただし驚きの並べ替えは
    地合いの影響を受けるので、指定できるならしたほうが正確です。

.PARAMETER Period
    is | oos | all。

.PARAMETER Benchmark
    市場対比に使う銘柄コード（TOPIX連動ETFなど）。省略可。

.PARAMETER ReadyForOos
    OOS を含む期間を走らせるときに必要。

.EXAMPLE
    .\scripts\pead-run.ps1 -Period is
    .\scripts\pead-run.ps1 -Period is -Benchmark 1306
#>
[CmdletBinding()]
param(
    [ValidateSet('is', 'oos', 'all')]
    [string]$Period = 'is',
    [string]$Benchmark,
    [switch]$ReadyForOos
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section "PEAD: 封印した事前登録どおりに計算する ($Period)"
if ($Period -ne 'is' -and -not $ReadyForOos) {
    Write-Err "'$Period' には検証用に取り置いた期間が含まれます。"
    Write-Host ''
    Write-Host '実装の確認とIS期間の確認が終わってから、-ReadyForOos を付けて'
    Write-Host '実行してください。合否判定はOOSで一度だけ行います。'
    Exit-WithPause 1
}
Write-Host '全銘柄の価格と開示を突き合わせます。数分かかります。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'pead-run', $Period)
if ($Benchmark) { $arguments += @('--benchmark', $Benchmark) }
if ($ReadyForOos) { $arguments += '--i-am-ready-for-oos' }

uv @arguments
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。'
}

Exit-WithPause $code
