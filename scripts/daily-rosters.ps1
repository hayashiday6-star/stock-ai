<#
.SYNOPSIS
    保存した原本から、営業日ごとの名簿を取り出す。API は1回も叩かない。

.DESCRIPTION
    **一括ファイル1本の中に、その月の全営業日ぶんが入っています。**
    2026-08 の1本が 88,870 行で、4,441銘柄 × 20営業日でした。

    いま手元にある名簿は、JSON API を30日刻みで叩いて集めた66枚です。
    **同じ5年ぶんが、一括ファイルには約1,220枚（全営業日）入っています。**

      30日刻み（66枚）    廃止が「どの月」に起きたかまで
      営業日ごと（1,220枚） 廃止が「どの日」に起きたかまで

    20年ぶんなら約5,000枚になります。**取得はしません**——原本さえあれば、
    解約後にも何度でも取り出し直せます。

    書き出し先は data\universe_daily で、いまの data\universe_snapshots とは
    **別です。混ぜません。** 絞り込みが食い違ったとき、境目をまたいだ差が
    「消えてもいない銘柄が消えた」に化けるためです。

.PARAMETER Refetch
    すでにある日付も書き直す。

.EXAMPLE
    .\scripts\daily-rosters.ps1
#>
[CmdletBinding()]
param(
    [switch]$Refetch,
    [string]$Dir = '',
    [string]$Out = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '営業日ごとの名簿を取り出す（取得はしない）'
Write-Host '保存済みの原本から取り出すだけです。API は1回も叩きません。' -ForegroundColor DarkGray
Write-Host '書き出し先は universe_daily で、いまの universe_snapshots とは別です。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'jquants-daily-rosters')
if ($Refetch) { $arguments += '--refetch' }
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }
if ($Out -ne '') { $arguments += @('--out', $Out) }

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
