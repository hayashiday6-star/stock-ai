<#
.SYNOPSIS
    EDINET の書類本体を1件だけ取り、中身の形を観測する。

.DESCRIPTION
    いま我々が読んでいるのは documents.json――目録だけで、書類そのものは開いて
    いない。有価証券報告書の中身（売上・純利益・自己資本）を自前で持てば、
    J-Quants の有料プランを完全に外せる。立花では埋まらない最後の穴がここ。

    実装の前に確かめることが2つある。どちらも推測で書くと、例外を出さずに
    間違った財務データを持つことになる。

      1. documents/{docID} の type に何を渡すと何が返るのか。ZIP に入った
         XBRL なのか、集計済みの CSV なのか。CSV なら XBRL の構文解析が
         丸ごと不要になり、実装量が桁で変わる。
      2. 値がどの要素名・どの文脈に入っているのか。連結と個別、当期と前期は
         別の行として同居する。取り違えても数字は出る。

    書類本体は edinet_probe\ に保存する。中身は公開情報なので、そのまま貼って
    構わない。APIキーは指紋しか表示しない。

.PARAMETER SecCode
    証券コード。既定は 6501（日立製作所）。

.PARAMETER Days
    遡る日数。有価証券報告書は年1回なので既定は 400 日。

.EXAMPLE
    .\scripts\edinet-xbrl-probe.ps1
    .\scripts\edinet-xbrl-probe.ps1 -SecCode 7203
#>
[CmdletBinding()]
param(
    [ValidatePattern('^\d{4}$')]
    [string]$SecCode = '6501',
    [int]$Days = 400
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

if (-not (Test-EnvKeySet 'EDINET_API_KEY')) {
    Write-Section 'EDINET XBRL probe'
    Write-Err '.env に EDINET_API_KEY がありません。'
    Write-Host ''
    Write-Host 'EDINET確認.bat で鍵の状態を先に確かめてください。'
    Exit-WithPause 1
}

Write-Section "EDINET: 書類本体の形を観測する ($SecCode)"
Write-Host "有価証券報告書を直近 $Days 日から探します。日付を1日ずつ遡るので" -ForegroundColor DarkGray
Write-Host '少し時間がかかります。' -ForegroundColor DarkGray
Write-Host ''

uv run python tools\edinet_xbrl_probe.py --sec-code $SecCode --days $Days
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok 'edinet_probe フォルダの中身と、上の出力を貼ってください。'
}

Exit-WithPause $code
