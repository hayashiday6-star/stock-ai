<#
.SYNOPSIS
    原本の写しを、外付けや同期フォルダに作る。照合まで通す。

.DESCRIPTION
    原本（data\jquants_bulk）は git で預かっていません。大きすぎるからです
    （Light で 265MB、Premium の20年で 1GB 超）。**預からない = 消えて
    よい、ではありません。** 2026-09-22 を過ぎるとどこからも取り直せません。

    ここでやるのは3つです。

      1. 写し先に、増えたファイルだけを写す（**消しません**）
      2. 目録も一緒に写る
      3. 写した先で照合し、欠けや壊れが無いことを確かめる

    3 が要点です。**写したつもりで写せていないのが、いちばん困ります。**
    目録が一緒に行くので、写し先だけを見て確かめられます。

    **写し先のファイルを消すことはしません。** robocopy の /MIR は使って
    いません。写し先を打ち間違えたときに、そこにあるものを消してしまう
    ためです。

.PARAMETER To
    写し先。省略すると聞きます。例: D:\backup\jquants_bulk

.PARAMETER From
    原本の置き場所。既定は data\jquants_bulk。

.EXAMPLE
    .\scripts\archive-backup.ps1
    .\scripts\archive-backup.ps1 -To D:\backup\jquants_bulk
#>
[CmdletBinding()]
param(
    [string]$To = '',
    [string]$From = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

$source = if ($From -ne '') { $From } else { Join-Path (Get-Location) 'data\jquants_bulk' }

Write-Section '原本の写しを作る'

if (-not (Test-Path $source)) {
    Write-Err "原本が見つかりません: $source"
    Write-Host '  先に checks\原本をまるごと保存.bat を実行してください。'
    Exit-WithPause 1
}

$files = @(Get-ChildItem -Path $source -File -Recurse)
$bytes = ($files | Measure-Object -Property Length -Sum).Sum
Write-Host ("元    : {0}" -f $source)
Write-Host ("中身  : {0:N0} 本、{1:N1} MB" -f $files.Count, ($bytes / 1MB))

if ($To -eq '') {
    Write-Host ''
    Write-Host '写し先を入れてください（外付けや同期フォルダ）。' -ForegroundColor DarkGray
    Write-Host '例: D:\backup\jquants_bulk' -ForegroundColor DarkGray
    Write-Host '無ければ作ります。写し先のファイルを消すことはしません。' -ForegroundColor DarkGray
    $To = (Read-Host '写し先').Trim('"').Trim()
}
if ($To -eq '') {
    Write-Host '写し先が空です。何もしていません。' -ForegroundColor DarkGray
    Exit-WithPause 0
}

$full = [System.IO.Path]::GetFullPath($To)
if ($full -eq [System.IO.Path]::GetFullPath($source)) {
    Write-Err '写し先が元と同じです。'
    Exit-WithPause 1
}

Write-Host ("写し先: {0}" -f $full)
$free = $null
try {
    $drive = Get-PSDrive -Name ([System.IO.Path]::GetPathRoot($full).TrimEnd(':\')) -ErrorAction Stop
    $free = $drive.Free
}
catch { }
if ($null -ne $free) {
    Write-Host ("空き  : {0:N1} GB" -f ($free / 1GB))
    if ($free -lt $bytes) {
        Write-Err '空きが足りません。'
        Exit-WithPause 1
    }
}

Write-Host ''
Write-Host '増えたぶんだけ写します。写し先のファイルは消しません。' -ForegroundColor DarkGray

# /E    空のフォルダも含めて再帰
# /XO   写し先のほうが新しければ飛ばす（＝増えたぶんだけ）
# /R:2  読めないファイルは2回まで試す
# /NP   進捗のパーセントを出さない（1行に収めるため）
# /NFL /NDL  ファイル名・フォルダ名を並べない
# **/MIR は使わない。** 写し先を打ち間違えたときに、そこにあるものを消す。
robocopy $source $full /E /XO /R:2 /W:2 /NP /NFL /NDL | Out-Host
$robo = $LASTEXITCODE

# **robocopy は成功でも 0 を返さない。**
#   0 = 写すものが無かった  1 = 写した  2 = 余分があった  4 = 食い違い
#   8 以上 = 失敗
# ここを `-ne 0` で見ると、写せた回が毎回「失敗」になる。
if ($robo -ge 8) {
    Write-Err "写せませんでした（robocopy $robo）。上の出力を貼ってください。"
    Exit-WithPause 1
}
Write-Ok ("写しました（robocopy {0}）。" -f $robo)

Write-Host ''
Write-Host '写した先を、目録と突き合わせます...' -ForegroundColor DarkGray
uv run stock-ai jquants-archive-verify --dir $full
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '写し先が目録と合いません。上の一覧を貼ってください。'
    Write-Host '  もう一度実行すると、足りないぶんを写します。' -ForegroundColor DarkGray
    Exit-WithPause 1
}

Write-Ok '写し先も目録と一致しています。原本はもう1台のディスクだけの話ではありません。'
Exit-WithPause 0
