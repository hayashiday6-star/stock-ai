<#
.SYNOPSIS
    立花証券・ｅ支店・ＡＰＩの仕様を実機で確定する。

.DESCRIPTION
    本実装の前に、ログイン1回と株価取得1回だけを実際に通して確かめる。
    要求の組み立ては公式サンプル（e_api_sample_v4r9.py）に合わせてあり、
    POST・ShiftJIS・RSA-OAEP(SHA256)・p_no の採番・sJsonOfmt まで同じ。

    仮想ＵＲＬは復号できたことだけでは成功としない。http で始まることまで
    確かめる。鍵や方式を取り違えたまま先へ進むのを防ぐためである。

    手順は2段階だが、どちらを実行するかはこのスクリプトが判断する。
    秘密鍵が無ければ鍵ペアを作って止まり、あれば疎通確認に進む。
    ダブルクリックで起動しても、そのとき必要な方だけが動く。

    認証IDと仮想ＵＲＬは表示しない。ログイン応答に含まれる口座開設区分
    （ＮＩＳＡ・信用の有無など）も、仕様確定には不要なので読まない。

.PARAMETER Keygen
    秘密鍵が既にあっても、作り直す。既存のファイルは上書きせず中断するので、
    作り直すなら先に自分で消すか -PrivateKey で別名を指定する。

.PARAMETER Symbol
    試す銘柄コード。既定は 6501（日立製作所）。

.PARAMETER PrivateKey
    秘密鍵のパス。既定は tachibana_private.pem（.gitignore 済み）。

.PARAMETER Demo
    本番ではなく検証環境（demo-kabuka）へ接続する。デモ用の口座と認証IDが要る。
    利用できる時間帯が決まっている点に注意。

.PARAMETER UseGet
    POST ではなく GET で送る。サンプルの既定は POST で、通常は切り替え不要。
    片方だけ塞がれている環境かどうかを切り分けたいときに使う。

.PARAMETER Fresh
    保存済みの当日セッションを捨て、ログインからやり直す。仮想ＵＲＬは当日限り
    なので、2回目以降は既定で保存分を使い回してログインを重ねない。

.EXAMPLE
    .\scripts\tachibana-probe.ps1
    .\scripts\tachibana-probe.ps1 -Symbol 7203
    .\scripts\tachibana-probe.ps1 -Demo
    .\scripts\tachibana-probe.ps1 -Fresh
#>
[CmdletBinding()]
param(
    [switch]$Keygen,
    [ValidatePattern('^\d{4}$')]
    [string]$Symbol = '6501',
    [string]$PrivateKey = 'tachibana_private.pem',
    [switch]$Demo,
    [switch]$UseGet,
    [switch]$Fresh
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

$hasKey = Test-Path $PrivateKey

if ($Keygen -or -not $hasKey) {
    Write-Section 'Tachibana: 鍵ペアを作る'
    if (-not $hasKey) {
        Write-Host "秘密鍵 $PrivateKey が無いので、先に作ります。" -ForegroundColor Cyan
        Write-Host ''
    }
    uv run python tools\tachibana_probe.py keygen --private $PrivateKey
    if ($LASTEXITCODE -ne 0) { Exit-WithPause 1 }

    Write-Host ''
    Write-Host '--- 次にやること ---' -ForegroundColor Cyan
    Write-Host '  1. 上の公開鍵を、ｅ支店の API 利用設定画面に登録する'
    Write-Host '     （まず X.509 の方を試し、弾かれたら PKCS#1 の方）'
    Write-Host '  2. 同じ画面で認証IDを生成し、.env に次の行を書く'
    Write-Host '        TACHIBANA_AUTH_ID=（生成された値）'
    Write-Host '  3. このファイルをもう一度ダブルクリックする'
    Write-Host ''
    Write-Host "秘密鍵 $PrivateKey は .env と同じ扱いです。人に渡さないでください。" -ForegroundColor Yellow
    Write-Host '同じ扱いのファイルがもう1つ、実行後に tachibana_session.json として' -ForegroundColor Yellow
    Write-Host 'できます。その日の仮想URLが復号済みで入っています。' -ForegroundColor Yellow
    Exit-WithPause 0
}

if (-not (Test-EnvKeySet 'TACHIBANA_AUTH_ID')) {
    Write-Section 'Tachibana: probe'
    Write-Err '.env に TACHIBANA_AUTH_ID がありません。'
    Write-Host ''
    Write-Host 'ｅ支店の API 利用設定画面で認証IDを生成し、.env に次の行を'
    Write-Host '書いてから、もう一度実行してください。'
    Write-Host ''
    Write-Host '    TACHIBANA_AUTH_ID=（生成された値）'
    Write-Host ''
    Write-Host '同じ画面に公開鍵の登録も必要です。まだなら、いま作られている'
    Write-Host "$PrivateKey を消してから実行すると鍵作成からやり直せます。"
    Exit-WithPause 1
}

$label = if ($Demo) { "$Symbol / demo" } else { $Symbol }
Write-Section "Tachibana: probe ($label)"

$probeArgs = @('probe', '--symbol', $Symbol, '--private', $PrivateKey)
if ($Demo) { $probeArgs += '--demo' }
if ($UseGet) { $probeArgs += '--get' }
if ($Fresh) { $probeArgs += '--fresh' }

uv run python tools\tachibana_probe.py @probeArgs
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Err '最後まで通りませんでした。上の出力をそのまま貼ってください。'
    Write-Host '観測できたところまでが、そのまま次の一手を決める材料になります。'
}
else {
    Write-Host ''
    Write-Ok '疎通しました。tachibana_history.json も貼ってください。'
}

Exit-WithPause $code
