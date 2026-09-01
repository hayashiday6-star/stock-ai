<#
.SYNOPSIS
    J-Quants が開示について実際に何を返すかを、実物のレスポンスで確かめる。

.DESCRIPTION
    PEAD は「決算がいつ市場に出たか」で成否が決まります。引け後の開示なら
    翌日の寄りから、場中の開示ならその日のうちに価格が動きます。どちらか
    分からないまま実装すると、先読みになるか、反応日を1日取り違えるかの
    どちらかになります。

    いま手元のパーサは開示「日」しか読んでおらず、時刻を持つ列があるか
    どうかを誰も確かめていません。DBにも時刻の列がありません。この確認は
    その1点を、推測ではなく実データで確定させるためのものです。

    もう1つ、期限があります。5年分の開示履歴を取り直せるのは有料プランが
    ある間だけで、解約予定は 2026-09-22 です。Light プランに何が含まれるかを
    先に見ておかないと、事前登録を書いた後に取り返しがつかなくなります。

    値は出しません。出すのはレスポンスに現れたキーの名前と、値の「形」
    （時刻らしい、日付らしい、など）だけです。そのまま貼って差し支えない
    出力になります。

.PARAMETER Symbol
    調べる銘柄コード。既定は 7203（トヨタ自動車）。

.EXAMPLE
    .\scripts\jquants-disclosure-probe.ps1
    .\scripts\jquants-disclosure-probe.ps1 -Symbol 6758
#>
[CmdletBinding()]
param(
    [ValidatePattern('^\d{4}$')]
    [string]$Symbol = '7203'
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

if (-not (Test-EnvKeySet 'JQUANTS_API_KEY')) {
    Write-Section 'J-Quants disclosure probe'
    Write-Err '.env に JQUANTS_API_KEY がありません。'
    Write-Host ''
    Write-Host 'APIキー設定.bat で設定してから実行してください。'
    Exit-WithPause 1
}

Write-Section "J-Quants: 開示の列構成を実物で確かめる ($Symbol)"
Write-Host '決算エンドポイントと決算発表予定日エンドポイントを1回ずつ叩きます。' -ForegroundColor DarkGray
Write-Host ''

uv run python tools\jquants_disclosure_probe.py --symbol $Symbol
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。キー名と値の形だけで、中身は出ません。'
}

Exit-WithPause $code
