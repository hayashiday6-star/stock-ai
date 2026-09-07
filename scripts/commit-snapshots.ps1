<#
.SYNOPSIS
    日付ごとの名簿を git に記録して、origin へ送る。

.DESCRIPTION
    名簿（data/universe_snapshots/ と data/tachibana_snapshots/）は、**解約
    後どこからも作り直せません。** 手元にあるだけの状態は、パソコンが1台
    壊れたら終わりです。DB は作り直せますが、これは作り直せません。

    **対象はその2つのフォルダだけです。** `git add -A` のように全部をまとめ
    て入れることはしません。関係のない編集や、うっかり置いたファイルまで
    一緒に送ってしまうのを避けるためです。

    順序は「記録 → 取り込み → 送信」です。**先に記録します**——取り込みで
    何かあっても、手元の作業が消えないようにするためです。

.PARAMETER DryRun
    何が対象になるかだけを出して、記録しない。

.EXAMPLE
    .\scripts\commit-snapshots.ps1 -DryRun
    .\scripts\commit-snapshots.ps1
#>
[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Err 'git が見つかりません。'
    Exit-WithPause 1
}

# 以降の git はどれも失敗しうる。native の失敗で throw させない。
$PSNativeCommandUseErrorActionPreference = $false

Write-Section '名簿を記録する'

$expected = 'claude/recent-activity-z1t0is'
$branch = (git rev-parse --abbrev-ref HEAD 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $branch -or $branch -eq 'HEAD') {
    Write-Err '今どの枝にいるのか分かりません。'
    Write-Host '  git status を実行して、出力を貼ってください。'
    Exit-WithPause 1
}
Write-Host "枝: $branch"
if ($branch -ne $expected) {
    Write-Warn "開発用の枝は $expected です。いまは $branch にいます。"
    Write-Host '  このまま記録すると、送信が弾かれます。先に枝を移ってください:' -ForegroundColor Yellow
    Write-Host "    git checkout -B $expected" -ForegroundColor Cyan
    Exit-WithPause 1
}

# **存在するフォルダだけを渡す。** git add は無いパスを渡すと fatal で
# 止まる。立花の月次名簿はまだ作っていない環境があるので、そこで毎回
# 止まることになる。
$targets = @(
    @('data/universe_snapshots', 'data/tachibana_snapshots') |
        Where-Object { Test-Path $_ }
)

if ($targets.Count -eq 0) {
    Write-Warn '名簿のフォルダがまだありません。'
    Write-Host '  checks\廃止銘柄の取り込み.bat を先に実行してください。' -ForegroundColor DarkGray
    Exit-WithPause 0
}

$pending = @(git status --porcelain -- $targets 2>$null)

if ($pending.Count -eq 0) {
    Write-Ok '記録していない名簿はありません。'
    Write-Host '手元の名簿は、すべて origin にも入っています。' -ForegroundColor DarkGray
    Exit-WithPause 0
}

Write-Host ''
Write-Host "記録していない名簿が $($pending.Count) 件あります。" -ForegroundColor Yellow
Write-Host '（先頭の記号: A=新しい M=変わった D=消えた ??=まだ git に無い）' -ForegroundColor DarkGray
Write-Host ''
$pending | Select-Object -First 20 | ForEach-Object { Write-Host "  $_" }
if ($pending.Count -gt 20) {
    Write-Host "  ... 他 $($pending.Count - 20) 件" -ForegroundColor DarkGray
}
Write-Host ''

if ($DryRun) {
    Write-Ok '下見なので、ここで止めます。何も記録していません。'
    Exit-WithPause 0
}

Write-Host '上の名簿を記録して、origin へ送ります。' -ForegroundColor DarkGray
Write-Host '対象はこの2つのフォルダだけで、他の編集には触れません。' -ForegroundColor DarkGray
$answer = Read-Host '進めますか (y/N)'
if ($answer -notmatch '^[yY]') {
    Write-Host '何もしていません。' -ForegroundColor DarkGray
    Exit-WithPause 0
}

Write-Host ''
git add -- $targets
if ($LASTEXITCODE -ne 0) {
    Write-Err '追加できませんでした。上の出力を貼ってください。'
    Exit-WithPause 1
}

$message = "名簿を $(Get-Date -Format 'yyyy-MM-dd') 時点まで記録する`n`n" +
    "解約後どこからも作り直せないデータ。手元にあるだけの状態にしない。"
git commit -m $message
if ($LASTEXITCODE -ne 0) {
    Write-Err '記録できませんでした。上の出力を貼ってください。'
    Exit-WithPause 1
}
Write-Ok '記録しました。'

Write-Host ''
Write-Host 'origin の最新を取り込みます...' -ForegroundColor DarkGray
git pull --rebase origin $expected
if ($LASTEXITCODE -ne 0) {
    Write-Err '取り込みで止まりました。**記録そのものは手元に残っています。**'
    Write-Host '  上の出力をそのまま貼ってください。' -ForegroundColor Yellow
    Exit-WithPause 1
}

Write-Host ''
Write-Host 'origin へ送ります...' -ForegroundColor DarkGray
git push -u origin $expected
if ($LASTEXITCODE -ne 0) {
    Write-Err '送れませんでした。**記録そのものは手元に残っています。**'
    Write-Host '  上の出力をそのまま貼ってください。' -ForegroundColor Yellow
    Exit-WithPause 1
}

Write-Host ''
Write-Ok '送りました。名簿はもう手元だけのものではありません。'
Exit-WithPause 0
