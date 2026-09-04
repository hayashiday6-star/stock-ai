<#
.SYNOPSIS
    origin から最新を取り込み、増えた .bat を知らせる。

.DESCRIPTION
    押されたけれど手元に来ていない修正は、動かない修正と見分けが付かない。
    同じ行番号から同じ例外が出る。既存の .bat は起動時に「N コミット遅れて
    います」と言うが、それは**その .bat を既に持っている**場合の話で、新しく
    増えたファイルは自分の存在を知らせられない。checks\EDINET財務確認.bat が
    出てこなかったのがまさにそれ。

    そこでこれは、取り込んだ後に**増えた .bat の名前を並べる**。次に何を
    ダブルクリックすればいいかが、更新した画面にそのまま出る。

    早送りできるときだけ取り込む（--ff-only）。手元に編集が残っていたり枝が
    分かれていたりしたら、何も壊さずに止まって理由を言う。勝手に stash も
    reset もしない。
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Write-Section '最新にする'

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Err 'git が見つかりません。'
    Write-Host 'Git for Windows を入れてください: https://git-scm.com/download/win'
    Exit-WithPause 1
}

# 以降の git はどれも失敗しうる。native の失敗で throw させない。
$PSNativeCommandUseErrorActionPreference = $false

$branch = (git rev-parse --abbrev-ref HEAD 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $branch -or $branch -eq 'HEAD') {
    Write-Err '今どの枝にいるのか分かりません（detached HEAD かもしれません）。'
    Write-Host '  git status を実行して、出力を貼ってください。'
    Exit-WithPause 1
}

$before = (git rev-parse --short HEAD 2>$null)
Write-Host "枝      : $branch"
Write-Host "現在    : $before"

# 開発用の枝から降りていないかを、毎回いちばん最初に見る。
#
# **3回起きている。** main に乗ったままコミットして、push が
# 「non-fast-forward」で弾かれる。作業そのものは消えないが、なぜ弾かれたのか
# が分かりにくく、毎回同じところで止まる。CLAUDE.md にも書いてあるが、
# 書いてあるだけでは防げなかったので、ここで見る。
$expected = 'claude/recent-activity-z1t0is'
if ($branch -ne $expected) {
    Write-Host ''
    Write-Warn "開発用の枝は $expected です。いまは $branch にいます。"
    Write-Host '  このままコミットすると、push が non-fast-forward で弾かれます。' -ForegroundColor Yellow
    Write-Host '  作業が消えるわけではありませんが、毎回ここで止まります。' -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '  移るには（いまの変更やコミットはそのまま持っていけます）:' -ForegroundColor DarkGray
    Write-Host "    git checkout -B $expected" -ForegroundColor Cyan
    Write-Host ''
}

Write-Host ''

# 取り込む前の .bat 一覧。増えた分と減った分を後で差分で出す。
#
# **サブフォルダも見る。** .bat は自分の存在を知らせられないが、自分が
# 消えたことも知らせられない。整理でフォルダを移すと、手元から黙って
# 消えたように見える。相対パスで持つので、移動は「消えて増えた」と出る。
function Get-BatList {
    @(
        Get-ChildItem -Path . -Filter '*.bat' -File -Recurse |
            Where-Object { $_.FullName -notmatch '\\\.(git|venv)\\' } |
            ForEach-Object { Resolve-Path -Relative $_.FullName }
    )
}
$batsBefore = Get-BatList

$env:GIT_HTTP_LOW_SPEED_LIMIT = '1000'
$env:GIT_HTTP_LOW_SPEED_TIME = '20'
Write-Host 'origin を確認しています...' -ForegroundColor DarkGray
git fetch --quiet origin $branch 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Err "origin に届きませんでした（枝: $branch）。"
    Write-Host '  ネットワークを確認して、もう一度実行してください。'
    Exit-WithPause 1
}

$behind = (git rev-list --count "HEAD..origin/$branch" 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $behind) { $behind = '0' }

if ([int]$behind -eq 0) {
    Write-Ok '既に最新です。取り込むものはありません。'
    Write-Host ''
    Write-Host 'いま置いてある .bat:' -ForegroundColor DarkGray
    foreach ($name in ($batsBefore | Sort-Object)) { Write-Host "  $name" }
    Exit-WithPause 0
}

Write-Host "$behind 件の新しいコミットがあります:" -ForegroundColor Cyan
git log --oneline --no-decorate "HEAD..origin/$branch" 2>$null | ForEach-Object { Write-Host "  $_" }
Write-Host ''

# 早送りだけ。手元の編集を巻き込む取り込み方はしない。
git merge --ff-only "origin/$branch" 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Err '早送りで取り込めませんでした。何も変更していません。'
    Write-Host '  手元に編集が残っているか、枝が分かれています。'
    Write-Host '  git status の出力をそのまま貼ってください。'
    Exit-WithPause 1
}

$after = (git rev-parse --short HEAD 2>$null)
Write-Ok "$before -> $after に更新しました。"

# 依存が増えていることがある。ここを飛ばすと ModuleNotFoundError で気付くことになる。
if (Test-UvInstalled) {
    Write-Host ''
    Write-Host '依存を同期しています...' -ForegroundColor DarkGray
    uv sync --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Warn 'uv sync が失敗しました。上の出力を貼ってください。'
    }
    else {
        Write-Ok '依存も最新です。'
    }
}

$batsAfter = Get-BatList
$added = @($batsAfter | Where-Object { $batsBefore -notcontains $_ })
$gone = @($batsBefore | Where-Object { $batsAfter -notcontains $_ })

Write-Host ''
if ($added.Count -gt 0 -or $gone.Count -gt 0) {
    Write-Section '.bat の増減'
    foreach ($name in ($added | Sort-Object)) {
        Write-Host "  + $name" -ForegroundColor Green
    }
    foreach ($name in ($gone | Sort-Object)) {
        Write-Host "  - $name" -ForegroundColor Yellow
    }
    Write-Host ''
    if ($gone.Count -gt 0 -and $added.Count -gt 0) {
        Write-Host '同じ名前が + と - の両方にあれば、フォルダを移しただけです。'
    }
    Write-Host 'エクスプローラで見えているはずです。'
}
else {
    Write-Host '.bat の増減はありません（中身だけの更新です）。' -ForegroundColor DarkGray
}

Exit-WithPause 0
