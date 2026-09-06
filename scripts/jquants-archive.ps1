<#
.SYNOPSIS
    一括ファイルの原本を、展開せずにそのまま保存する。

.DESCRIPTION
    **取得は1回きり、解析は何度でもやり直せます。** 契約は 2026-09-22 で
    終わりますが、パーサの誤りは10月にも11月にも見つかります。原本が無けれ
    ば、そのとき取り返せません。

    名簿（data/universe_snapshots/）を CSV で残したのと同じ理屈です。DB は
    作り直せますが、解約後の J-Quants は作り直せません。

    返ってきたバイト列をそのまま書きます。展開もしませんし、CSV にも直しま
    せん。**読み方は後から変えたい側**だからです。

    途中で止めても、もう一度実行すれば続きから取ります。既にあって大きさの
    合うファイルは落としに行きません。**大きさが合わないものは落とし直しま
    す**——「ファイルがある」と「中身が揃っている」は別で、転送が途中で切れ
    ても例外が出ないことがあります。

.PARAMETER DryRun
    1バイトも落とさず、本数と合計サイズだけを出す。**先にこちらを回すこと。**
    何本・何MBかを知らずに始めない。日割り契約の日数もこの数字から決まる。

.PARAMETER Endpoint
    1つだけ落とすとき。既定は一括対応の全エンドポイント。

.PARAMETER Dir
    置き場所。既定は data/jquants_bulk。

.EXAMPLE
    .\scripts\jquants-archive.ps1 -DryRun
    .\scripts\jquants-archive.ps1
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$Endpoint = '',
    [string]$Dir = '',
    [double]$Throttle = 0.5
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

if (-not (Test-EnvKeySet 'JQUANTS_API_KEY')) {
    Write-Section '原本の保存'
    Write-Err '.env に JQUANTS_API_KEY がありません。'
    Write-Host ''
    Write-Host 'APIキー設定.bat で設定してから実行してください。'
    Exit-WithPause 1
}

if ($DryRun) {
    Write-Section '原本の下見（1バイトも落とさない）'
    Write-Host '本数と合計サイズだけを出します。契約日数はこの数字から決めます。' -ForegroundColor DarkGray
}
else {
    Write-Section '原本をそのまま保存する'
    Write-Host '展開せずに書きます。解析は後から何度でもやり直せます。' -ForegroundColor DarkGray
    Write-Host '途中で止めても、もう一度実行すれば続きから取ります。' -ForegroundColor DarkGray
}
Write-Host ''

$arguments = @('run', 'stock-ai', 'jquants-archive')
if ($DryRun) { $arguments += '--dry-run' }
if ($Endpoint -ne '') { $arguments += @('--endpoint', $Endpoint) }
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }
$arguments += @('--throttle', ([string]$Throttle))

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の表と警告を貼ってください。'
}
elseif ($DryRun) {
    Write-Ok '落としていません。上の表を貼ってください。'
    Write-Host '本数と合計 MB を見てから、保存を始めます。' -ForegroundColor DarkGray
}
else {
    Write-Ok '保存しました。上のまとめと警告を貼ってください。'
    Write-Host '「大きさが合わない」が出ていたら、もう一度実行すると落とし直します。' -ForegroundColor DarkGray
}

Exit-WithPause $code
