<#
.SYNOPSIS
    保存した原本を目録と突き合わせる。1バイトも落とさない。

.DESCRIPTION
    **解約後こそ実行する意味があります。** そのとき欠けていると分かっても
    取り返せませんが、**欠けているのに揃っていると思って解析するよりは
    ましです。**

    見るのは3つです。

      消えている        ファイルが無くなった
      大きさが合わない  転送が途中で切れたまま残っている
      中身が変わった    大きさは同じで中身が違う（大きさだけ見ると素通りする）

    何も出なければ、目録どおり揃っています。

.PARAMETER Dir
    置き場所。既定は data/jquants_bulk。

.EXAMPLE
    .\scripts\jquants-archive-verify.ps1
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

Write-Section '原本の照合（落とさない）'
Write-Host '保存済みのファイルを目録と突き合わせるだけです。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'jquants-archive-verify')
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -eq 0) {
    Write-Ok '目録と一致しています。'
}
else {
    Write-Err '食い違いがあります。上の一覧をそのまま貼ってください。'
    Write-Host '契約中なら、保存をもう一度実行すると落とし直します。' -ForegroundColor DarkGray
}

Exit-WithPause $code
