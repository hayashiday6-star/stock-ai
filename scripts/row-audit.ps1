<#
.SYNOPSIS
    原本から読んだ行が、本当にデータベースに入ったかを数える（項目4）。

.DESCRIPTION
    **この形でしか閉じられません。**

    データベースは行ごとの出所を持っていません。`price_bars` に「立花から来た」
    「J-Quants から来た」の区別はありません。

    5年ぶん（2021-09〜）のときは困りませんでした。窓がまるごと J-Quants の覆う
    期間だったからです。**20年に伸ばしたら、立花の 2001年以降と重なりました。**
    差が「入らなかった行」なのか「立花の行」なのか、言えなくなりました。

    立花のマスタは現存銘柄しか返しません。**廃止銘柄の株価が DB にあれば、
    それは J-Quants から来たものに決まっています。** そこだけを数えます。

    出すもの。

      廃止銘柄の件数と、覆っている期間
      原本の行数 ／ DB の行数 ／ その差

    差が 0 なら、読んだ行は全部入っています。**項目4 はそこで閉じます。**

    **API を1回も叩きません。** 原本を全部読むので数分かかります。

.EXAMPLE
    .\scripts\row-audit.ps1
#>
[CmdletBinding()]
param(
    [string]$Dir = '',
    [string]$Rosters = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '原本の行は、全部データベースに入ったか'
Write-Host '廃止銘柄だけで数えます。立花の行が混ざらない集合です。' -ForegroundColor DarkGray
Write-Host 'API は叩きません。原本を全部読むので数分かかります。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'jquants-row-audit')
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }
if ($Rosters -ne '') { $arguments += @('--rosters', $Rosters) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '最後の2行を貼ってください。'
}

Exit-WithPause $code
