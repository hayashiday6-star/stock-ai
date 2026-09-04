<#
.SYNOPSIS
    立花関係のファイルを tachibana\ にまとめる。

.DESCRIPTION
    プロジェクト直下に tachibana_private.pem のようなファイルが4〜5個
    散らばっていて、どれが資格情報でどれが生成物か見分けが付かなくなって
    いました。1つのフォルダにまとめます。

      tachibana_private.pem  ->  tachibana\private.pem   ★秘密鍵
      tachibana_public.txt   ->  tachibana\public.txt
      tachibana_session.json ->  tachibana\session.json  ★当日の仮想ＵＲＬ
      tachibana_history.json ->  tachibana\history.json
      tachibana_master.json  ->  tachibana\master.json

    **消しません。移すだけです。** 移動先に同じ名前がある場合はそのまま
    残し、何もしません（上書きしません）。

    秘密鍵は特に注意が必要です。作り直すと立花に登録済みの公開鍵と合わなく
    なり、登録からやり直しになります。そのためコード側は、移す前でも旧い
    場所を読みに行くようにしてあります。急ぐ必要はありません。

.EXAMPLE
    .\scripts\tachibana-tidy.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

Write-Section '立花関係のファイルを tachibana\ にまとめる'
Write-Host '消しません。移すだけです。移動先に同名があれば何もしません。' -ForegroundColor DarkGray
Write-Host ''

$moves = @(
    @{ From = 'tachibana_private.pem';  To = 'tachibana\private.pem';  Note = '秘密鍵（資格情報）' }
    @{ From = 'tachibana_public.txt';   To = 'tachibana\public.txt';   Note = '登録用の公開鍵' }
    @{ From = 'tachibana_session.json'; To = 'tachibana\session.json'; Note = '当日の仮想ＵＲＬ（資格情報）' }
    @{ From = 'tachibana_history.json'; To = 'tachibana\history.json'; Note = 'プローブの生出力' }
    @{ From = 'tachibana_master.json';  To = 'tachibana\master.json';  Note = '銘柄マスタの生出力' }
)

if (-not (Test-Path 'tachibana')) {
    New-Item -ItemType Directory -Path 'tachibana' | Out-Null
    Write-Host 'tachibana\ を作りました。' -ForegroundColor DarkGray
}

$moved = 0
$already = 0
$missing = 0
foreach ($move in $moves) {
    if (-not (Test-Path $move.From)) {
        $missing++
        continue
    }
    if (Test-Path $move.To) {
        Write-Host ("  [そのまま] {0} … 移動先に既にあります" -f $move.From) -ForegroundColor Yellow
        $already++
        continue
    }
    Move-Item -Path $move.From -Destination $move.To
    Write-Host ("  [移動] {0} -> {1}  ({2})" -f $move.From, $move.To, $move.Note) -ForegroundColor Green
    $moved++
}

Write-Host ''
Write-Host ("移動 {0} 件 ／ 移動先に既存 {1} 件 ／ 直下に無し {2} 件" -f $moved, $already, $missing)

if (Test-Path 'tachibana\private.pem') {
    Write-Ok '秘密鍵は tachibana\private.pem にあります。立花への再登録は不要です。'
}
elseif (Test-Path 'tachibana_private.pem') {
    Write-Host ''
    Write-Host '秘密鍵がまだ直下にあります。コードはそちらも読むので動きます。' -ForegroundColor Yellow
}
else {
    Write-Host ''
    Write-Host '秘密鍵がどちらにもありません。まだ作っていない場合は' -ForegroundColor Yellow
    Write-Host 'checks\立花API確認.bat で作れます。' -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'このフォルダは README 以外すべて git 管理外です。' -ForegroundColor DarkGray

Exit-WithPause 0
