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

.PARAMETER DryRun
    写さずに、**何が写るかだけ**見ます。20年ぶんに伸びたとき、いきなり流す前
    に本数と大きさを確かめるためです。

.EXAMPLE
    .\scripts\archive-backup.ps1
    .\scripts\archive-backup.ps1 -To D:\backup\jquants_bulk
#>
[CmdletBinding()]
param(
    [string]$To = '',
    [string]$From = '',
    [switch]$DryRun
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

# **空きが分からなかったことを、黙って通さない。**
#
# 最初は Get-PSDrive だけを見ていて、失敗したら catch {} で握り潰していた。
# ネットワークパス（\\server\share\...）では GetPathRoot が `\\server\share`
# を返すので、ドライブ名として引けずに例外になる。**そのまま容量を確かめずに
# 写し始める。** 265MB なら入るので気付かない。20年ぶんの 1GB 超で、途中で
# 尽きたときに初めて分かる——それがいちばん困る形である。
$free = $null
try {
    $drive = Get-PSDrive -Name ([System.IO.Path]::GetPathRoot($full).TrimEnd(':\')) -ErrorAction Stop
    $free = $drive.Free
}
catch { }
if ($null -eq $free) {
    # ドライブ名で引けないとき（ネットワークパス等）は、パスそのものに聞く。
    try {
        $free = ([wmi]"Win32_Volume.DriveLetter='$([System.IO.Path]::GetPathRoot($full).TrimEnd('\'))'").FreeSpace
    }
    catch { }
}
if ($null -eq $free) {
    try {
        $info = New-Object System.IO.DriveInfo([System.IO.Path]::GetPathRoot($full))
        if ($info.IsReady) { $free = $info.AvailableFreeSpace }
    }
    catch { }
}

if ($null -ne $free) {
    Write-Host ("空き  : {0:N1} GB" -f ($free / 1GB))
    if ($free -lt $bytes) {
        Write-Err ("空きが足りません（要 {0:N1} GB）。" -f ($bytes / 1GB))
        Exit-WithPause 1
    }
}
else {
    Write-Warn '写し先の空きを調べられませんでした。**確かめずに進みます。**'
    Write-Host ("  要るのは {0:N1} GB です。足りるか自分で見てください。" -f ($bytes / 1GB))
    Write-Host '  途中で尽きると、写せた本数だけ増えて robocopy が 8 以上を返します。'
}

# **写し先に既に何があるかを数える。**
#
# これが無いと、robocopy が「386本ぜんぶ写る」と言ったときに、意味が2通りに
# 読めてしまう——写し先が空なのか、既にあるのに毎回写し直しているのか。
# 前者なら正常で、後者なら 20年ぶんの 1GB 超を毎回上げ直すことになる。
# **どちらも robocopy の出力は同じ形をしている。**
$thereFiles = 0
$thereBytes = 0
if (Test-Path $full) {
    $there = @(Get-ChildItem -Path $full -File -Recurse -ErrorAction SilentlyContinue)
    $thereFiles = $there.Count
    if ($thereFiles -gt 0) {
        $thereBytes = ($there | Measure-Object -Property Length -Sum).Sum
    }
}
Write-Host ("既にある: {0:N0} 本、{1:N1} MB" -f $thereFiles, ($thereBytes / 1MB))

function Show-CopyReading {
    <#
        .SYNOPSIS
            写し先の中身と、robocopy が写すと言った量を読み解く。
    #>
    param([int]$Already, [int]$Total)

    if ($Already -eq 0) {
        Write-Host '  写し先は空です。ぜんぶ写ると出るのが正しい姿です。' -ForegroundColor DarkGray
        return
    }
    if ($Already -ge $Total) {
        Write-Host '  写し先には既に同じだけのファイルがあります。' -ForegroundColor DarkGray
        Write-Host '  「スキップ」がその本数なら、正しい姿です。' -ForegroundColor DarkGray
        Write-Host '' -ForegroundColor DarkGray
        Write-Host '  スキップが 0 なら、写し先が元の更新時刻を保てていません。' -ForegroundColor DarkGray
        Write-Host '  /FFT（2秒単位）と /DST（1時間のずれ）を渡してもなお 0 なら、' -ForegroundColor DarkGray
        Write-Host '  ずれはそれより大きいということです。毎回ぜんぶ上げ直すので、' -ForegroundColor DarkGray
        Write-Host '  20年ぶんでは重くなります。' -ForegroundColor DarkGray
        return
    }
    Write-Host ('  差は {0:N0} 本です。増えたぶんだけ写るのが正しい姿です。' -f ($Total - $Already)) -ForegroundColor DarkGray
}

if ($DryRun) {
    Write-Host ''
    Write-Host '写さずに、何が写るかだけ見ます（robocopy /L）。' -ForegroundColor DarkGray
    robocopy $source $full /E /XO /FFT /DST /R:0 /W:0 /NP /NFL /NDL /L | Out-Host
    Write-Host ''
    Show-CopyReading -Already $thereFiles -Total $files.Count
    Write-Host ''
    Write-Ok '写していません。上の「コピー済み」が、次に写るぶんです。'
    Exit-WithPause 0
}

Write-Host ''
Write-Host '増えたぶんだけ写します。写し先のファイルは消しません。' -ForegroundColor DarkGray

# /E    空のフォルダも含めて再帰
# /XO   写し先のほうが新しければ飛ばす（＝増えたぶんだけ）
# /FFT  更新時刻を2秒単位で見る
# /DST  1時間のずれを吸収する
# /R:2  読めないファイルは2回まで試す
# /NP   進捗のパーセントを出さない（1行に収めるため）
# /NFL /NDL  ファイル名・フォルダ名を並べない
# **/MIR は使わない。** 写し先を打ち間違えたときに、そこにあるものを消す。
#
# **/FFT と /DST は実測から足した（2026-09-12）。** pCloud の写し先に 386本・
# 252.7MB が揃っているのに、robocopy は毎回その 386本すべてを写すと言った
# （スキップ 0）。本数もバイト数も一致しているので中身は届いていて、**合わない
# のは更新時刻だけ**である。クラウドや共有ドライブは元の時刻を秒単位までは
# 保たない。
#
# 252MB では気付かない。**20年ぶんでは毎回 1GB 超を上げ直す。**
robocopy $source $full /E /XO /FFT /DST /R:2 /W:2 /NP /NFL /NDL | Out-Host
$robo = $LASTEXITCODE

Show-CopyReading -Already $thereFiles -Total $files.Count

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
