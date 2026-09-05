<#
.SYNOPSIS
    推定量の校正：分位ソート 対 横断回帰。**判定ではない。**

.DESCRIPTION
    #6 と #7 は判定済みで閉じています。**ここで出る t を合否の主張に使いません。**
    測るのは推定量どうしの比だけで、しきい値との比較を1つも持ちません。
    （`docs/HYPOTHESES.md` に、回す前に宣言してあります。）

    **比べるのは t です。SD の比は取りません。** 分位スプレッドは断面が正規なら
    1σチルトの約2.8倍になります。推定量を変えると SD も効果も一緒に縮むので、
    SD 比だけ掛けたところに文献の分位スプレッドの効果量を当てると、**2.8倍の
    改善が無料で出たように見えます。** t は無次元なので、そうなりません。

    ロングショートどうしを比べます（分位1−分位5 対 1σチルト）。どちらも建玉の
    合計がゼロなので β を引く必要がありません。ロングオンリー側も出しますが、
    **β を引いていない**ので #7 の判定（t +1.70）とは直接比べられません。

    同じ月・同じ universe・同じ因子で、変えるのは推定量だけです。数分かかります。

.PARAMETER Start
    最初の月。既定 2014-01-01（#7 の判定期間に合わせる）。

.EXAMPLE
    .\scripts\lowvol-estimator.ps1
#>
[CmdletBinding()]
param(
    [string]$Start = '2014-01-01'
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '推定量の校正（判定ではない）'
Write-Host '#6 と #7 は閉じています。この数字を合否の主張に使いません。' -ForegroundColor Yellow
Write-Host '比べるのは t。SD の比は取りません。' -ForegroundColor DarkGray
Write-Host '同じ月・同じ universe・同じ因子で、推定量だけ変えます。' -ForegroundColor DarkGray
Write-Host ''

uv run stock-ai lowvol-estimator --start $Start
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
}
else {
    Write-Host ''
    Write-Ok '表だけ貼ってください。§0 ゲートに掛けるのはロングショートの t 比です。'
}

Exit-WithPause $code
