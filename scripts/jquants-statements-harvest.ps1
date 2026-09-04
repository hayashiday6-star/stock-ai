<#
.SYNOPSIS
    会社予想と開示時刻を、解約前に取り切る。

.DESCRIPTION
    J-Quants の解約は 2026-09-22 です。会社の通期予想（FSales/FOP/FNP/FEPS）と
    開示時刻（DiscTime）は、**EDINET の有報には無く、解約後はどこからも増えません**。

    いま使う予定が無くても落としておく価値があります。**捨てる判断は後から
    できますが、取る判断は 9/22 までしかできません。**

    廃止銘柄を取り込んだので、DB には以前より多くの銘柄が入っています。この
    スクリプトは、**財務がまだ1行も無い銘柄だけ**を取りに行きます（既にある
    銘柄は要求もしません）。

    レート制限に当たったら、待つか止まるかします。以前のように残り全部を
    「失敗」として数え上げることはありません。**止まったら、しばらく置いて
    もう一度実行してください。** 取れた分は飛ばします。

    全銘柄だと30分以上かかることがあります。中断しても安全です。

.PARAMETER Segment
    prime | standard | growth | all | stored。既定は stored（DBにある銘柄すべて）。

.PARAMETER Limit
    銘柄数の上限。まず少数で試すときに使います。

.EXAMPLE
    .\scripts\jquants-statements-harvest.ps1 -Limit 20
    .\scripts\jquants-statements-harvest.ps1
#>
[CmdletBinding()]
param(
    [ValidateSet('prime', 'standard', 'growth', 'all', 'stored')]
    [string]$Segment = 'stored',
    [int]$Limit = 0
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

if (-not (Test-EnvKeySet 'JQUANTS_API_KEY')) {
    Write-Section '会社予想と開示時刻を取り切る'
    Write-Err '.env に JQUANTS_API_KEY がありません。'
    Write-Host ''
    Write-Host 'APIキー設定.bat で設定してから実行してください。'
    Exit-WithPause 1
}

Write-Section "会社予想と開示時刻を、解約前に取り切る ($Segment)"
Write-Host '解約後はどこからも増えません。取る判断は 9/22 まで。' -ForegroundColor Yellow
Write-Host '財務がまだ1行も無い銘柄だけを取りに行きます。' -ForegroundColor DarkGray
Write-Host 'レート制限に当たったら待つか止まります。止まったら時間を置いて再実行を。' -ForegroundColor DarkGray
Write-Host '30分以上かかることがあります。中断しても安全です。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @(
    'run', 'stock-ai', 'bulk-fetch',
    '--what', 'statements',
    '--statement-source', 'jquants',
    '--segment', $Segment
)
if ($Limit -gt 0) { $arguments += @('--limit', "$Limit") }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
Write-Section 'いま手元にある量'
uv run stock-ai jquants-inventory

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。「うち会社予想あり」が増えていれば進んでいます。'
}

Exit-WithPause $code
