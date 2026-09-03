<#
.SYNOPSIS
    封印する前に「何%あれば有意になるか」を出す。平均は見ない。

.DESCRIPTION
    決算ドリフトの2本は、封印してから検出力が足りないと分かりました。SUE版では
    1分位あたり1〜2銘柄という薄さが、日次リターンの標準偏差 21.08% という異常な
    値になって初めて見えました。順序が逆でした。今回は先に出します。

    見るのは分散と自己共分散だけです。平均は計算もしませんし、出しません。
    分散の推定は仮説検定を消費しないので、判定に使わない期間（2020年まで）から、
    判定に使う期間の標準誤差を先に計算できます。

    -End は 2021-09-01 以降を受け付けません。判定期間を混ぜると、平均を見て
    いなくても「その期間なら何%出るか」を選べてしまうためです。

    重なりが要点です。毎営業日エントリーして20営業日持つと、隣り合う観測は
    19/20が同じ日を共有します。独立と見なすと標準誤差を4倍近く小さく見積もる
    ので、Newey-West（ラグ20）で長期分散に直します。

    全銘柄・全期間を走査するので、数分かかります。

.PARAMETER End
    分散を推定する最後の日。既定 2020-12-31。

.PARAMETER Start
    最初の日。既定は履歴の先頭から。

.PARAMETER OosDays
    OOSの想定営業日数。0 なら暦から数えます。

.EXAMPLE
    .\scripts\reversal-power.ps1
    .\scripts\reversal-power.ps1 -Start 2010-01-01
#>
[CmdletBinding()]
param(
    [string]$End = '2020-12-31',
    [string]$Start = '',
    [int]$OosDays = 0
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section 'リバーサル：封印前の検出力'
Write-Host '分散だけを見ます。平均は計算も表示もしません。' -ForegroundColor DarkGray
Write-Host '判定期間（2021-09以降）は混ぜません。混ぜると期間を選べてしまいます。' -ForegroundColor DarkGray
Write-Host '出るのは「どれだけ大きければ検出できるか」で、「どれだけ出るか」ではありません。' -ForegroundColor DarkGray
Write-Host '数分かかります。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'reversal-power', '--end', $End)
if ($Start) { $arguments += @('--start', $Start) }
if ($OosDays -gt 0) { $arguments += @('--oos-days', "$OosDays") }

uv @arguments
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '上の出力をそのまま貼ってください。判定期間の最終決定に使います。'
}

Exit-WithPause $code
