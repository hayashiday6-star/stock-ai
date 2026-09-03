<#
.SYNOPSIS
    通期決算の実績と、その時点で公表済みだった会社予想を組にして数える。

.DESCRIPTION
    SUE（実績と予想の差）を日本の開示で組めるかを、リターンを見る前に
    確かめます。

    **四半期では組めません。** 日本の短信は「通期予想」と「期中累計の実績」を
    出すので、Q1の実績3ヶ月ぶんを通期予想12ヶ月ぶんから引くことになります。
    差を取るには「期待累計＝通期予想×季節配分」という推定が要り、可動部が
    増えます。校正用の物差しに可動部は持ち込みません。

    通期短信だけなら、実績も予想も同じ12ヶ月ぶんで、そのまま引けます。

    出力で見るのは3つです。

    - **独立開示日数**。通期短信は5月に集中するので、イベント数のわりに
      日数が少なくなります。差の検定はこの日数で効きます
    - **驚きの分布**。会社は着地が見えた時点で予想を出し直すので、実績が
      予想に寄りがちです。分位に分けても上位と下位が同じものになっていないか
    - **±1%未満の割合**。ここが大きいと、並べ替える意味が薄れます

    リターンは計算しません。件数と分布だけです。

.PARAMETER Field
    比較する項目。revenue | operating_income | net_income | eps。既定は net_income。

.EXAMPLE
    .\scripts\sue-census.ps1
    .\scripts\sue-census.ps1 -Field operating_income
#>
[CmdletBinding()]
param(
    [ValidateSet('revenue', 'operating_income', 'net_income', 'eps')]
    [string]$Field = 'net_income'
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section "SUE の件数センサス ($Field)"
Write-Host '予想フィールドは後から足した列です。財務を取り直していないDBでは' -ForegroundColor DarkGray
Write-Host '1件も組めません。その場合は checks\開示時刻の取り込み.bat を先に' -ForegroundColor DarkGray
Write-Host '実行してください。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai sue-census --field $Field
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。件数と分布だけで、銘柄名は出ません。'
}

Exit-WithPause $code
