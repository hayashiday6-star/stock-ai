<#
.SYNOPSIS
    合成の利得 r を測る。**判定ではない。閾値は測る前に確定済み。**

.DESCRIPTION
    閾値（`docs/HYPOTHESES.md` に測る前から書いてある）:

      r ≥ 2.0        通過。財務抽出に進み、合成を1本だけ封印
      1.5 ≤ r < 2.0  曖昧域 → 打ち切り。4' へ。**再測定しない**
      r < 1.5        明確に不足。4' へ

    **曖昧域は意図して打ち切りに倒しています。**「過小評価だから財務系を足せば
    届くかもしれない」は測定後にしか使えない理屈で、それで測り直す形は
    「当てはまるまで測り方を変えること」と区別が付きません。

    r は「合成の t ÷ **最も良い**単一因子の t」です。最良を後から選ぶぶん
    分母が大きくなるので、**r は控えめに出ます。**

    最初に検算します。低ボラだけの盤面が #7 の判定（t +1.70）を再現しなければ、
    フィルタが揃っていないので**そこで止まります。**

    数分かかります。

.PARAMETER Start
    最初の月。既定 2014-01-01（#7 の判定期間に合わせる）。

.EXAMPLE
    .\scripts\composite-gain.ps1
#>
[CmdletBinding()]
param(
    [string]$Start = '2014-01-01'
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '合成の利得 r（判定ではない）'
Write-Host '閾値は測る前に確定済み。測定後に変更しません。' -ForegroundColor Yellow
Write-Host '曖昧域（1.5〜2.0）は打ち切り。財務系での再測定はしません。' -ForegroundColor Yellow
Write-Host '先に #7 の再現を検算します。揃っていなければそこで止まります。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai composite-gain --start $Start
$code = $LASTEXITCODE

if ($code -eq 2) {
    Write-Host ''
    Write-Err '検算が通りませんでした。フィルタが揃っていません。上の出力を貼ってください。'
}
elseif ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '表だけ貼ってください。当てはめはコードが済ませています。'
}

Exit-WithPause $code
