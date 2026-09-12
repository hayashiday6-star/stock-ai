<#
.SYNOPSIS
    値幅制限と売買停止明けの件数を数える。**判定ではありません。**

.DESCRIPTION
    8本目を封印する前に、母集団があるかだけを先に確かめます。

    #1 では、条件を満たす銘柄の 97.5% が流動性フィルタで消えました。それが
    分かったのは検証を組んだあとでした。ここでは順序を逆にします。

    **リターンは1つも計算しません。** だから判定を消費しません。

    見るところは4つです。

      1. フィルタ後の件数  1,000 件に届くか。#1 の再現になっていないか
      2. 前日比の分布      値幅制限の検出は近似です。制限幅の階段表が効いて
                           いるなら山は少数の位置に立ちます。なだらかなら、
                           拾っているのは制限ではなく薄い銘柄です
      3. 売買代金の分布    フィルタを通ってなお小型に寄っていないか
      4. 執行              翌日も張り付いて**買えなかった**件数と、翌日始値の
                           ギャップ。ギャップの中央値が往復 0.6% を超えていたら、
                           0.6% のまま封印すると**実行できない合格**が出ます

    4 は費用の話だけではありません。買えなかった件は費用ではなく、**取れない**
    ということです。約定を仮定した検証は、それをそのまま「買えた」ことにします。

    過去の値幅制限の表は手元にないので、「高値＝安値・出来高あり・前日比プラス」
    で近似しています。**表を推測して書くと、当たっているか確かめる手段ごと
    失います。**

    全銘柄の日足を読むので数分かかります。

.PARAMETER MinTurnover
    流動性の下限（円）。既定は他の説と同じ1億円。

.EXAMPLE
    .\scripts\event-census.ps1
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

Write-Section '値幅制限・売買停止明けの件数（判定ではない）'
Write-Host 'リターンは計算しません。判定は消費しません。' -ForegroundColor Yellow
Write-Host '件数が足りなければ、そこで閉じられます。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai event-census --min-turnover $MinTurnover
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
