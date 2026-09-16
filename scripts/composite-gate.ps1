<#
.SYNOPSIS
    #11「低ボラ × バリュー」の関門を当てる。**判定ではありません。**

.DESCRIPTION
    **複合型の1本目です。** 構成要素は #7 低ボラ（technical）と
    #10 バリュー（fundamental）で、**種類をまたぎます。**

    2026-09-05 に束ねた3本は3本とも technical でした。合成 t −0.14、最良の
    単独 +1.69 で符号が逆になり、因子探索はそこで閉じています。**その束には
    fundamental が1本も入っていませんでした。**

    **閉じた結末は動かしません。** ここで測るのは `composite-gain` の r では
    なく、複合型それ自体です。

    当てる関門は6つです（`docs/PURPOSE.md` の複合型ルール）。

      構成要素が登録済みか     登録の無い脚は、主張が文書に残らない
      IS で試す通り数          1通りと決めてコミット済み
      件数と流動性の内訳       PBR を要求すると断面が痩せる
      §0 検出可能性ゲート      見込みの下限が検出できる差を上回るか
      種類が1つに偏っていないか  偏っていても止めませんが、言います
      累計は1通り＝1本          構成要素の数ではありません

    **脚ごとの単独も同じ盤面から出ます。** 別々に組むと、比が「合成の利得」
    ではなく「universe の差」を含みます。**ただしそれは合格線であって、脚の
    判定ではありません。**

    **判定ではありません。** IS は 2009-01〜2017-12 で、OOS（2018-01〜2026-08、
    104ヶ月）には1日も触れません。

    月末の PBR ファイルが要ります。無ければ
    `checks\月末のPBRを抜き出す.bat` を先に実行してください。

    **API を1回も叩きません。** 全銘柄の日足を読むので数分かかります。

.PARAMETER Components
    構成要素。既定は #11 の登録どおり。**変えると別の1本になります。**

.PARAMETER Tries
    IS で試す通り数。**1 と決めてコミット済みです。増やすと封印できません。**

.EXAMPLE
    .\scripts\composite-gate.ps1
#>
[CmdletBinding()]
param(
    [string]$Components = 'LOWVOL_JP:低ボラ,VALUE_JP:バリュー',
    [int]$Tries = 1,
    [string]$IsEnd = '',
    [string]$Valuation = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '複合の関門（#11 低ボラ × バリュー。判定ではない）'
Write-Host 'OOS には1日も触れません。判定は封印してから1回だけです。' -ForegroundColor Yellow
Write-Host 'IS で試すのは1通りだけ、と測る前に決めてコミット済みです。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'composite-gate', '--components', $Components, '--tries', $Tries)
if ($IsEnd -ne '') { $arguments += @('--is-end', $IsEnd) }
if ($Valuation -ne '') { $arguments += @('--valuation', $Valuation) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '関門で止まりました。上の出力を貼ってください。'
}
else {
    Write-Ok '3つの表と、いちばん下の2行を貼ってください。'
}

Exit-WithPause $code
