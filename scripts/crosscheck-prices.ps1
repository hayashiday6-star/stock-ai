<#
.SYNOPSIS
    立花と J-Quants を、重なる5年ぶんで日次に突き合わせる。

.DESCRIPTION
    **継ぎ目の検査は1日しか見ていません。** 2021-09-01 が普通の1日に見えた
    ことは、その日に段差が無いことしか言っていません。**5年ぶんの毎日が
    合っているかは、別の話です。**

      継ぎ目の検査   1日 × 76銘柄
      これ           5年 × 選んだ銘柄の全営業日

    **いましかできません。** 2026-09-22 に解約すると、片方が更新されなく
    なります。原本は残りますが、**2つの生きた経路が同じことを言うかを
    確かめる機会は無くなります。**

    生の終値から比べます。両者が同じ公式の値を見ているはずの、いちばん素の
    ところです。**ここが合わないなら、調整の話をしても意味がありません。**

    立花に問い合わせるので、銘柄数ぶん時間がかかります（既定12銘柄）。
    立花にあるのは現存銘柄だけなので、最新の名簿から等間隔に抜きます。

.PARAMETER Sample
    銘柄を指定しないときに抜く数。既定12。

.EXAMPLE
    .\scripts\crosscheck-prices.ps1
    .\scripts\crosscheck-prices.ps1 -Sample 30
#>
[CmdletBinding()]
param(
    [int]$Sample = 0,
    [string]$Dir = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

if (-not (Test-EnvKeySet 'TACHIBANA_AUTH_ID')) {
    Write-Section '立花と J-Quants の突き合わせ'
    Write-Err '.env に TACHIBANA_AUTH_ID がありません。'
    Write-Host ''
    Write-Host 'APIキー設定.bat で設定してから実行してください。'
    Exit-WithPause 1
}

Write-Section '立花と J-Quants を日次で突き合わせる'
Write-Host '重なる5年ぶんを、毎日ぶん比べます。' -ForegroundColor DarkGray
Write-Host '生の終値から見ます。ここが合わないなら調整の話は意味がありません。' -ForegroundColor DarkGray
Write-Host '立花に問い合わせるので、銘柄数ぶん時間がかかります。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'jquants-crosscheck')
if ($Sample -gt 0) { $arguments += @('--sample', "$Sample") }
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と最後の2行を貼ってください。'
}

Exit-WithPause $code
