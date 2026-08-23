<#
.SYNOPSIS
    有報の「主要な経営指標等」を実物で読み、拾えた要素と拾えなかった要素を出す。

.DESCRIPTION
    パーサの要素名は日立（IFRS）の有報1本から起こしたもので、日本基準の会社で
    同じ名前が使われている確証がない。実際 NetSalesSummaryOfBusinessResults は
    日立の有報には1行も出てこなかった。IFRS 適用会社の CSV に入っている日本基準名
    の要素は提出会社「単体」の表なので、連結の日本基準名は別物かもしれない。

    黙って空欄が並ぶのが一番困る。5期ぶんの表が歯抜けで出てくるだけで、例外は
    出ない。だからこの確認は、表に出てくる要素名を全部並べて、パーサが見ている
    名前に印を付ける。印の付かない行が、埋めるべき穴。

    既定は 8306（三菱UFJフィナンシャル・グループ）。日本基準の代表として選んだ。
    別の会社でも構わない（9020 JR東日本など）。

.PARAMETER SecCode
    証券コード。既定は 8306。

.PARAMETER Days
    遡る日数。有価証券報告書は年1回なので既定は 400 日。

.EXAMPLE
    .\scripts\edinet-financials-check.ps1
    .\scripts\edinet-financials-check.ps1 -SecCode 9020
#>
[CmdletBinding()]
param(
    [ValidatePattern('^\d{4}$')]
    [string]$SecCode = '8306',
    [int]$Days = 400
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

if (-not (Test-EnvKeySet 'EDINET_API_KEY')) {
    Write-Section 'EDINET financials check'
    Write-Err '.env に EDINET_API_KEY がありません。'
    Write-Host ''
    Write-Host 'EDINET確認.bat で鍵の状態を先に確かめてください。'
    Exit-WithPause 1
}

Write-Section "EDINET: 有報の財務を実物で読む ($SecCode)"
Write-Host "有価証券報告書を直近 $Days 日から探します。日付を1日ずつ遡るので" -ForegroundColor DarkGray
Write-Host '少し時間がかかります。' -ForegroundColor DarkGray
Write-Host ''

uv run python tools\edinet_financials_check.py --sec-code $SecCode --days $Days
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。有報は公開情報です。'
}

Exit-WithPause $code
