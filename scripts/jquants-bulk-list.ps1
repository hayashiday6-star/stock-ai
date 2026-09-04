<#
.SYNOPSIS
    一括ダウンロードで何が取れるのか、落とさずに見る。

.DESCRIPTION
    いまの財務取得は銘柄ごとに1リクエストです。3,700銘柄で 429 に当たって
    止まり、315銘柄は一度も取りに行けませんでした。

    J-Quants は `/fins/summary` と `/equities/bars/daily` を**一括ファイル**
    でも配っています。月1本の gzip CSV に全銘柄が入るので、リクエスト数は
    3,700回から数十回になります。解約前に取り切りたい会社予想（FSales/FOP/
    FNP/FEPS）と開示時刻（DiscTime）は `/fins/summary` に乗っています。

    **このスクリプトは1バイトも落としません。** 一覧を見るだけです。見るのは
    本数・覆っている期間・合計サイズの3つで、取り込みはその数字を見てから
    作ります。**粒度を推測して作ると、行が少ないまま黙って入ります。**

    専用の CLI は要りません。認証ヘッダが既存の取得経路と同じなので、いまの
    .env の JQUANTS_API_KEY でそのまま通ります。

.PARAMETER Endpoint
    /fins/summary のように1つだけ見るとき。既定は期限ものの2つ。

.PARAMETER All
    一括対応の全エンドポイントを見る。プランで開いていないものは理由が出る。

.EXAMPLE
    .\scripts\jquants-bulk-list.ps1
    .\scripts\jquants-bulk-list.ps1 -All
    .\scripts\jquants-bulk-list.ps1 -Endpoint /fins/summary
#>
[CmdletBinding()]
param(
    [string]$Endpoint = '',
    [switch]$All
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

if (-not (Test-EnvKeySet 'JQUANTS_API_KEY')) {
    Write-Section '一括ダウンロードの下見'
    Write-Err '.env に JQUANTS_API_KEY がありません。'
    Write-Host ''
    Write-Host 'APIキー設定.bat で設定してから実行してください。'
    Exit-WithPause 1
}

Write-Section '一括ダウンロードで何が取れるか（落とさない）'
Write-Host '1バイトも落としません。一覧を見るだけです。' -ForegroundColor DarkGray
Write-Host '本数・覆っている期間・合計サイズを見てから取り込みを作ります。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'jquants-bulk-list')
if ($Endpoint -ne '') { $arguments += @('--endpoint', $Endpoint) }
if ($All) { $arguments += '--all' }

uv @arguments
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。取り込みはこの数字から作ります。'
}

Exit-WithPause $code
