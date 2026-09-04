<#
.SYNOPSIS
    低ボラ：封印前の検出力。平均は見ない。

.DESCRIPTION
    #6 と同じ手順です。判定に使わない期間（既定 2013年まで）の分散と自己共分散
    だけから、判定期間で「t≥2.0 に必要な差」を出します。平均は計算も表示も
    しません。-End が -OosFrom 以降だと拒否します。

    この説の売りは、窓が重ならないことです。#6 は毎営業日エントリーして20日
    持つ形だったので標準誤差が 2.95倍に膨らみました。月次リバランスなら
    重ならないので、膨張は 1 前後のはずです。**「はず」なので数字で確かめます。**

    費用のしきい値も仮定しません。センサスで分位1の月をまたぐ残存率が 88.5% と
    測れたので、毎月動くのは 11.5%。実効費用は 0.40% × 11.5% = 0.046%／月
    （年 0.55%）です。#6 の年 5.0% の9分の1になります。

.PARAMETER End
    分散を推定する最後の月。既定 2013-12-31。

.PARAMETER OosFrom
    判定に使う最初の月。既定 2014-01-01。

.EXAMPLE
    .\scripts\lowvol-power.ps1
    .\scripts\lowvol-power.ps1 -End 2015-12-31 -OosFrom 2016-01-01
#>
[CmdletBinding()]
param(
    [string]$End = '2013-12-31',
    [string]$OosFrom = '2014-01-01'
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '低ボラ：封印前の検出力'
Write-Host '分散だけを見ます。平均は計算も表示もしません。' -ForegroundColor DarkGray
Write-Host '判定期間を推定に混ぜないよう、コマンド側で拒否します。' -ForegroundColor DarkGray
Write-Host '重なりの膨張が 1 前後なら、この説を選んだ理由が数字で確かめられます。' -ForegroundColor DarkGray
Write-Host '数分かかります。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai lowvol-power --end $End --oos-from $OosFrom
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。これで封印できます。'
}

Exit-WithPause $code
