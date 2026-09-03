<#
.SYNOPSIS
    立花から銘柄マスタを取り、J-Quants の銘柄マスタを置き換えられるか確かめる。

.DESCRIPTION
    v4r10 のマニュアルで要求形式が確定しました（2026-09-03）。要求は機能ＩＤ
    だけで、全銘柄が1回で返ります。

    取るのは2つです。

    - `CLMStkGetIssueMstKabu`（株式銘柄マスタ）
      銘柄名・上場発行株数・売買単位・**業種コード（33業種）**
    - `CLMStkGetIssueSizyouMstKabu`（株式銘柄市場マスタ）
      **上場区分（01プライム / 02スタンダード / 09グロース）**・
      新規上場日・**上場廃止日**・値幅・信用区分

    **マニュアルは仕様であって実測ではありません。** 項目名が書いてあっても、
    実際に値が入っているとは限りません。J-Quants では推測した項目名だけを
    並べて、決算期末日が1件も入らないまま気付きませんでした。同じ形を
    繰り返さないため、このプローブは**値が入っている件数と分布**を出します。

    リターンも価格も扱いません。銘柄コードと分類だけです。

.PARAMETER Fresh
    保存済みセッションを捨ててログインからやり直す。

.EXAMPLE
    .\scripts\tachibana-master.ps1
    .\scripts\tachibana-master.ps1 -Fresh
#>
[CmdletBinding()]
param(
    [switch]$Demo,
    [switch]$Fresh,
    [string]$PrivateKey = 'tachibana_private.pem'
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

if (-not (Test-Path $PrivateKey)) {
    Write-Err "秘密鍵が見つかりません: $PrivateKey"
    Write-Host ''
    Write-Host '先に 7-立花API確認.bat を実行して鍵を作り、登録してください。'
    Exit-WithPause 1
}

Write-Section '立花: 銘柄マスタが J-Quants の代わりになるか'
Write-Host '見るのは3つです。' -ForegroundColor DarkGray
Write-Host '  1. 上場区分にプライム/スタンダード/グロースが入っているか' -ForegroundColor DarkGray
Write-Host '  2. 業種コードが33業種で埋まっているか' -ForegroundColor DarkGray
Write-Host '  3. 上場廃止日が入っているか（J-Quants には無かったもの）' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'python', 'tools\tachibana_probe.py', 'master', '--private', $PrivateKey)
if ($Demo) { $arguments += '--demo' }
if ($Fresh) { $arguments += '--fresh' }

uv @arguments
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。件数と分布だけです。'
}

Exit-WithPause $code
