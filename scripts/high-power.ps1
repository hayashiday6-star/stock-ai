<#
.SYNOPSIS
    52週高値バスケットの散らばりを測る。**平均は出しません。判定ではありません。**

.DESCRIPTION
    件数センサスは通りました（248,217件・5,843日・上位1割の日の占有 39%・
    翌日始値のギャップ中央値 +0.03%）。次は §0 ゲートの入力です。

    §0 に入れる「検出できる差」は実測から取る、と決めてあります。その実測が
    これです。

    **平均は表示しません。** 表示すれば、封印の前に答えを見たことになります。
    出るのは散らばり（SD）と、重なりによる標準誤差の膨張だけです。

    **分散を測ることは判定を消費しません。** 効果の大きさではなく散らばりを
    測っているからで、#6・#7 と同じ手順・同じ理由です。

    保有日数を1日・5日・20日の3通りで出します。**分散は効果ではないので、
    ここで比べても多重検定にはなりません。** 封印するのは1本だけで、それを
    どの窓にするかを決める根拠がこの表になります。

    見るのは「t≥2.0 に必要な差」の列です。そこに費用を足したものが、
    イベント型として現実的な効果量に収まるかどうかで決まります。

    全銘柄の日足を3回読むので、件数センサスより時間がかかります。

.PARAMETER MinTurnover
    流動性の下限（円）。既定は他の説と同じ1億円。

.EXAMPLE
    .\scripts\high-power.ps1
#>
[CmdletBinding()]
param(
    [double]$MinTurnover = 100000000
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '52週高値バスケットの散らばり（平均は出さない）'
Write-Host '平均は表示しません。封印の前に答えを見ないためです。' -ForegroundColor Yellow
Write-Host '分散を測ることは判定を消費しません。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai high-power --min-turnover $MinTurnover
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '表だけ貼ってください。'
}

Exit-WithPause $code
