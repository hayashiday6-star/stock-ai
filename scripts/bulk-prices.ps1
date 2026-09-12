<#
.SYNOPSIS
    保存した原本から、全銘柄の株価を DB に入れる。API は1回も叩かない。

.DESCRIPTION
    **銘柄ごとに叩きません。** いまの取り込みは1銘柄1リクエストで、この経路
    は2回止まっています——84銘柄で1回、3,700銘柄で1回、どちらもレート制限
    です。20年 × 約4,400銘柄は1週間に収まりません。

    一括ファイルには**全銘柄の四本値が日付ごとに**入っています。名簿と同じ
    形です。保存済みの原本から読むだけなので、**解約後にも実行できます。**

    上場廃止した銘柄も入っています。その日に売買があった銘柄はすべて載って
    いるので、**生存バイアスの材料がそのまま揃います。**

    保存するのは生値です。読み出しのときに分割調整が掛かるので、ここで調整
    すると二重になります。

    何度実行しても同じ結果になります。**DB は作り直せる**ので、ここでの失敗
    は安い——取り直せないのは原本のほうで、それは pCloud にあります。

.PARAMETER Limit
    読む原本の本数の上限。**まず少数で試すとき**に使います。

.EXAMPLE
    .\scripts\bulk-prices.ps1 -Limit 1
    .\scripts\bulk-prices.ps1
#>
[CmdletBinding()]
param(
    [int]$Limit = 0,
    [string]$Dir = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '一括ファイルから株価を取り込む（取得はしない）'
Write-Host '保存済みの原本から読むだけです。API は1回も叩きません。' -ForegroundColor DarkGray
Write-Host '上場廃止した銘柄も入っています。' -ForegroundColor DarkGray
if ($Limit -gt 0) {
    Write-Host ''
    Write-Host "$Limit 本だけ読みます。まず少数で試す形です。" -ForegroundColor Yellow
}
Write-Host ''

$arguments = @('run', 'stock-ai', 'jquants-bulk-prices')
if ($Limit -gt 0) { $arguments += @('--limit', "$Limit") }
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '上のまとめと警告を貼ってください。'
}

Exit-WithPause $code
