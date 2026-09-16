<#
.SYNOPSIS
    月末の PBR だけを抜いて、1つのファイルに落とす。

.DESCRIPTION
    説 #9「買いにくい相場は高い」を検証するのに、月末の PBR が要ります。

    `pbr` は原本にしかなく、1,588万銘柄日を毎回読むと数分かかります。**必要
    なのは月末の1点だけ**なので、そこだけ抜いて小さなファイルにします。
    200ヶ月 × 約3,500銘柄で数 MB です。

    **暦の月末は探しません。** 月の途中で上場廃止になった銘柄は、その日が
    最後の観測です。暦で引くと**その銘柄がその月から丸ごと消え**、生存バイアス
    が入ります。

    **空の PBR は 0 で埋めません。** 落とした件数を出します。

    できるファイルは**生成物**です。手で直さないでください。原本から作り直せ
    ます。

    **API を1回も叩きません。** 229本を読むので数分かかります。

.EXAMPLE
    .\scripts\valuation-monthly.ps1
#>
[CmdletBinding()]
param(
    [string]$Dir = '',
    [string]$Into = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '月末の PBR を抜き出す'
Write-Host '説 #9 の材料です。API は叩きません。数分かかります。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'valuation-monthly')
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }
if ($Into -ne '') { $arguments += @('--into', $Into) }

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
