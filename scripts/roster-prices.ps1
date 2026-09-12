<#
.SYNOPSIS
    名簿と四本値が、互いに整合しているかを見る。

.DESCRIPTION
    同じ原本から、2本の経路が出ています。

      名簿     /equities/master  → normalize_listings を通す
      四本値   /equities/bars/daily → 証券コードの変換しか通さない

    **2本は同じ絞り込みを通っていません。** だから差が出るのが普通で、
    件数を見ても何も分かりません。分かるのは**理由が言えるかどうか**です。

    見る向きを分けます。

      名簿にいて四本値の行が無い        → 警告。思い付く理由がありません
      名簿にいて終値が無く出来高も0     → 売買が無かった日。普通です
      名簿にいて終値が無いのに出来高あり → 警告。読み方が違います
      終値があって名簿にいない          → ETF・REIT・TOKYO PRO。理由が要ります

    **「だいたい説明が付く」で終わらせません。** 名簿の差 109 件を「たぶん
    市場が違うのだろう」で済ませかけて、実際には当時ではなく最新の市場を
    見ていた、というのが直前の失敗です。1件ずつ理由を付けて、理由の言えない
    ものを 0 にします。

    **API を1回も叩きません。** 解約後にも実行できます。原本を1周読むので、
    数分かかります。

.PARAMETER Limit
    四本値の原本を何本まで読むか。試すとき用。既定は全部。

.PARAMETER Show
    警告ごとに銘柄を何件まで並べるか。

.EXAMPLE
    .\scripts\roster-prices.ps1
    .\scripts\roster-prices.ps1 -Limit 2
#>
[CmdletBinding()]
param(
    [int]$Limit = 0,
    [int]$Show = 0,
    [string]$Dir = '',
    [string]$Rosters = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '名簿と四本値を突き合わせる'
Write-Host '同じ原本から出た2本が、互いに整合しているかを見ます。' -ForegroundColor DarkGray
Write-Host '差が出るのは普通です。見るのは「理由を1件ずつ言えるか」です。' -ForegroundColor DarkGray
Write-Host '原本を1周読むので数分かかります。API は叩きません。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'jquants-roster-prices')
if ($Limit -gt 0) { $arguments += @('--limit', "$Limit") }
if ($Show -gt 0) { $arguments += @('--show', "$Show") }
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }
if ($Rosters -ne '') { $arguments += @('--rosters', $Rosters) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と最後の1〜2行を貼ってください。'
}

Exit-WithPause $code
