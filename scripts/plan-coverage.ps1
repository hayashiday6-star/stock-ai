<#
.SYNOPSIS
    プランを落としてよいかを、エンドポイントごとに数えて答える。

.DESCRIPTION
    **「たぶん全部取った」で解約しないためのものです。**

    公式の「プラン別 API 利用可否・データ取得範囲」と、原本の目録に実際に
    何本あるかを並べます。落とすと取れなくなり、しかも**手元に1本も無い**
    ものがあれば、それが止める理由になります。

    出すもの。

      エンドポイントごとの本数・大きさ・期間
      そのデータの最低必要プラン
      落とす先のプランで、まだ増やせるかどうか
      落とす前に取りに行く先（あれば）

    Free は特別です。取引カレンダーを除いて**一括ダウンロードが丸ごと使えず**、
    API で見える範囲も「12週間前〜2年12週間前」になります。プランの上下だけでは
    言い表せないので、そこは別に見ています。

    **API を1回も叩きません。** 数えるだけです。

.EXAMPLE
    .\scripts\plan-coverage.ps1

.EXAMPLE
    .\scripts\plan-coverage.ps1 -To Light
#>
[CmdletBinding()]
param(
    [ValidateSet('Free', 'Light', 'Standard', 'Premium')]
    [string]$To = 'Free',
    [string]$Dir = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section "解約してよいか（落とす先: $To）"
Write-Host '原本の在庫を、公式のプラン表に当てます。API は叩きません。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'jquants-plan-coverage', '--to', $To)
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と、そのあとの数行を貼ってください。'
}

Exit-WithPause $code
