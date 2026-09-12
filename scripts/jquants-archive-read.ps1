<#
.SYNOPSIS
    保存した原本を、読み口に通して数える。1バイトも落とさない。

.DESCRIPTION
    **「落とせた」と「読めた」は別です。** このプロジェクトはこの2日で2回
    それを踏んでいます（名簿の取り直しは3回、原本の保存は2回）。原本を保存
    したあとで読み口が繋がっていないと分かるのが、いちばん高くつきます。

    **配布サンプルで通ったことは、実物で通ったことになりません。** サンプル
    は1〜6行しかなく、一括ファイルは月次で全銘柄が入っています。文字コード
    も、サンプルが cp932 だったからといって一括ファイルもそうとは限りません。

    出るのは2つの表です。

      1. エンドポイントごとの本数・行数・状態
      2. 1本ずつ見た形（行数・文字コード・日付の範囲・列名）

    2つ目は**契約日数の見積もりに効きます。** 1本が1ヶ月ぶんなら20年で240本、
    1日ぶんなら5,000本で、20倍違います。

.PARAMETER Dir
    置き場所。既定は data/jquants_bulk。

.PARAMETER NoShapes
    1つ目の表だけにする。

.EXAMPLE
    .\scripts\jquants-archive-read.ps1
#>
[CmdletBinding()]
param(
    [string]$Dir = '',
    [switch]$NoShapes
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '原本を読み口に通す（落とさない）'
Write-Host '保存したものが実際に読めるかを確かめます。取得はしません。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'jquants-archive-read')
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }
if ($NoShapes) { $arguments += '--no-shapes' }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の表をそのまま貼ってください。'
}
else {
    Write-Ok '上の2つの表をそのまま貼ってください。'
    Write-Host '「読み口を作っていない」は、保存できていて読む口が無いだけです。' -ForegroundColor DarkGray
}

Exit-WithPause $code
