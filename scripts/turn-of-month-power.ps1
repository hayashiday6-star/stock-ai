<#
.SYNOPSIS
    #13「月替わり効果」の IS を測ります。**判定ではありません。**

.DESCRIPTION
    月末から月初の数日は、それ以外の日より上がるのか。窓は**月の最終営業日から
    翌月3営業日目まで**（4営業日）に固定してあります（事前登録 §3）。

    **売買しません。** 測るのは次の差だけです。

      1観測 = その月の窓の中の日次リターンの合計
            − 窓の中の日数 × 窓の外の平均日次リターン

    **費用は引きません**——売買していないので引くものがありません。その代わり
    §0 の線を、**将来売買するときの費用**から置いてあります（年 +2.4%）。

    **観測の単位は「月替わり1回」です。** 日ごとにすると観測数を水増しします
    （#5 で 1,827 件を 831 日と数え違えた形）。

    指数（`1306`）と等加重の宇宙の**両方**を出します。**線を当てるのは指数の
    ほう**です（事前登録 §2）。等加重は、効果が小型に偏っていないかを見るため。

    **IS は 2009-01〜2017-12 で、OOS（2018-01〜2026-08）には1日も触れません。**

    **表の「検出できる差」は暫定です。** この管の線をまだ測っていないので、
    月次の線を仮に当てています。**先に `research\暦の対照.bat` を回してください。**

    **API を1回も叩きません。**

.PARAMETER NoUniverse
    等加重の宇宙を読みません。**速くなります**（全銘柄の足を読まなくなる）。

.EXAMPLE
    .\scripts\turn-of-month-power.ps1
#>
[CmdletBinding()]
param(
    [switch]$NoUniverse
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '#13 月替わり効果 — IS を測る（判定ではない）'
Write-Host '窓は事前登録が固定しています。ここでは何も選びません。' -ForegroundColor Yellow
Write-Host 'OOS には1日も触れません。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'turn-of-month-power')
if ($NoUniverse) { $arguments += '--no-universe' }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '2つの表と、その下の数行を貼ってください。'
}

Exit-WithPause $code
