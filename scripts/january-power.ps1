<#
.SYNOPSIS
    #14「1月効果」の IS を測ります。**判定ではありません。**

.DESCRIPTION
    測るのは「1月は上がるか」ではなく、**小型 − 大型の差が1月だけ大きいか**
    です（事前登録 §1）。時価総額で5分位に分け、**最小分位 − 最大分位**の月次
    スプレッドを作り、**その年の1月**と**同じ年の他の月の平均**の差を取ります。

      1観測 =（その年の1月のスプレッド）
           −（同じ年の、1月以外の月のスプレッドの平均）

    **観測は年に1回しかありません。** IS（2009-01〜2017-11）で 9回、
    OOS（2018-01〜2026-08）でも 9回です。

    **n=9 では `t` が正規から離れます。** 自由度8の正しい線は **4.33** で、
    対照の `t` の SD から出す 3.49 では **24% 甘い**（事前登録 §0）。表には
    **素の線 3.02**（線の下限）と**自由度8の線**の両方で「検出できる差」を
    出します。

    **費用は引きません**（事前登録 §4）。その代わり §0 の線を、いちばん安い
    実装——12月末に仕込んで1月末に外す、年1往復——の費用 **1月 0.4%** から
    置いてあります。

    **流動性の絞りは #9・#12 と同じ（日商1億円）です。** 小型株の側をまるごと
    削るので、**削った数を出します。** 日商1億円に届かない小型株については、
    何も主張しません（事前登録 §9）。

    **IS は 2009-01〜2017-11 で、OOS には1ヶ月も触れません。**

    **API を1回も叩きません。** 全銘柄の足を1度ずつ読むので時間がかかります。

.PARAMETER IsEnd
    IS の最終日。既定は 2017-12-31 です。**動かす理由はありません。**

.EXAMPLE
    .\scripts\january-power.ps1
#>
[CmdletBinding()]
param(
    [string]$IsEnd = '2017-12-31'
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '#14 1月効果 — IS を測る（判定ではない）'
Write-Host '測るのはサイズの傾きです。設計は事前登録が固定しています。' -ForegroundColor Yellow
Write-Host '観測は年に1回。OOS には1ヶ月も触れません。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai january-power --is-end $IsEnd
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '2つの表と、その下の数行を貼ってください。'
}

Exit-WithPause $code
