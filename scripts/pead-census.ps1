<#
.SYNOPSIS
    PEAD（決算後ドリフト）を事前登録する前に、測れるかどうかを先に数える。

.DESCRIPTION
    前回のアキュムレーション検証は、事前登録を書き上げてから実装し、最後に
    件数を測って「検証不能」で終わりました。原因は仮説ではなく、現象が起きて
    いる場所と、手元のデータが届く場所が交わっていなかったことです。

    同じ順序を繰り返さないための下調べです。事前登録を書く「前」に走らせて、
    次の3つを事実として確定させます。

      1. 開示イベントが年に何件あるか（銘柄数・ユニーク開示日数つき）
      2. 流動性で絞っても残るか。「年数千件ある」は市場についての主張で
         あって、手元のDBについての主張ではありません
      3. リターン窓が実際に取れるか。D+1 で入り D+61 で出られない開示は、
         件数に数えても検証には使えません

    リターンは一切計算しません。ネットワークにも出ません。手元のDBを
    数えるだけです。

.PARAMETER Symbols
    調べる銘柄コードをカンマ区切りで。省略すると全銘柄。1銘柄だけ指定すると、
    10-JQuants開示確認.bat が出す「レコード数」と直に突き合わせられる。
    APIが返す件数とDBの行数が食い違えば、取り込み側で落ちている。

.EXAMPLE
    .\scripts\pead-census.ps1
    .\scripts\pead-census.ps1 -Symbols 7203
#>
[CmdletBinding()]
param(
    [string]$Symbols
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section 'PEAD: 事前登録の前に件数を数える'
Write-Host '全銘柄の価格と開示を突き合わせます。数分かかります。' -ForegroundColor DarkGray
Write-Host 'リターンは計算しません。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'pead-census')
if ($Symbols) { $arguments += $Symbols.Split(',') | ForEach-Object { $_.Trim() } }

uv @arguments
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。件数だけで、銘柄名は出ません。'
}

Exit-WithPause $code
