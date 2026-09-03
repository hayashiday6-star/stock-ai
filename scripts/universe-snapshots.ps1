<#
.SYNOPSIS
    過去のスナップショットで、いま上場していない会社が取れるかを確かめる。

.DESCRIPTION
    事前登録すべてに「上場廃止銘柄が入っていない」と書いてきました。短期
    リバーサル（負け組を買う）では、**これは脚注ではなく主要な脅威**です。
    大きく下げた銘柄を買って持つ——その中で消えたものが丸ごと欠けたデータで
    測ることになります。

    `equities/master` は日付を取ります。2018年のスナップショットに、いまの
    DBに無い銘柄コードが含まれていれば、**生存バイアスは制約ではなく作業**に
    変わります。

    **これは解約予定（2026-09-22）の J-Quants プランを使います。** 取れる場合、
    スナップショットは解約より前に貯めなければなりません。

.EXAMPLE
    .\scripts\universe-snapshots.ps1
    .\scripts\universe-snapshots.ps1 -Dates 2016-06-01,2019-06-01
#>
[CmdletBinding()]
param(
    # 5年ローリングの外は 400 で断られる（2026-09 時点の境界は 2021-09 前後）。
    # 断られる日付を入れておくと境界がどこかが分かる。
    [string]$Dates = '2021-06-01,2021-10-01,2022-06-01,2023-06-01,2024-06-01,2025-06-01'
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '過去の銘柄一覧が取れるか（生存バイアスが直せるか）'
Write-Host '見るのは2つです。' -ForegroundColor DarkGray
Write-Host '  1. スナップショット同士の差 = その期間に廃止された銘柄' -ForegroundColor DarkGray
Write-Host '  2. その銘柄の株価が取れるか（取れないと一覧だけでは使えない）' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai universe-snapshots --dates $Dates
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。件数と銘柄コードだけです。'
}

Exit-WithPause $code
