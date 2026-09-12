<#
.SYNOPSIS
    取引カレンダーに半日立会が実在するかを数える。

.DESCRIPTION
    コードには「営業日を `HolDiv=1` かどうかで判定すると、半日立会が非営業日に
    落ちる。年に数日なので件数からは気付けない」と書いてあります。

    **備えとしては正しいのですが、その区分が手元のデータに1日も無ければ、
    効いていません。** 書いてあることと、在ることは別です。数えます。

    出すもの。

      区分ごとの日数          `1`/`2`/`0`/`3` を分けて数えます
      半日立会の日付          年ごとに並べます
      `1` だけで絞ると何日落ちるか   備えの値段です
      一覧に無い区分          あれば区分が増えています

    名簿のある日とも突き合わせます。**カレンダーは「立会がある」と言っている
    だけで、本当にその日のデータが在るかは別のファイルが知っています。**
    名簿は `/equities/master` から出ていて、カレンダーとは別の原本です。

    **API を1回も叩きません。** すぐ終わります。

.EXAMPLE
    .\scripts\calendar-census.ps1
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

Write-Section '取引カレンダーに半日立会は在るか'
Write-Host '区分ごとに数えて、半日立会の日付を並べます。' -ForegroundColor DarkGray
Write-Host '名簿のある日とも突き合わせます。API は叩きません。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'jquants-calendar')
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }
if ($Rosters -ne '') { $arguments += @('--rosters', $Rosters) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表2つと最後の数行を貼ってください。'
}

Exit-WithPause $code
