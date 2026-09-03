<#
.SYNOPSIS
    生存バイアスが実際にどれだけの大きさで、どちら向きかを測る。

.DESCRIPTION
    同じ期間・同じ日を2回回し、変えるのは universe だけです。1回目は日付ごとの
    名簿（後に上場廃止になった会社を含む）、2回目はいま残っている銘柄だけ。
    その差が、そのままバイアスの大きさと符号になります。

    IS（2021-09〜2023-12）でだけ回します。-End は 2024-01-01 以降を受け付け
    ません。OOSを覗くと、判定に使える一度が失われるためです。

    これはこの説だけの話ではありません。これまでの事前登録はすべて「上場廃止
    銘柄が入っていない」と書きながら、その大きさも符号も数字にしていません
    でした。TOB・完全子会社化はプレミアム付きで消え、破綻はゼロになるので、
    向きは自明ではありません。**プロジェクト全体で使い回せる数字**になります。

    先に checks\廃止銘柄の取り込み.bat を実行しておく必要があります。名簿が
    無いと差の取りようがありません。

.PARAMETER End
    測る最後の日。既定は OOS の前日（2023-12-31）。

.EXAMPLE
    .\scripts\reversal-bias.ps1
#>
[CmdletBinding()]
param(
    [string]$End = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '生存バイアスの大きさと符号を測る'
Write-Host '同じ日を2回回し、universe だけを差し替えます。' -ForegroundColor DarkGray
Write-Host '差がバイアスです。ISでだけ回し、OOSは覗きません。' -ForegroundColor DarkGray
Write-Host '先に checks\廃止銘柄の取り込み.bat が終わっている必要があります。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'reversal-bias')
if ($End) { $arguments += @('--end', $End) }

uv @arguments
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。事前登録に書き写します。'
}

Exit-WithPause $code
