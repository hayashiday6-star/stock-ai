<#
.SYNOPSIS
    J-Quants から財務を取り直し、開示時刻（DiscTime）を埋める。

.DESCRIPTION
    開示時刻は J-Quants の fins/summary にしかありません。EDINET にはなく、
    いまの JP_STATEMENT_SOURCE は edinet です。そのため通常の
    3-データ取得.bat では、この列は永久に埋まりません。

    このスクリプトは .env を書き換えずに、この1回だけ取得元を jquants に
    します。設定値のつもりで APIキーを上書きする事故を避けるためです。

    もう1つ、--no-resume を付けます。再開の判定は「その銘柄の行があるか」
    だけを見るので、列を新しく足しても行が既にあれば飛ばされます。実際、
    列を足した直後の取り直しは「0 ok, 20 skipped」で成功したように見えて
    1件も取れていませんでした。

    既存の行はいったん消してから入れ直します（--replace）。upsert は鍵が
    一致する行しか置き換えないので、鍵の意味が変わったり5年ローリング窓が
    ずれたりすると古い行が残ります。実測で 7203 は API が返す20件に対し
    DBに21行ありました。数える側から見れば、存在しない開示が1件増えている
    のと同じです。消してから入れ直せば、DBの中身は「いまAPIが返すもの」と
    一致します。

    期限があります。5年分の開示履歴を取れるのは有料プランがある間だけで、
    解約予定は 2026-09-22 です。それ以降は取り直せません。

    全銘柄で30分以上かかります。中断しても安全で、--no-resume なので
    再実行すると最初からやり直します。

.PARAMETER Segment
    prime | standard | growth | all | stored。既定は stored（DBにある銘柄）。

.PARAMETER Limit
    銘柄数の上限。まず少数で試すときに使います。

.EXAMPLE
    .\scripts\jquants-statements-backfill.ps1 -Limit 20
    .\scripts\jquants-statements-backfill.ps1
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
    Write-Section 'J-Quants statements backfill'
    Write-Err '.env に JQUANTS_API_KEY がありません。'
    Write-Host ''
    Write-Host 'APIキー設定.bat で設定してから実行してください。'
    Exit-WithPause 1
}

Write-Section "J-Quants: 財務を取り直して開示時刻を埋める ($Segment)"
Write-Host '.env は書き換えません。この1回だけ取得元を jquants にします。' -ForegroundColor DarkGray
Write-Host '既存の財務行はいったん消してから入れ直します。価格には触れません。' -ForegroundColor DarkGray
Write-Host '全銘柄なら30分以上かかります。中断しても安全です。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @(
    'run', 'stock-ai', 'bulk-fetch',
    '--what', 'statements',
    '--statement-source', 'jquants',
    '--segment', $Segment,
    '--no-resume',
    '--replace'
)
if ($Limit -gt 0) { $arguments += @('--limit', "$Limit") }

uv @arguments
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '終わりました。PEAD件数センサス.bat で開示時刻の内訳を確認してください。'
}

Exit-WithPause $code
