<#
.SYNOPSIS
    説 #9 の IS を測る。**§0 に入れる材料であって、判定ではありません。**

.DESCRIPTION
    説 #9「買いにくい相場は高い」を PBR で読み替えて測ります。**割安（PBR の
    低いほう）を買って割高（高いほう）を売ると、割安のままで終わるのか。**

    出るのは3つです。

      1期あたりのSD   検出できる差を決めます
      入れ替わり率     費用を決めます（往復 0.40%）
      効果の推定       封印するかどうかを決めます

    **判定ではありません。** 見ているのは 2009-01〜2017-12 の IS だけで、
    OOS（2018-01〜2026-08）には1日も触れません。判定は一度きりの資源なので、
    封印する前にここで見込みを立てます。

    **費用を引いてから出します。** 引く前の数字を見込みに置くと、実行できない
    大きさで §0 を通してしまいます。

    **線は測る前にコミット済みです。** 事前登録 `docs/PREREG_ANTIVALUE_JP.md`
    に「費用引き後で年 1.0% を下回ったら封印しない」と書いてあります。下回れば
    この画面がそう言います。**そのとき線は動かしません。**

    名簿（`data/universe_snapshots`）を必ず読みます。渡さないと、いま残って
    いる銘柄だけで測ることになり、生存バイアスが入ります。

    月末の PBR ファイルが要ります。無ければ
    `checks\月末のPBRを抜き出す.bat` を先に実行してください。

    **API を1回も叩きません。**

.PARAMETER IsEnd
    IS の最終日。既定は 2017-12-31。**動かすと OOS を削ることになります。**

.EXAMPLE
    .\scripts\antivalue-estimate.ps1
#>
[CmdletBinding()]
param(
    [string]$IsEnd = '',
    [string]$Valuation = '',
    [string]$Rosters = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '割安は割安のままか（IS の推定。判定ではない）'
Write-Host 'OOS には1日も触れません。判定は封印してから1回だけです。' -ForegroundColor Yellow
Write-Host '線（費用引き後で年 1.0%）は測る前にコミット済みです。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'antivalue-estimate')
if ($IsEnd -ne '') { $arguments += @('--is-end', $IsEnd) }
if ($Valuation -ne '') { $arguments += @('--valuation', $Valuation) }
if ($Rosters -ne '') { $arguments += @('--rosters', $Rosters) }

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
