<#
.SYNOPSIS
    解約で失われるもののうち、まだ手元に無いものを数える。

.DESCRIPTION
    J-Quants の有料プランは 2026-09-22 で切れます。切れてから「これが無いと
    動かない」と分かるのがいちばん困る形なので、先に数えます。

    作り直せるものは急ぎません。株価は立花、有報は EDINET から何度でも取れます。
    作り直せないのは3つです。

      1. 日付ごとの上場名簿（立花のマスタは現存銘柄のみ）
      2. 上場廃止銘柄の株価（立花にはもう存在しない銘柄コード）
      3. 会社の通期予想と開示時刻（EDINETの有報は実績のみ）

    5年ローリング窓は解約より先に効きます。いま取れるのは 2021-09 以降で、
    その端は毎日後ろへ動きます。「解約日まで待てる」ものはありません。

    数えるだけで、取りには行きません。

.EXAMPLE
    .\scripts\jquants-inventory.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '解約前の棚卸し'
Write-Host '数えるだけです。取得はしません。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai jquants-inventory
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。残りの日数の使い道を決めます。'
}

Exit-WithPause $code
