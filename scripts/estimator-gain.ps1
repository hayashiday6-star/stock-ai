<#
.SYNOPSIS
    共通因子（業種・規模）を抜いた利得 r を測る。**判定ではありません。**

.DESCRIPTION
    **推定量の校正です。説の合否ではありません。** 見るのは IS だけです。

    1つ目の校正（分位ソート → 横断回帰）は **0.93倍**で終わりました。効きません
    でした。**これが2つ目で、残っている最後の手です**——どの説も市場βしか
    引いていません。

    バリューや低ボラのスプレッドは大きな業種の賭けを抱えています。そこを抜けば
    残差の散らばりは下がります。**効果そのものが業種の賭けでなければ t は上がり、
    業種の賭けだったなら下がります。どちらに出ても答えです。**

    しきい値は測る前に確定済みです（`docs/HYPOTHESES.md`「2つ目の校正」）。

      r >= 1.4        動いた。新しい説の設計に使う
      1.2 <= r < 1.4  曖昧域 -> 打ち切り。**再測定しません**
      r < 1.2         動かない

    **1.4 の出どころは #7 です。** 必要 年4.0% に対し実測 年2.9% で、
    **1.38倍あれば足りていました。** それを上回る線として置いています。

    **t 比で測ります。SD 比ではありません。** 推定量を変えると効果も一緒に縮む
    ので、SD 比だと改善が無料で出たように見えます。

    **判定済みの説を測り直すのには使いません。** 答えを見た後で測り方を変える
    ことになります。

    **これが通っても #8（5.3倍要る）と #9（10倍要る）は戻りません。** 戻る見込み
    があるのは「月次・technical・効果が年3〜4%」という一角だけです。

    **API を1回も叩きません。** 全銘柄の日足を読むので数分かかります。

.PARAMETER Factor
    校正に使う因子。既定は低ボラ（#7 と同じ形で比べられます）。

.EXAMPLE
    .\scripts\estimator-gain.ps1
#>
[CmdletBinding()]
param(
    [string]$Factor = '低ボラ',
    [string]$IsStart = '',
    [string]$IsEnd = '',
    [string]$Valuation = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '共通因子を抜いた利得 r（判定ではない）'
Write-Host 'しきい値は測る前に確定済み。測定後に変更しません。' -ForegroundColor Yellow
Write-Host '曖昧域（1.2〜1.4）は打ち切り。別の因子での再測定はしません。' -ForegroundColor Yellow
Write-Host ''

$arguments = @('run', 'stock-ai', 'estimator-gain', '--factor', $Factor)
if ($IsStart -ne '') { $arguments += @('--is-start', $IsStart) }
if ($IsEnd -ne '') { $arguments += @('--is-end', $IsEnd) }
if ($Valuation -ne '') { $arguments += @('--valuation', $Valuation) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表と、その下の2〜3行を貼ってください。'
}

Exit-WithPause $code
