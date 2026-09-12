<#
.SYNOPSIS
    株価の無い銘柄の理由を、原本から決める。取得はしない。

.DESCRIPTION
    名簿には出ているのに株価が1本も無い銘柄が16件あります。**そのうち0件が
    出所の違いでした**（一括の名簿にも全部出ています）。残る説明は原本の中に
    しかありません。

    「株価が無い」には、少なくとも3つの理由がありえます。

      四本値に行が無い    一括の株価に載っていない銘柄である
      行はあるが終値が無い  上場しているが売買が成立していない
      終値もある          こちらの取り込みが落としている——**不具合**

    **件数だけでは、この3つが区別できません。** 上2つは取り直しても埋まり
    ませんが、3つ目は直すべき不具合です。

    銘柄を指定する必要はありません。棚卸しが数えている銘柄をそのまま調べます。

    原本を全部なめるので数分かかります。取得はしません。

.EXAMPLE
    .\scripts\symbol-probe.ps1
#>
[CmdletBinding()]
param(
    [string]$Dir = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '株価が無い理由を原本から決める（取得はしない）'
Write-Host '名簿にあって株価の無い銘柄を、原本まで降りて調べます。' -ForegroundColor DarkGray
Write-Host '原本を全部なめるので数分かかります。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'jquants-symbol-probe')
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と最後の1行を貼ってください。'
}

Exit-WithPause $code
