<#
.SYNOPSIS
    EDINET が実際にどこまで遡れるかを数える。

.DESCRIPTION
    手元の財務データは 58ヶ月で止まっています。**58ヶ月はほぼ5年ちょうど**で、
    J-Quants の5年ローリング窓と同じ数字です。EDINET の制約なのか、こちらの
    harvest 窓の設定なのか、データベースからは見分けが付きません。

    **推定量を変えるより、こちらのほうが効きます。** 150ヶ月から58ヶ月に
    なると標準誤差は 1.61倍。推定量を1.3倍改善しても食い潰されます。

    毎年1日ずつ叩いて件数を数えるだけです。保存も解析もしません。
    土日祝は 0 件が正しいので、**境目は「0 が始まってそのまま続く所」**で、
    最初の 0 ではありません。

    1分もかかりません。

.PARAMETER Years
    何年ぶん遡るか。既定 16。

.PARAMETER Day
    毎年その月の何日を叩くか。0 が並んだら別の日で試せます。

.EXAMPLE
    .\scripts\edinet-reach.ps1
    .\scripts\edinet-reach.ps1 -Day 20
#>
[CmdletBinding()]
param(
    [int]$Years = 16,
    [int]$Month = 6,
    [int]$Day = 15
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

if (-not (Test-EnvKeySet 'EDINET_API_KEY')) {
    Write-Section 'EDINET はどこまで遡れるか'
    Write-Err '.env に EDINET_API_KEY がありません。'
    Write-Host ''
    Write-Host 'APIキー設定.bat で設定してから実行してください。'
    Exit-WithPause 1
}

Write-Section 'EDINET はどこまで遡れるか'
Write-Host '58ヶ月が EDINET の制約か、こちらの設定かを見分けます。' -ForegroundColor DarkGray
Write-Host '件数を数えるだけ。保存も解析もしません。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai edinet-reach --years $Years --month $Month --day $Day
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '表だけ貼ってください。これで次の設計が決まります。'
}

Exit-WithPause $code
