<#
.SYNOPSIS
    名簿の絞り込みが、20年に伸ばしても持つかを数える。

.DESCRIPTION
    絞り込みは `S33`（33業種）に寄りかかっています。**無ければ無条件に「会社」
    とみなします。** 符号の名前が変わったときに universe が空になるより、ETF が
    1つ紛れるほうが安いからで、そこは意図した設計です。

    **ただしそれは、`S33` がほぼ全部の行に在ることを前提にしています。** いま
    手元にあるのは5年ぶん（2021-09〜）で、そこで揃っていることは 2006年にも
    揃っていることを意味しません。

    起きうることは2つあり、**向きが逆です。**

      `S33` が空          ETF・REIT が「会社」として universe に入る
      公式にも無い符号    普通の会社が投信とみなされて**落ちる**

    **後者のほうが重いです。** 符号の体系が変われば、落ちるのは1社ではなく
    全部になりえます。どちらも例外は出ません。

    `9999`（その他）は公式の表に載っています。業種ではありませんが正式な符号
    なので、「公式にも無い符号」とは別に数えます。**混ぜると、投信の `9999` が
    数百件あるところに未知の符号が数件混じっても見えません。**

    `ProdCat`（商品区分）が受け皿になるかも見ます。**決め打ちしません。**
    落としている行と残している行で値が重なっていなければ使え、重なっていれば
    使えません。

    ここで出るのは**基準線**です。20年ぶんを取った日に同じコマンドを実行して
    比べます。**基準線が無ければ、20年ぶんの数字を見ても多いのか少ないのかが
    言えません。**

    **API を1回も叩きません。** 名簿の原本だけを読むので、数分で終わります。

.PARAMETER Show
    符号や銘柄を何件まで並べるか。

.PARAMETER Product
    その `ProdCat` の値だけを、銘柄数によらず名前つきで並べます。

    **既定でも、銘柄数が Show 以下の値は全部その場で名前まで出ます。** 値を
    1つずつ聞き直すと、そのたびに原本を1周読み直すことになるからです。
    これを渡すのは、銘柄の多い値（通常の株式など）を覗きたいときだけです。

.EXAMPLE
    .\scripts\filter-census.ps1
    .\scripts\filter-census.ps1 -Product 012
#>
[CmdletBinding()]
param(
    [int]$Show = 0,
    [string]$Product = '',
    [string]$Dir = ''
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

. "$PSScriptRoot\_common.ps1"

Show-Version

if (-not (Test-UvInstalled)) { Exit-WithPause 1 }

Write-Section '絞り込みは20年でも持つか'
Write-Host '`S33` が空の行と、公式にも無い符号を、年ごとに数えます。' -ForegroundColor DarkGray
Write-Host '向きが逆の2つなので、同じ数に混ぜません。' -ForegroundColor DarkGray
Write-Host '20年ぶんを取った日に比べるための基準線です。API は叩きません。' -ForegroundColor DarkGray
Write-Host ''

$arguments = @('run', 'stock-ai', 'jquants-filter-census')
if ($Show -gt 0) { $arguments += @('--show', "$Show") }
if ($Product -ne '') { $arguments += @('--product', $Product) }
if ($Dir -ne '') { $arguments += @('--dir', $Dir) }

uv @arguments
$code = $LASTEXITCODE

Write-Host ''
if ($code -ne 0) {
    Write-Err '最後まで通りませんでした。上の出力を貼ってください。'
}
else {
    Write-Ok '表2つと、最後の「基準線」の行を貼ってください。'
}

Exit-WithPause $code
