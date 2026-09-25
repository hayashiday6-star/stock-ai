"""**壁の下見** — 事前登録を書く前に、検出できる差だけを出す。

## なぜ要るか

**7本続けて §0 で閉じている。** 説が悪いからではない——**設計の散らばりと
観測数で、壁の高さが決まっている。**

既に測った形の壁は `docs/PASSING.md` に出ている（**生成物である。ここに
書き写さない**——線が動けば全部動く）。月次の分位ロングショートは年20%台で、
**実在するアノマリーでその大きさのものは、まず無い。**

それなら、事前登録を書く前に**壁の高さのほうを先に見る**ほうが安い。

## なぜこれが「答えを先に見る」ことにならないか

検出できる差は **`線 × SD ÷ √n`** で、**散らばりと観測数だけで決まる。**
効果（平均）は式に入らない。

**だから `Wall` に平均の欄を作っていない。** `PowerEstimate` が平均を持たない
のと同じ理由である——**入れられる形にすると、「効果がありそうだから通す」が
書けてしまう。** `tests/test_wall.py` が、この module が平均を計算していない
ことを機械的に確かめる。

**設計を検出力で選ぶのは、答えを見て選ぶのとは違う。** #10 が封印できなく
なったのは、**IS の `t`（＝効果を見た数字）が設計で 2.8倍振れた**からである。
ここで比べるのは壁の高さだけで、**どの設計が勝っているかは分からない。**

## 何を測り、何を測らないか

**IS（2009-01〜2017-12）のリターンだけを読む。** OOS のリターンは1つも計算
しない。**ただしイベントの件数は OOS も数える**——判定に使える観測数 `n` が
そこで決まるためで、**件数は効果ではない**（#5 の件数センサスと同じ扱い）。

**イベント型の窓は 20営業日に固定した**（#5 と同じ）。**壁を同じ物差しで
並べるため**であって、事前登録がそれを選ぶという意味ではない。窓を変えれば
壁も動くので、**そのときは測り直す。**

## 候補A・B の畳み方は、壁を測る前に1つに決めてある

**複数試して良いほうを採ると、その時点で #10 と同じところに落ちる。**
だから**ここに書いてから測る。**

| | 候補A（候補6 の一部） | 候補B（候補7） |
|---|---|---|
| 材料 | オプションの ATM 予想変動率 | 投資部門別（`TokyoNagoya`） |
| 事象 | 前日比が :data:`IV_SPIKE_LADDER` の線を超える | その週が**買い越し** |
| 入る日 | その**翌営業日** | **公表日の翌営業日** |
| 保有 | :data:`HOLDING` 営業日 | :data:`FLOW_HOLDING` 営業日 |
| 1観測 | 1イベント日 | 1公表 |
| IS/OOS | :data:`IV_IS_FROM`〜:data:`IV_IS_END` / :data:`IV_OOS_FROM`〜 | 暦どおり |
| 引く相手 | **無い**（指数を買うだけ） | 同左 |
| 管 | `calendar` | `calendar` |

**管は `calendar` である。** 校正したのは日次の月替わりだが（`multiplicity`）、
**指数の日次リターンを規則で選んで束ねる**という形は同じである。**別の形で
校正した値を当てていることは、そう書いておく**——`docs/PASSING.md` が月次の
線を全部の形に当てていた件と同じ型を避けるため。

**梯子と「買い越し」に出典は無い。** 決めの値である。文献から取ったのでは
ない——**そう書いておく**（`CLAUDE.md`「出典の無い数字を書かない」）。

## 候補12・13・18・19 の畳み方も、測る前に1つに決めた（2026-09-21）

**番号は 12 から続けた。** ユーザーの挙げた10本は 1〜10 と振られていたが、
**`docs/CANDIDATES.md` の 1〜11 は別の説である。** 番号を詰めたり振り直したり
すると、**過去の会話と突き合わせられなくなる**（`docs/CANDIDATES.md`「順位の 1 は
空けてある」と同じ理由）。

| | 12 小型株 | 13 低位株 | 19 節目の株価 | 18 掉尾の一振 |
|---|---|---|---|---|
| 材料 | `market_cap`（月末） | 終値（月末） | 終値（月末） | 指数の日次 |
| 並べ方 | 小さい順 | 安い順 | :func:`round_number_position` | — |
| 事象 | 無し（毎月） | 無し（毎月） | 無し（毎月） | **12月末の :data:`TAIL_SESSIONS` 営業日** |
| 1観測 | 1ヶ月 | 1ヶ月 | 1ヶ月 | **1年** |
| 引く相手 | 分位ロングショート | 同左 | 同左 | **無い**（指数を買うだけ） |
| 管 | `monthly` | `monthly` | `monthly` | **自由度で引く**（#14 と同じ） |

**分位ロングショートで組んでいる。** ユーザーは「ロングのみ・α で組め」と
言ったが、**それは要るリターンを下げるだけで、要る情報比を下げない**
（`docs/PASSING.md` §2）。**壁を見るのが目的なので、既に測った4本と同じ形に
揃えるほうが比べられる。**

**重なりは先に片付けた。**

| 挙がっていた説 | どうしたか |
|---|---|
| 大型株を避ける | **12 の裏。落とした**——同じ設計が2行在ると、
  後で良いほうを選んだのと区別が付かない |
| 貸借倍率の低い銘柄 | **15 と同じ原本の別読み。落とした** |
| 12 と 13 | **どちらも測る。** 重なるかどうかは :func:`signal_overlap` が出す |

**節目の畳み方に出典は無い。** :data:`TAIL_SESSIONS` も同じである。決めの値
であって、文献から取ったのではない。

**線は梯子から1つに決まる**——`power.sample_needed(holding)` を満たす中で
いちばん厳しいもの（:func:`choose_spike`）。**効果は1つも見ない。**
選ぶのは観測数だけである。

**候補11 を足した**（2026-09-21）。候補7 の絞りをやめた形で、**常に市場に
居て直近の公表の符号で向きを切り替える。** ±1 倍は SD を変えないので、
**壁は指数の週次の散らばりと週数だけで決まる。**

**引く相手が無いので、壁は「指数そのものの散らばり」で決まる。** イベント型
（等加重の宇宙を引く）とは別の量である。**並べても、どちらが良い設計かは
分からない。**
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
from bisect import bisect_right
from collections.abc import Callable, Iterable
from pathlib import Path

import numpy as np
import pandas as pd

from stock_ai.backtest.discontinuity import crossings, session_breaks, spans_break
from stock_ai.backtest.gap_fill import GAP_DOWN as _GAP_DOWN
from stock_ai.backtest.gap_fill import gap_positions, liquid_bars
from stock_ai.backtest.knife import KNIFE_DAYS as _KNIFE_DAYS
from stock_ai.backtest.knife import KNIFE_DROP as _KNIFE_DROP
from stock_ai.backtest.knife import knife_positions
from stock_ai.backtest.lowvol_census import formation_dates
from stock_ai.backtest.pead import MIN_TURNOVER
from stock_ai.core.logging import get_logger
from stock_ai.data.schema import CLOSE, OPEN, VOLUME, split_adjusted
from stock_ai.database.engine import Database

logger = get_logger(__name__)

#: IS の終わり。**ここまでのリターンしか読まない。**
IS_END = dt.date(2017, 12, 31)

#: OOS の始まり。**件数はここも数える。リターンは計算しない。**
OOS_FROM = dt.date(2018, 1, 1)

#: OOS の終わり。
OOS_END = dt.date(2026, 8, 31)

#: イベント型の保有営業日数。**#5 と同じ。** 壁を同じ物差しで並べるため。
HOLDING = 20

#: 52週高値を測る営業日数。**52週 ≒ 250営業日。** 暦から出した値である。
HIGH_WINDOW = 250

#: 下窓と呼ぶ幅。**正本は `gap_fill.GAP_DOWN` にある。**
#:
#: #15 が同じ規則を使う。**2つ持つと、下見で選んだ設計と判定に使う設計が、
#: 黙ってずれる。** ここは名前を残すためだけである。
GAP_DOWN = _GAP_DOWN

#: 急落と呼ぶ幅と日数。**正本は `knife` にある。**
#:
#: #16 が同じ規則を使う。**2つ持つと、下見で選んだ設計と判定に使う設計が、
#: 黙ってずれる。** ここは名前を残すためだけである。
KNIFE_DROP = _KNIFE_DROP
KNIFE_DAYS = _KNIFE_DAYS

#: 予想変動率が跳ねたと呼ぶ幅。**前日比。候補A（候補6）で使う。**
#:
#: **壁を測る前に1つに決めた。** 複数試して良いほうを採ると、その時点で
#: #10 と同じところに落ちる（`docs/POSTMORTEMS.md`「#10 が封印できなくなったのは
#: 効果を見て設計を選べる形だったから」）。
#:
#: **出典は無い。** 「1日で2割上がったら跳ねたと呼ぶ」という、決めの値で
#: ある。文献から取ったのではない——**そう書いておく。**
IV_SPIKE = 0.20

#: 予想変動率の跳ねを、どこまで下げてよいか。**梯子を先に固定する。**
#:
#: **n ≥ `power.sample_needed(lags)` を満たす中で、いちばん厳しい線を採る**
#: （2026-09-21 にコミットした）。**効果は1つも見ない**——選ぶのは観測数だけ
#: である。`docs/WALL.md` の冒頭が許しているのはそこまでで、**そのつど効果を
#: 見れば #10 と同じところに落ちる。**
#:
#: **順に試して良いほうを採るのではない。** 上から見て**最初に条件を満たした
#: ものを採る**ので、答えは1つに決まる。
IV_SPIKE_LADDER: tuple[float, ...] = (0.20, 0.15, 0.10, 0.075, 0.05)

#: 候補6 の IS の初日。**`IV` が原本に在る最初の日である**（実測)。
#:
#: 2016-07-19。それより前は `IV` / `BaseVol` / `UnderPx` が全行で空
#: （`checks\候補6と7の材料は在るか.bat` が年ごとに数える）。
IV_IS_FROM = dt.date(2016, 7, 19)

#: 候補6 の IS の最終日。**この候補だけ暦の :data:`IS_END` を使わない。**
#:
#: **2026-09-21 に決めてコミットした。** 理由は算数である——暦の 2017-12-31
#: で切ると `IV` が在るのは **359 日**しかなく、全部が事象でも 339 窓。
#: `lags=20` の膨張に要る **200** を満たすには事象が6割の日に出る必要があり、
#: **それは事象ではない。** どの線を選んでも膨張が推定できない。
#:
#: **`IV` が在る期間の最初の3年**を IS にする。代償は OOS が 8.7年 → 7.2年。
#: **他の説と並べるときは「別の窓」と書くこと。**
IV_IS_END = dt.date(2019, 7, 18)

#: 候補6 の OOS の初日。**IS の翌日。**
IV_OOS_FROM = dt.date(2019, 7, 19)

#: 掉尾の一振（候補18）で買う、12月の最後の営業日数。
#:
#: **出典は無い。決めの値である**（`docs/POSTMORTEMS.md`「幅を決め打つときは、その
#: 数字がどこから来たかを書く」）。格言は「年末に一相場ある」としか言わない。
#:
#: **年に1観測しか作れない。** #14（1月効果）と同じ形なので、線も同じく
#: `multiplicity.student_t_line` で自由度から引く——**`t` の SD から線を
#: 引いてよいのは観測が多いときだけ**である。
TAIL_SESSIONS = 5

#: 需給の週で保有する営業日数。**候補B（候補7）で使う。**
#:
#: **1週である。** 需給は週に1回しか更新されないので、20営業日にすると
#: 1つの窓に4回ぶんの合図が入る。**イベント型の 20営業日には揃えない**
#: ——揃えると、同じ合図を4回数えることになる。
FLOW_HOLDING = 5


@dataclasses.dataclass(frozen=True)
class Wall:
    """ある設計の**壁の高さ**。**平均は持たない。**

    `PowerEstimate` と同じ設計である。**平均を持てる形にすると、「効果が
    ありそうだから通す」が書けてしまう。**
    """

    candidate: int
    """`docs/CANDIDATES.md` の順位。"""

    name: str
    pipe: str
    unit: str
    """1観測が何か（`月` / `年` / `イベント日`）。"""

    observations: int
    """**判定に使える観測数。** OOS の数である。"""

    sd: float
    """1観測あたりの標準偏差。**IS で測った。**"""

    inflation: float
    """重なりで標準誤差が何倍になるか。**実測。**

    **掛け忘れると緩む。** 20日保有・毎日エントリーなら理屈の上で4倍前後に
    なるので、落とせば壁が数倍低く出る（`power.standard_error`）。
    """

    line: float
    source: str
    """どうやって測ったか。**出典の無い数字を書かない。**"""

    sample: int = 0
    """**SD を測った IS の観測数。** `observations`（OOS）とは別である。

    **薄ければ、壁の高さそのものが当てにならない。** `wall-survey` には
    「IS が薄ければ〜」というコメントが在ったのに、**検査は OOS の
    `observations` を見ていた**（2026-09-21 に気付いた）。
    **コメントが主張していることと、コードが守っていることが別だった**
    ——`docs/POSTMORTEMS.md`「テストの名前が主張していることと、assert が守っている
    ことを突き合わせる」の、コメント版である。

    候補6（予想変動率）は `IV` が **2016-07-19 からしか無い**ので、IS が
    1年半しかない。**そこに当たる。**
    """

    window: int = 0
    """保有営業日数。**上限（`power.overlap_ceiling`）を出すのに要る。**

    0 なら上限を当てない（重ならない設計）。
    """

    undersampled: bool = False
    """**膨張のラグが、SD を測った標本より長かったか。**

    長ければ、いちばん長いラグが**1組の積**からできている。`Ω` がただの
    雑音になり、**1.0 を割ることさえある**——実データで候補6が **0.82x** を
    出した（2026-09-21）。`power.INFLATION_FLOOR` が壁を下げる向きは止めるが、
    **推定そのものが当てにならないことは別に言う。**
    """

    period_years: float | None = None
    """**判定に使える年数。** 年率に直せない設計では ``None``。

    ## 1年あたりの観測数を、別に持たない

    **`per_year` を焼き付けていた**（2026-09-21 に発覚）。候補7 と候補11 の
    両方に ``52.0`` と書いてあったが、**候補7 は買い越し週にしか入らない**
    ので、8.67年で 213 観測——**年 24.6 回である。**

    | | 記録した値 | 正しい値 |
    |---|---|---|
    | 候補7 の年率 | 年 32.0% | **年 15.1%** |

    **同じ行の2つの列が、別々の標本を指していた**——`docs/POSTMORTEMS.md` に5度
    書いてある形の6度目である。`observations` は絞った後の数なのに、
    `per_year` は絞る前の刻みを言っていた。

    **年数のほうを持って、率はそこから作る。** `power.Requirement` が
    ``periods ÷ period_years`` から率を作り、**2つの比が一致することを
    見る**のと同じ形で、**ここでは混ぜた行がそもそも作れない。**
    """

    notes: tuple[str, ...] = ()

    @property
    def ceiling(self) -> float:
        """標本が足りないときに置く**上限**（`power.overlap_ceiling`）。"""
        from stock_ai.backtest.power import overlap_ceiling

        return overlap_ceiling(self.window)

    @property
    def effective_inflation(self) -> float:
        """**実際に壁を作るのに使った膨張。**

        1. 床（1.0）を当てる——**割り引く向きには効かせない**
        2. **標本が足りなければ、上限に切り替える**（2026-09-21）

        **表に出す膨張と、壁を作った膨張が違うと、行が自分と食い違う**
        ——`docs/POSTMORTEMS.md`「表の見出しが約束していることと、行が答えていることを
        突き合わせる」。**測った値は :attr:`inflation` に残す。**

        ## 上限に切り替えたあと、推定値には戻さない

        **壁が上がって他の説を超えても、戻さない**（2026-09-21 に決めた。
        ユーザーの指摘）。**結果を見てから規則を選ぶことになる**——#7 が
        「五分五分と気付いたうえで回して負けた」形と同じで、**止める場所を
        作ったのに使わないことになる。**

        **どちらに転んでも上限を使う。** それを測る前に書いた。

        **下げる向きには使わない。** 推定値が上限より大きいなら、そちらを
        採る——上限は「これ以上は無い」と言うためのもので、**低く見せる
        ためのものではない。**
        """
        from stock_ai.backtest.power import INFLATION_FLOOR

        found = max(self.inflation, INFLATION_FLOOR)
        if self.undersampled:
            # **大きいほうを採る。** 上限で壁を下げない。
            found = max(found, self.ceiling)
        return found

    @property
    def floored(self) -> bool:
        """床か上限が効いたか。**効いたなら、表にそう出す。**"""
        return self.inflation < self.effective_inflation

    @property
    def capped(self) -> bool:
        """**上限に切り替えたか。** 床とは別に言う——理由が違う。"""
        return self.undersampled and self.effective_inflation == self.ceiling > self.inflation

    @property
    def detectable(self) -> float:
        """検出できる差。**式は `power` に1つだけ置いてある。**

        `線 × SD × 膨張 ÷ √n` である。**膨張を落とした版を1度書いた**
        （2026-09-19）——`passing.Shape` は掛けていたのに、ここだけ落ちて
        いた。**同じ式を3つ書けば、1つは間違える。**

        **掛けるのは :attr:`effective_inflation` である。** 測った値を
        そのまま掛けていたので、**上限に切り替えても壁に届いていなかった**
        （2026-09-21。テストが落ちて分かった）。床は `standard_error` の中で
        効いていたので気付かず、**上限を足したときに初めて出た。**

        **欄には「→ 上限 4.47x」と出るのに、壁は 1.62 で作られる**——
        この欄が防ぐはずだった食い違いそのものである。
        """
        from stock_ai.backtest.power import detectable_difference

        return detectable_difference(
            self.sd, self.effective_inflation, self.observations, self.line
        )

    @property
    def per_year(self) -> float | None:
        """1年あたりの観測数。**:attr:`period_years` から作る。**

        **焼き付けない。** 絞る設計では、絞る前の刻み（週次なら 52）と
        実際の観測数が食い違う。
        """
        if self.period_years is None or self.period_years <= 0:
            return None
        return self.observations / self.period_years

    @property
    def annual(self) -> float | None:
        """年あたりに直した壁。**直せない設計では ``None``。**

        **決めずに掛けない。** 資金をどれだけ張るかを決めないと、イベント型は
        年率に直せない（`docs/PASSING.md` と同じ扱い）。
        """
        rate = self.per_year
        return None if rate is None else self.detectable * rate

    @property
    def required_ir(self) -> float | None:
        """合格に要る**年率の情報比**。**式は `power` に1つだけ置いてある。**

        **設計によらない1つの数である**（`線 × 膨張 ÷ √年数`）。壁そのものは
        単位も桁も設計ごとに違うので、**行どうしを並べても比べられない。**

        **候補7 と候補11 がその実例だった。** 年 32.0% 対 24.5% と出ていたが、
        **要る情報比では 1.12 対 1.09 でほとんど差が無い**——違いは効果では
        なく、**市場に居る時間の割合**だった。

        **重なる窓には出さない**（:attr:`annual` と同じ理由）。
        """
        if self.period_years is None or self.period_years <= 0:
            return None
        from stock_ai.backtest.power import required_information_ratio

        return required_information_ratio(self.line, self.effective_inflation, self.period_years)


@dataclasses.dataclass(frozen=True)
class Missing:
    """**材料が無くて測れない候補。** 出力に出す——無いことは出力に出ない。

    ## 理由の文面は、黙って古くなる

    **2度やった**（2026-09-21、ユーザーが2度とも指摘）。

    | 候補 | 文面 | 実際 |
    |---|---|---|
    | 14 高配当 | 「在るかは `checks` が決める」 | **決めていた。2008年から在る** |
    | 16 空売り | 「何年から在るかも数えていない」 | **数えていた。欠損ゼロ** |

    **列の棚卸しが答えを出しているのに、壁の表の側が古い文面のまま残る。**
    `ex-date-audit` の見出しが 819 件のまま2世代古かったのと同じ形で、
    こちらは**数字ではなく、確かめたかどうかが古い。**

    **注意書きでは止まらない。** :attr:`endpoint` を書けば、`wall-survey`
    が**目録に原本が在るかを確かめて、在れば鳴る**——`read_manifest` は
    ファイルを開かないので、数える費用も掛からない。
    """

    candidate: int
    name: str
    reason: str
    endpoint: str = ""
    """**その理由が指している原本。** 書けば、在るかどうかを機械が見る。

    **空なら見ない。** 「口が無い」（#8 の噂、#17 の発表予定日）のように、
    **原本の有無では決まらない理由もある。**
    """


def archived_files(directory: Path, endpoint: str) -> int:
    """目録に、そのエンドポイントの原本が何本あるか。**開かない。**

    **`Missing` の文面が古くなっていないかを見るためである。** 中身を
    読まないので、`wall-survey` の頭で全部の候補に当てても費用が無い。

    Args:
        directory: 原本の置き場所。
        endpoint: 数えるエンドポイント。

    Returns:
        ファイル数。**目録が無ければ 0。**
    """
    from stock_ai.data.jquants_archive import read_manifest
    from stock_ai.data.jquants_read import endpoint_of

    return sum(1 for key in read_manifest(directory) if endpoint_of(key) == endpoint)


def stale_reasons(directory: Path, missing: Iterable[Missing]) -> list[str]:
    """**「材料が無い」と書いてあるのに、原本が在る候補**を言う。

    **「無いことは出力に出ない」の裏返しである。** 在るのに「無い」と
    書いてあることも、誰かが確かめるまで出力に出ない。

    Args:
        directory: 原本の置き場所。
        missing: 測れないと書いてある候補。

    Returns:
        鳴らすべき行。**1つも無ければ空。**
    """
    found: list[str] = []
    for item in missing:
        if not item.endpoint:
            continue
        files = archived_files(directory, item.endpoint)
        if files:
            found.append(
                f"**{item.candidate} は「材料が無い」に載っているが、"
                f"`{item.endpoint}` の原本が {files:,} ファイル在る。** "
                "**中身を数えてから書き直すこと**——`checks\\原本の列は"
                "埋まっているか.bat`。"
                "\n  **1本ずつが「その日の断面」でも、積み重なれば歴史になる。**"
                "「API が直近しか返さない」ことと、「手元に歴史が無い」ことは別である。"
            )
    return found


@dataclasses.dataclass
class Materials:
    """1回の走査で集めた材料。**価格を何度も読まないため。**"""

    high52: dict[tuple[str, pd.Period], tuple[dt.date, float]]
    price_level: dict[tuple[str, pd.Period], tuple[dt.date, float]]
    """月末の終値そのもの（候補13 低位株）。**小さい順に並べる。**"""

    round_position: dict[tuple[str, pd.Period], tuple[dt.date, float]]
    """節目からの位置（候補19）。:func:`round_number_position`。"""

    raw_price_level: dict[tuple[str, pd.Period], tuple[dt.date, float]]
    """月末の**分割調整前**の終値（候補14 の分母）。

    **配当利回りの分母は、調整前でなければならない。** 開示された1株配当は
    **その時点の株数**で書かれているので、分割調整後の終値で割ると
    **分割比のぶん利回りが跳ねる**——実データで 1:10 の分割を挟むと
    **1.0% が 10.0% になった**（2026-09-20、`_dividend_ratio` の `base`）。

    **同じ間違いを2度しないために、別の欄で持つ。** `price_level`（調整後）
    は順位づけに使うもので、**割り算の分母ではない。**
    """

    raw_differs: int
    """**調整前と調整後が違った**月末の銘柄月。

    **「調整前」と書いて、違うことを1度も測っていなかった**（2026-09-25）。
    件数が `raw_price_level` と同じと出ても、**それは両方作れたというだけで、
    値が違うことは言っていない。** 分割が1つでも在れば、分割より前の月では
    ここが増える。**0 なら、調整前と言っているものが調整後である。**
    """

    factor_changes: dict[str, tuple[tuple[dt.date, float], ...]]
    """``銘柄 -> ((日, 調整の倍率), ...)``。**倍率が変わった日だけ持つ。**

    倍率は ``調整後の終値 ÷ 調整前の終値``（`split_adjusted` が掛けている
    もの）。**分割の比は、この倍率の変わり方そのものである**——こちらで
    推測した係数ではなく、**その銘柄のその日の値**である。

    毎日持つと 5千銘柄 × 4千日になるので、**変わった日だけ**持つ。
    :func:`split_ratio_between` が2つの日の間の比を引く。
    """

    gaps_is: list[tuple[str, dt.date]]
    gaps_oos_days: int
    knives_is: list[tuple[str, dt.date]]
    knives_oos_days: int
    symbols: int
    skipped_short: int
    """52週に足りず、近さを作れなかった銘柄。"""

    dropped_broken: int
    """**不連続をまたぐので捨てたイベント**（IS）。

    **5営業日で −20% は、調整漏れの分割がそう見える形**である。#6 は不連続を
    外すだけで SD が 24.42% → 3.64% になった（`discontinuity`）。
    """

    def summary(self) -> str:
        """1行のまとめ。**平均は出さない。**"""
        return (
            f"{self.symbols:,} 銘柄を読んだ。"
            f"52週高値への近さ {len(self.high52):,} 銘柄月"
            f"（履歴が足りず外した銘柄 {self.skipped_short:,}）、"
            f"月末の終値 {len(self.price_level):,} 銘柄月、"
            f"うち調整前も取れた {len(self.raw_price_level):,}"
            f"（**調整後と値が違ったのは {self.raw_differs:,}**）、"
            f"下窓 {len(self.gaps_is):,} 件（IS）、"
            f"急落 {len(self.knives_is):,} 件（IS）。"
            f"**不連続をまたぐので捨てた {self.dropped_broken:,} 件。**"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**早期 return しない。**"""
        found: list[str] = []
        if self.raw_price_level and not self.raw_differs:
            found.append(
                f"**調整前の終値 {len(self.raw_price_level):,} 銘柄月が、1つも調整後と"
                "違わなかった。** 分割が1つも無いことはありえないので、**「調整前」と"
                "言っているものが調整後である。** 配当利回りの分母が崩れている。"
            )
        return found


def split_ratio_between(
    changes: tuple[tuple[dt.date, float], ...],
    start: dt.date,
    end: dt.date,
) -> float:
    """``start`` から ``end`` までに、**株数が何倍になったか。**

    倍率（調整後 ÷ 調整前）は、分割より前の足では ``1 ÷ 分割比`` になり、
    分割の日に 1 へ戻る。だから ``倍率(end) ÷ 倍率(start)`` が、その間の
    **分割比そのもの**になる。1:400 なら 400。

    **推測した係数ではない。** その銘柄の、その日の値である——
    `docs/POSTMORTEMS.md`「推測した係数で割るのが、いちばんやってはいけない直しである」
    の、やってよい側である。

    Args:
        changes: :attr:`Materials.factor_changes` の1銘柄ぶん。
        start: 開示日など、前の日。
        end: 組み替え日など、後の日。

    Returns:
        比。**分割が無ければ 1.0。** 材料が無くても 1.0（分からないものを
        分割に数えない）。
    """
    if not changes:
        return 1.0

    def factor_on(day: dt.date) -> float:
        days = [when for when, _factor in changes]
        position = bisect_right(days, day)
        # **最初の変わり目より前は、最初の値を使う。** その前の足は無いので、
        # 倍率が変わっていないと読むしかない。
        return changes[max(position - 1, 0)][1]

    before = factor_on(start)
    after = factor_on(end)
    if before <= 0 or after <= 0:
        return 1.0
    return after / before


def scan(
    database: Database,
    symbols: list[str] | None = None,
    min_turnover: float = MIN_TURNOVER,
    progress: Callable[[int, int], None] | None = None,
) -> Materials:
    """価格を**1度だけ**読んで、3つの設計の材料を同時に作る。

    Args:
        database: 価格の保存先。
        symbols: 対象銘柄。省くと JP の全銘柄。
        min_turnover: 流動性の下限（円）。
        progress: ``(済み, 全体)`` で呼ばれる。**1行に収めること。**

    Returns:
        :class:`Materials`。

    Raises:
        ValueError: 銘柄が1つも無い。
    """
    from stock_ai.database.repository import PriceRepository, list_securities

    high52: dict[tuple[str, pd.Period], tuple[dt.date, float]] = {}
    price_level: dict[tuple[str, pd.Period], tuple[dt.date, float]] = {}
    round_position: dict[tuple[str, pd.Period], tuple[dt.date, float]] = {}
    raw_price_level: dict[tuple[str, pd.Period], tuple[dt.date, float]] = {}
    factor_changes: dict[str, tuple[tuple[dt.date, float], ...]] = {}
    raw_differs = 0
    gaps_is: list[tuple[str, dt.date]] = []
    knives_is: list[tuple[str, dt.date]] = []
    gap_days_oos: set[dt.date] = set()
    knife_days_oos: set[dt.date] = set()
    read = short = dropped = 0

    with database.session() as session:
        if symbols is None:
            symbols = [sym for sym, market in list_securities(session) if market == "JP"]
        if not symbols:
            raise ValueError("銘柄が1つも無い。価格を取り込んでいない。")
        prices = PriceRepository(session)

        for position, symbol in enumerate(symbols, start=1):
            if progress is not None:
                progress(position, len(symbols))
            raw = prices.get_raw_prices(symbol)
            if raw.empty:
                continue
            read += 1
            adjusted = split_adjusted(raw)
            closes = adjusted[CLOSE].to_numpy(dtype=float)
            # **調整前の終値も持つ。** 配当利回りの分母はこちらである
            # ——1株配当はその時点の株数で書かれている。
            unadjusted = raw[CLOSE].to_numpy(dtype=float)
            opens = adjusted[OPEN].to_numpy(dtype=float)
            volumes = adjusted[VOLUME].to_numpy(dtype=float)
            index = adjusted.index
            if len(closes) < 2:  # noqa: PLR2004 - 1本では何も作れない
                continue

            # **調整の倍率が変わった日だけ持つ。** 分割の比はその変わり方で
            # ある——推測した係数ではなく、その銘柄のその日の値。
            changes: list[tuple[dt.date, float]] = []
            with np.errstate(divide="ignore", invalid="ignore"):
                factors = closes / unadjusted
            for stamp, factor in zip(index, factors, strict=True):
                if not np.isfinite(factor) or factor <= 0:
                    continue
                if not changes or abs(factor / changes[-1][1] - 1.0) > 1e-9:  # noqa: PLR2004
                    changes.append((stamp.date(), float(factor)))
            if changes:
                factor_changes[symbol] = tuple(changes)

            liquid = liquid_bars(closes, volumes, min_turnover)
            days = [stamp.date() for stamp in index]
            # **不連続をまたぐ窓を使わない。** 規則も定数も #6 と同じものを
            # 呼ぶ（`discontinuity`）——ここで近いものを書き直すと、数えた
            # 件数と実際に回したときの件数がずれる。
            prefix = crossings(session_breaks(adjusted[CLOSE]))
            last = len(closes) - 1

            def clean(first: int, until: int, _prefix=prefix, _last=last) -> bool:
                """``[first, until]`` に不連続が無いか。**端は切り詰める。**"""
                return not spans_break(_prefix, max(first, 0), min(until, _last))

            # --- 下窓（前日終値 → 当日始値）------------------------------
            # **規則は `gap_fill` に1つだけ置いてある。**
            for position in gap_positions(opens, closes, liquid, GAP_DOWN):
                # **窓の中に不連続があれば使わない。** 前日も見る——窓そのもの
                # が不連続でできている形を外すため。
                if not clean(position - 1, position + HOLDING):
                    dropped += 1
                    continue
                when = days[position]
                if when <= IS_END:
                    gaps_is.append((symbol, when))
                elif OOS_FROM <= when <= OOS_END:
                    gap_days_oos.add(when)

            # --- 急落（KNIFE_DAYS 営業日で KNIFE_DROP 以上）---------------
            # **規則は `knife` に1つだけ置いてある。**
            if len(closes) > KNIFE_DAYS:
                for position in knife_positions(closes, liquid, KNIFE_DROP, KNIFE_DAYS):
                    # **急落そのものが不連続でないこと。** 1:2 の併合は
                    # −50% に見える。そして窓の中も見る。
                    if not clean(position - KNIFE_DAYS, position + HOLDING):
                        dropped += 1
                        continue
                    when = days[position]
                    if when <= IS_END:
                        knives_is.append((symbol, when))
                    elif OOS_FROM <= when <= OOS_END:
                        knife_days_oos.add(when)

            # --- 月末の足（候補13・19・5 が同じものを使う）-----------------
            #
            # **52週の足切りより前に採る。** ここを `continue` の後ろに
            # 置いていたら、**履歴の短い銘柄が3つとも黙って落ちる**
            # ——`docs/POSTMORTEMS.md`「早期 return が、下に足した検査を黙らせる」。
            # 52週高値だけが 52週ぶんの履歴を要る。
            months = index.to_period("M")
            # **その銘柄の、その月の最後の足。** 暦の月末を探さない——月の
            # 途中で上場廃止になった銘柄が丸ごと落ちる（`valuation_monthly`）。
            last_of_month = np.flatnonzero(np.r_[months[1:] != months[:-1], True])
            for offset in last_of_month:
                close = float(closes[offset])
                if not liquid[offset] or not close > 0:
                    continue
                price_level[(symbol, months[offset])] = (days[offset], close)
                where = round_number_position(close)
                if where is not None:
                    round_position[(symbol, months[offset])] = (days[offset], where)
                bare = float(unadjusted[offset])
                if bare > 0:
                    raw_price_level[(symbol, months[offset])] = (days[offset], bare)
                    # **「調整前」を主張のままにしない。** 違う値であることを数える。
                    if abs(bare / close - 1.0) > 1e-9:  # noqa: PLR2004
                        raw_differs += 1

            # --- 52週高値への近さ（月末だけ）-----------------------------
            if len(closes) < HIGH_WINDOW:
                short += 1
                continue
            highs = pd.Series(closes).rolling(HIGH_WINDOW, min_periods=HIGH_WINDOW).max().to_numpy()
            for offset in last_of_month:
                top = highs[offset]
                if not np.isfinite(top) or top <= 0 or not closes[offset] > 0:
                    continue
                if not liquid[offset]:
                    continue
                high52[(symbol, months[offset])] = (days[offset], float(closes[offset] / top))

    return Materials(
        high52=high52,
        price_level=price_level,
        round_position=round_position,
        raw_price_level=raw_price_level,
        raw_differs=raw_differs,
        factor_changes=factor_changes,
        gaps_is=gaps_is,
        gaps_oos_days=len(gap_days_oos),
        knives_is=knives_is,
        knives_oos_days=len(knife_days_oos),
        symbols=read,
        skipped_short=short,
        dropped_broken=dropped,
    )


def round_number_position(price: float) -> float | None:
    """節目からどこに居るか。**0 が節目の直上、1 に近いほど節目の直下。**

    「1,000円の壁」を1つの数にする。**桁を揃えてから見る**——100円の株の
    節目は 100 で、10,000円の株の節目は 10,000 である。同じ「あと50円」でも
    意味が違う。

    ``刻み = 10 ** floor(log10(価格))`` として ``(価格 mod 刻み) / 刻み``
    を返す。**単位は無い**ので、桁の違う銘柄を同じ列に並べられる。

    **畳み方に出典は無い。決めの値である。** 格言は「キリ番は抜けにくい」と
    しか言わない。**測る前に1つに決めた**——複数試して良いほうを採れば、
    その時点で #10 と同じところに落ちる。

    Args:
        price: 終値。

    Returns:
        ``[0, 1)`` の位置。**0 以下なら ``None``**——対数が取れない。

    Examples:
        >>> round_number_position(1000.0)
        0.0
        >>> round_number_position(1950.0)
        0.95
    """
    if price <= 0 or not math.isfinite(price):
        return None
    step = 10.0 ** math.floor(math.log10(price))
    # **`price / step` が 10 に丸め上がることがある。** 1000 の対数がわずかに
    # 2.9999… になる盤面で、位置が 1.0 を超える——**範囲の外を返さない。**
    position = (price - step * math.floor(price / step)) / step
    return min(max(position, 0.0), 1.0 - 1e-12)


def tail_episodes(
    returns: list[float],
    dates: list[dt.date],
    sessions: int = TAIL_SESSIONS,
    end: dt.date = IS_END,
) -> tuple[list[int], list[float]]:
    """「12月の最後の ``sessions`` 営業日」を年ごとに1つ作る（候補18）。

    **年に1観測である。** `halloween_episodes` と同じ形——同じ年の中を2つに
    割ると、差を取っていないことになる。

    **その年の最後の営業日から数える。** 暦の 12/31 を探さない——大納会は
    年によって違う。

    Args:
        returns: 日次リターン。
        dates: その日付（``returns`` と同じ長さ）。
        sessions: 年末の何営業日を買うか。
        end: この日より後を使わない。

    Returns:
        ``(年, その年の取り高)``。**営業日が足りた年だけ。**

    Raises:
        ValueError: 長さが違う、``sessions`` が 1 未満。
    """
    if len(returns) != len(dates):
        raise ValueError(f"returns {len(returns)} と dates {len(dates)} の長さが違う。")
    if sessions < 1:
        raise ValueError(f"sessions must be at least 1; got {sessions}.")

    by_year: dict[int, list[float]] = {}
    for value, when in zip(returns, dates, strict=True):
        if when > end or when.month != 12:  # noqa: PLR2004 - 12月だけ
            continue
        by_year.setdefault(when.year, []).append(value)

    years: list[int] = []
    episodes: list[float] = []
    for year in sorted(by_year):
        tail = by_year[year][-sessions:]
        # **足りない年を、短いまま入れない。** 3日ぶんと5日ぶんを同じ列に
        # 並べると、散らばりが揃わない（`docs/POSTMORTEMS.md`「同じ推定量を測って
        # いることにならない」）。
        if len(tail) < sessions:
            continue
        compounded = 1.0
        for value in tail:
            compounded *= 1.0 + value
        years.append(year)
        episodes.append(compounded - 1.0)
    return years, episodes


def complete_tail_years(start: dt.date, end: dt.date, sessions: int = TAIL_SESSIONS) -> int:
    """``[start, end]`` に、12月の年末がまるごと入る年の数。

    **数えるのは年であって、日ではない。** `complete_halloween_years` と
    同じ理由——1観測が1年なので、判定に使える `n` は年の数である。

    **12月が途中で切れる年は数えない。** :func:`tail_episodes` が
    ``sessions`` に足りない年を捨てるので、**数え方を揃える。**

    Args:
        start: 判定に使う窓の始まり。
        end: 終わり。
        sessions: 年末の何営業日を買うか。**足りるかを暦では判定できない**
            ので、12月が丸ごと入っているかで数える。

    Returns:
        年の数。
    """
    if end <= start:
        return 0
    first = start.year if start <= dt.date(start.year, 12, 1) else start.year + 1
    last = end.year if end >= dt.date(end.year, 12, 31) else end.year - 1
    return max(last - first + 1, 0)


def signal_overlap(
    left: dict[tuple[str, pd.Period], tuple[dt.date, float]],
    right: dict[tuple[str, pd.Period], tuple[dt.date, float]],
) -> tuple[int, float | None]:
    """2つの並べ方が、**同じものを並べていないか。**

    **壁の表に実質同じ設計が2行在ると、後で良いほうを選んだのと区別が
    付かない**（`CLAUDE.md`「2箇所に同じ説があると、どちらが本当か分から
    なくなる」の設計版）。

    **順位で見る。** 値そのものの相関だと、片方が円でもう片方が比のときに
    桁で決まってしまう。**並べ方が同じかどうかだけが要る。**

    Args:
        left: ``(銘柄, 月) -> (日, 値)``。
        right: 同じ形。

    Returns:
        ``(重なった銘柄月, 順位相関)``。**3点未満なら相関は ``None``**
        ——2点は必ず ±1 になるので、何も言っていない。
    """
    shared = sorted(set(left) & set(right))
    if len(shared) < 3:  # noqa: PLR2004 - 2点の相関は必ず ±1
        return len(shared), None
    # **`method="spearman"` は scipy を要る。** 依存を増やさずに同じ値を出す
    # ——順位に直してから Pearson を取れば、それが Spearman の定義である。
    first = pd.Series([left[key][1] for key in shared]).rank()
    second = pd.Series([right[key][1] for key in shared]).rank()
    found = first.corr(second)
    return len(shared), None if pd.isna(found) else float(found)


def halloween_episodes(
    returns: list[float],
    dates: list[dt.date],
    end: dt.date = IS_END,
) -> tuple[list[int], list[float]]:
    """「冬（11月〜翌4月） − 夏（5月〜10月）」を年ごとに1つ作る。

    **年に1観測である。** 半年ごとに2つ作ると、同じ年の冬と夏が別々の観測に
    なり、**差を取っていないことになる。**

    Args:
        returns: 日次リターン。
        dates: その日付（``returns`` と同じ長さ）。
        end: この日より後を使わない。

    Returns:
        ``(年, その年の差)``。**冬と夏が両方そろった年だけ。**

    Raises:
        ValueError: 長さが違う。
    """
    if len(returns) != len(dates):
        raise ValueError(f"returns {len(returns)} と dates {len(dates)} の長さが違う。")

    winter: dict[int, list[float]] = {}
    summer: dict[int, list[float]] = {}
    for value, when in zip(returns, dates, strict=True):
        if when > end or not math.isfinite(value):
            continue
        if when.month >= 11:  # noqa: PLR2004 - 11月と12月は翌年の冬
            winter.setdefault(when.year + 1, []).append(value)
        elif when.month <= 4:  # noqa: PLR2004 - 1月〜4月はその年の冬
            winter.setdefault(when.year, []).append(value)
        else:
            summer.setdefault(when.year, []).append(value)

    years: list[int] = []
    episodes: list[float] = []
    for year in sorted(set(winter) & set(summer)):
        # **両方そろった年だけ。** 端の半年だけで1観測を作らない。
        years.append(year)
        episodes.append(float(np.sum(winter[year])) - float(np.sum(summer[year])))
    return years, episodes


def complete_halloween_years(start: dt.date, end: dt.date) -> int:
    """``start``〜``end`` に、冬と夏が**両方**収まる年が何回あるか。

    冬は前年11月から始まるので、**期間の頭の1年は作れない。**

    Args:
        start: 期間の始め。
        end: 期間の終わり。

    Returns:
        回数。
    """
    found = 0
    for year in range(start.year, end.year + 1):
        if dt.date(year - 1, 11, 1) >= start and dt.date(year, 10, 31) <= end:
            found += 1
    return found


def usable_rebalances(calendar: pd.DatetimeIndex, start: dt.date, end: dt.date) -> int:
    """``start``〜``end`` に収まる**組み替えの回数。判定に使える月数である。**

    **`build_grid` に聞く。** 月末を数えるだけでは1回多くなる——最後の月末は、
    降りる先の月末が無いので使えない。**同じ処理を2つ書かない。**

    Args:
        calendar: ベンチマークの暦。
        start: 期間の始め。
        end: 期間の終わり。**降りる日がこれを越える組み替えは数えない。**

    Returns:
        回数。1回も作れなければ 0。
    """
    from stock_ai.backtest.monthly_grid import build_grid

    try:
        grid = build_grid(calendar, formation_dates(calendar), start=start, end=end)
    except ValueError:
        # **1回も作れないのは例外ではない。** 期間が短ければ当たり前に起きる。
        return 0
    return grid.months


def volatility_spikes(
    levels: dict[dt.date, float],
    rise: float = IV_SPIKE,
) -> list[dt.date]:
    """予想変動率が**前日比 ``rise`` 以上**上がった日。

    **前の営業日と比べる。** 暦の前日ではない——原本に在る日だけを並べて、
    その1つ前と比べる。休みを挟んでも「前の観測」である。

    **割り算をしない。** ``後 >= 前 × (1 + rise)`` で当てる。`#16` が
    ちょうど −20% を取りこぼしていたのと同じ形を作らないため
    （`backtest/fall.py`）。

    Args:
        levels: ``日 -> 水準``。
        rise: 跳ねたと呼ぶ幅。

    Returns:
        跳ねた日。**日の順。**
    """
    days = sorted(levels)
    found: list[dt.date] = []
    for before, after in zip(days[:-1], days[1:], strict=True):
        low, high = levels[before], levels[after]
        if low > 0 and high >= low * (1.0 + rise):
            found.append(after)
    return found


def forward_windows(
    returns: list[float],
    dates: list[dt.date],
    entries: list[dt.date],
    holding: int,
    end: dt.date = IS_END,
) -> tuple[list[dt.date], list[float]]:
    """イベントの**翌営業日から** ``holding`` 営業日ぶんの指数リターン。

    **平均は取らない。** 系列をそのまま返す——`Wall` に平均の欄が無いのと
    同じ理由である。

    **同じ日に2回入らない。** イベント日が重なっても1つにまとめる
    ——`docs/POSTMORTEMS.md`「独立な観測を、件数で数えない」。

    Args:
        returns: 日次リターン。``dates`` と同じ長さ。
        dates: その日付。
        entries: イベント日。**その翌営業日から入る。**
        holding: 保有営業日数。
        end: この日より後に**入る**窓は作らない。

    Returns:
        ``(入った日, 窓のリターン)``。**窓が最後まで在るものだけ。**

    Raises:
        ValueError: ``returns`` と ``dates`` の長さが違う。
    """
    if len(returns) != len(dates):
        raise ValueError(f"returns {len(returns)} と dates {len(dates)} の長さが違う。")

    position = {when: index for index, when in enumerate(dates)}
    ordered = sorted(dates)
    used: list[dt.date] = []
    values: list[float] = []
    seen: set[int] = set()
    for event in sorted(set(entries)):
        # **翌営業日を探す。** 暦の翌日ではない。
        step = bisect_right(ordered, event)
        if step >= len(ordered):
            continue
        start = position[ordered[step]]
        if start in seen:
            continue
        if dates[start] > end or start + holding > len(returns):
            continue
        window = returns[start : start + holding]
        if any(not math.isfinite(value) for value in window):
            continue
        seen.add(start)
        used.append(dates[start])
        values.append(float(sum(window)))
    return used, values


def flow_entries(
    weeks: list[tuple[dt.date, float]],
    positive: bool = True,
) -> list[dt.date]:
    """需給の合図が立った**公表日**。

    **公表日で入る。** 週が終わってから公表まで 10 日ほどある（2008-01-04
    の週が 2008-01-16 公表）ので、**週末で入ると先読みになる。**

    Args:
        weeks: ``(公表日, その週の買い越し比率)``。
        positive: ``True`` なら買い越した週を採る。

    Returns:
        公表日。**日の順。**

    Notes:
        **向きは壁に効かない。** 検出できる差は散らばりと観測数だけで決まる
        ので、どちら側を採っても壁の高さは同じ形で出る。ここで ``positive``
        を既定にしているのは、**数える対象を1つに決めるため**である。
    """
    return sorted(
        published for published, share in weeks if (share > 0) is positive and math.isfinite(share)
    )


@dataclasses.dataclass(frozen=True)
class SpikeChoice:
    """梯子から選んだ線と、**その観測数だけ。** 効果は持たない。

    `Wall` と同じ設計である——**平均を持てる形にすると、「効果がありそうだ
    から通す」が書ける。**
    """

    rise: float
    """選んだ線（前日比）。"""

    events: tuple[dt.date, ...]
    """その線で跳ねた日。**IS も OOS も入っている**——OOS は件数だけ使う。"""

    is_windows: int
    """IS で窓が最後まで在った数。**膨張を推定する標本である。**"""

    needed: int
    """その保有日数で膨張を推定するのに要る数（`power.sample_needed`）。"""

    cleared: bool
    """要る数を満たしたか。**満たさなければ壁は暫定である。**"""

    tried: tuple[tuple[float, int], ...] = ()
    """``(線, IS の窓)`` を梯子の順に。**選び方が出力に出る。**"""


def choose_spike(  # noqa: PLR0913 - 梯子の当て方をすべて受け取る
    levels: dict[dt.date, float],
    returns: list[float],
    dates: list[dt.date],
    holding: int,
    end: dt.date,
    ladder: tuple[float, ...] = IV_SPIKE_LADDER,
) -> SpikeChoice:
    """梯子から線を1つ選ぶ。**満たす中でいちばん厳しいもの。**

    **効果を1つも計算しない。** 見るのは IS の窓の数だけである——
    `docs/WALL.md` の冒頭が許しているのはそこまでで、**そのつど効果を見れば
    #10 と同じところに落ちる。**

    **順に試して良いほうを採るのではない。** 上から見て**最初に条件を
    満たしたもの**を採るので、答えは1つに決まる。

    Args:
        levels: ``日 -> ATM の予想変動率``。
        returns: ベンチマークの日次リターン。
        dates: その日付。
        holding: 保有営業日数。**ラグでもある。**
        end: IS の最終日。
        ladder: 試す線。**厳しい順に並んでいること。**

    Returns:
        :class:`SpikeChoice`。**1つも満たさなければ、いちばん観測の多い線を
        返して ``cleared=False`` を立てる**——黙って空を返さない。

    Raises:
        ValueError: ``ladder`` が空。
    """
    from stock_ai.backtest.power import sample_needed

    if not ladder:
        raise ValueError("ladder must not be empty.")

    needed = sample_needed(holding)
    tried: list[tuple[float, int]] = []
    best: tuple[float, tuple[dt.date, ...], int] | None = None
    for rise in ladder:
        events = tuple(volatility_spikes(levels, rise))
        _used, values = forward_windows(returns, dates, list(events), holding, end=end)
        tried.append((rise, len(values)))
        if best is None or len(values) > best[2]:
            best = (rise, events, len(values))
        if len(values) >= needed:
            return SpikeChoice(
                rise=rise,
                events=events,
                is_windows=len(values),
                needed=needed,
                cleared=True,
                tried=tuple(tried),
            )

    assert best is not None  # noqa: S101 - ladder が空でないことは上で見た
    return SpikeChoice(
        rise=best[0],
        events=best[1],
        is_windows=best[2],
        needed=needed,
        cleared=False,
        tried=tuple(tried),
    )


#: 信用買い残（候補15）で、何公表ぶんの変化を見るか。
#:
#: **出典は無い。決めの値である。** 4公表 ≒ 1ヶ月で、月次の組み替えと
#: 刻みを揃えてある。格言は「買い残が減ると上がる」としか言わない。
MARGIN_LOOKBACK = 4


def margin_change(
    directory: Path,
    weeks: int = MARGIN_LOOKBACK,
    lag_days: int = 4,
) -> tuple[dict[tuple[str, pd.Period], tuple[dt.date, float]], MarginCensus]:
    """信用買い残の変化を、**銘柄月ごとに1つ**作る（候補15）。

    ## 畳み方は、壁を測る前に1つに決めてある

    | | |
    |---|---|
    | 材料 | `/markets/margin-interest` の `LongVol`（買い残・株数） |
    | 並べ方 | ``いまの買い残 ÷ ``weeks`` 公表前の買い残 − 1``。**減った順** |
    | 1観測 | 1ヶ月（月末の組み替え） |

    **比で見る。** 株数そのものは銘柄の大きさで桁が変わるので、断面に
    並べられない。

    ## 公表の遅れを外す

    `Date` は**金曜時点**で、公表はその第2営業日である（`jquants_markets`）。
    **月末の時点で公表されていない週を使うと、先読みになる。**
    ``as_of + lag_days`` がその月の末日以下のものだけを採る。

    **`MARGIN_INTEREST_LAG_DAYS` は下限である**（祝日のある週は後ろ倒し）。
    ここは壁の下見なので下限で足りるが、**事前登録では取引カレンダーと
    突き合わせること。**

    Args:
        directory: 原本の置き場所。
        weeks: 何公表ぶん前と比べるか。
        lag_days: 週末時点から公表までの日数（下限）。

    Returns:
        ``((銘柄, 月) -> (日, 変化率), 数えたもの)``。

    Raises:
        ValueError: ``weeks`` が 1 未満。
    """
    if weeks < 1:
        raise ValueError(f"weeks must be at least 1; got {weeks}.")

    from stock_ai.data.jquants_markets import parse_margin_interest

    history: dict[str, list[tuple[dt.date, float]]] = {}
    rows = 0
    for key, payload in _margin_files(directory):
        try:
            found = parse_margin_interest(payload)
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            logger.warning("信用残の原本を読めなかった: %s: %s", key, exc)
            continue
        for item in found:
            rows += 1
            if item.long_volume is None or item.long_volume <= 0:
                continue
            history.setdefault(item.symbol, []).append((item.as_of, item.long_volume))

    values: dict[tuple[str, pd.Period], tuple[dt.date, float]] = {}
    short = nonpositive = 0
    for symbol, entries in history.items():
        # **同じ週が2回出たら足さない。** 後から来たほうを1つだけ採る。
        by_week = dict(sorted(entries))
        ordered = sorted(by_week.items())
        if len(ordered) <= weeks:
            short += 1
            continue
        for position in range(weeks, len(ordered)):
            as_of, now = ordered[position]
            _before_when, before = ordered[position - weeks]
            if before <= 0:
                nonpositive += 1
                continue
            # **公表されてから使う。** 月末の時点で知れている週だけ。
            known = as_of + dt.timedelta(days=lag_days)
            month = pd.Timestamp(known).to_period("M")
            # **その月で、いちばん新しい公表を採る。** 上書きでよい——
            # `ordered` が古い順なので、最後に入ったものが最新になる。
            values[(symbol, month)] = (known, now / before - 1.0)

    return values, MarginCensus(
        rows=rows,
        symbols=len(history),
        observations=len(values),
        skipped_short=short,
        skipped_nonpositive=nonpositive,
        weeks=weeks,
    )


@dataclasses.dataclass(frozen=True)
class MarginCensus:
    """信用残を畳むときに落ちたもの。**合計だけ出すと、その中に紛れる。**"""

    rows: int
    symbols: int
    observations: int
    skipped_short: int
    """``weeks`` 公表ぶんの履歴が無かった銘柄。"""

    skipped_nonpositive: int
    """比べる相手の買い残が 0 以下だった銘柄月。"""

    weeks: int

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.rows:
            return "信用残の原本が1行も読めなかった。**材料が無い。**"
        return (
            f"信用残 {self.rows:,} 行、{self.symbols:,} 銘柄。"
            f"**{self.weeks} 公表ぶんの変化を {self.observations:,} 銘柄月**作れた。"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**早期 return しない。**"""
        found: list[str] = []
        if not self.rows:
            return ["**信用残の原本が1行も読めなかった。**"]
        if self.skipped_short:
            found.append(
                f"**{self.skipped_short:,} 銘柄は {self.weeks} 公表ぶんの履歴が無い。** "
                "変化を作れないので使っていない。"
            )
        if self.skipped_nonpositive:
            found.append(
                f"**{self.skipped_nonpositive:,} 銘柄月は、比べる相手の買い残が 0 以下だった。**"
            )
        if not self.observations:
            found.append("**1つも作れなかった。** 壁を出せない。")
        return found


def _margin_files(directory: Path):
    """信用残の原本を ``(鍵, 中身)`` で。**取りには行かない。**"""
    from stock_ai.data.jquants_archive import path_for, read_manifest
    from stock_ai.data.jquants_read import endpoint_of, read_archived

    for key in sorted(read_manifest(directory)):
        if endpoint_of(key) != "/markets/margin-interest":
            continue
        try:
            yield key, read_archived(path_for(directory, key))
        except Exception as exc:  # noqa: BLE001 - どこで開けないかが記録に値する
            logger.warning("信用残の原本を開けなかった: %s: %s", key, exc)


#: 配当利回りに使う列。**会社予想の年間配当。**
#:
#: **測る前に1つに決めた**（2026-09-21）。実データの列ごとの埋まり方から
#: 選んでいる——`checks\原本の列は埋まっているか.bat` が数えた。
#:
#: | 列 | 埋まっていた | 意味 |
#: |---|---|---|
#: | `FDivFY` | 57% | 会社予想の期末配当 |
#: | **`FDivAnn`** | **56%** | **会社予想の年間配当** |
#: | `Div2Q` | 53% | 実績の中間配当 |
#: | `DivFY` | 20% | 実績の期末配当 |
#: | `DivAnn` | 19% | 実績の年間配当 |
#: | `FDivTotalAnn` | **0%** | **1行も埋まっていない** |
#:
#: **実績（`DivAnn`、19%）ではなく予想を採る。** 実績の年間は期末の開示に
#: しか出ないので、断面が4分の1に痩せる。**そして投資家が見るのは予想の
#: ほうである。**
#:
#: **予想と実績を混ぜない**（`jquants_valuation` の `Fwd` と同じ規則）。
#: 混ぜると、**発表前から予想を知っていたこと**になりうる。
DIVIDEND_COLUMN = "FDivAnn"

#: 銘柄コードの列。**上から順に、最初に在ったものを使う。**
DIVIDEND_CODE_COLUMNS: tuple[str, ...] = ("Code", "LocalCode")

#: 開示日の列。**「いつ知れたか」である。** 先読みを外すのに要る。
DIVIDEND_DATE_COLUMNS: tuple[str, ...] = ("DiscDate", "DisclosedDate")

#: 直近の開示から、これより古くなったら使わない（候補14）。
#:
#: **出典は無い。決めの値である**（1年）。会社予想の年間配当は、**その会計
#: 年度のもの**である。1年を超えて引き継ぐと、**終わった年度の予想を
#: 今年の利回りとして使うことになる。**
#:
#: **これが無いと、2010年に開示をやめた銘柄が 2026年まで同じ配当を持ち歩く。**
#: 例外は出ない——**利回りが静かに古くなるだけである。**
DIVIDEND_STALE_DAYS = 365

#: 利回りがこれを超えたら、**数えて出す。落としも直しもしない。**
#:
#: **出典は無い。決めの値である。** 日本株で年 20% の利回りは、無配への
#: 訂正前か、単位の取り違えか、株価の異常である。
#:
#: **範囲から係数を逆算しない**——`MktCap` を百万倍間違えたときに書いた
#: 「範囲の中心から逆算すると根拠の無い数字になる」そのものである。
#: **どの単位なのかを決めるのは、その数字を見た人である。**
IMPLAUSIBLE_YIELD = 0.20


#: 実績の年間配当。**利回りには使わない。** 予想と混ぜないため。
#:
#: **監査でだけ並べる。** 予想が実績と桁違いなら、**訂正前の誤記**である
#: ——2131 の `DivRate` が `5600.0 → 56.0` と訂正されていたのと同じ形を、
#: 配当予想でも見る（2026-09-20）。
DIVIDEND_ACTUAL_COLUMN = "DivAnn"

#: 監査で持って返る、ありえない利回りの上限。**貼られる前提で作る。**
MAX_IMPLAUSIBLE_KEPT = 400


@dataclasses.dataclass(frozen=True)
class ImplausibleYield:
    """利回りが :data:`IMPLAUSIBLE_YIELD` を超えた1件。**中身を持って返る。**

    **件数だけ返すと、どちらの読み違いか追えない。** 「中身を見ること」と
    書いて見る道具が無い、を3度やった（`CLAUDE.md`）。

    **`DivAnn`（実績）も並べる。** 予想が実績と桁違いなら**訂正前の誤記**、
    どちらも大きいなら**株価か単位**である——**1回で分けられる。**
    """

    symbol: str
    month: str
    disclosed_on: dt.date
    forecast: float
    """:data:`DIVIDEND_COLUMN`（会社予想の年間配当）。"""

    actual: float | None
    """:data:`DIVIDEND_ACTUAL_COLUMN`。**同じ開示に無ければ ``None``。**"""

    close: float
    """**調整前**の月末終値。分母である。"""

    yielded: float

    @property
    def ratio(self) -> float | None:
        """予想 ÷ 実績。**訂正前の誤記なら、ここが 10 や 100 になる。**"""
        if self.actual is None or self.actual <= 0:
            return None
        return self.forecast / self.actual


@dataclasses.dataclass(frozen=True)
class YieldCensus:
    """配当利回りを畳むときに落ちたもの。**合計だけ出すと、その中に紛れる。**"""

    rows: int
    symbols: int
    observations: int
    no_symbol: int
    """銘柄コードが読めなかった行。**列名を間違えていれば、ここが全部になる。**"""

    no_date: int
    """開示日が読めなかった行。**同上。**"""

    no_amount: int
    """:data:`DIVIDEND_COLUMN` が空だった行。"""

    no_price: int
    """その月の**調整前**の終値が無かった銘柄月。"""

    priced_symbols: int
    """価格の側に在った銘柄。**突き合わせの相手である。**"""

    stale: int
    """**期限を越えた引き継ぎ**で落とした銘柄月。

    **合計だけ出すと、その中に紛れる。** 開示が止まった銘柄が多いのか、
    引き継ぎの期限が短すぎるのかは、**この数が言う。**
    """

    stale_days: int
    """引き継ぎの期限（日）。**決めの値である。**"""

    matched_symbols: int
    """**両側に在った銘柄。** 0 なら、綴りが噛み合っていない。

    `four_digit_code` は ``13060`` を ``1306`` に直す。**片側だけ通すと、
    列は全部読めているのに観測が 1つも出ない**——`no_symbol` は 0 のままな
    ので、**列の検査では捕まらない。**
    """

    implausible: int
    """利回りが :data:`IMPLAUSIBLE_YIELD` を超えた銘柄月。**外していない。**"""

    worst: tuple[ImplausibleYield, ...] = ()
    """そのうちの中身（利回りの大きい順）。**件数だけ返さない。**

    **`__post_init__` が件数と数を突き合わせる**ので、**札だけ古くなる形が
    消える**（`KnifeEvents` と同じ作り）。
    """

    def __post_init__(self) -> None:
        """**持って返った数が、数えた数を超えていないこと。**"""
        if len(self.worst) > self.implausible:
            raise ValueError(
                f"持って返った {len(self.worst)} 件が、数えた {self.implausible} 件を超えている。"
            )

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.rows:
            return "財務情報の原本が1行も読めなかった。**材料が無い。**"
        return (
            f"財務情報 {self.rows:,} 行、{self.symbols:,} 銘柄。"
            f"**`{DIVIDEND_COLUMN}` から利回りを {self.observations:,} 銘柄月**作れた。"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**早期 return しない。**"""
        found: list[str] = []
        if not self.rows:
            return ["**財務情報の原本が1行も読めなかった。**"]
        # **列名を間違えたら、ここが全部になる。** 黙って 0 件を返さない。
        if self.no_symbol == self.rows:
            found.append(
                f"**全 {self.rows:,} 行で銘柄コードが読めなかった。** 探したのは "
                + "、".join(DIVIDEND_CODE_COLUMNS)
                + "。**列名が違う。**"
            )
        if self.no_date == self.rows:
            found.append(
                f"**全 {self.rows:,} 行で開示日が読めなかった。** 探したのは "
                + "、".join(DIVIDEND_DATE_COLUMNS)
                + "。**列名が違う。**"
            )
        if self.no_amount == self.rows:
            found.append(f"**全 {self.rows:,} 行で `{DIVIDEND_COLUMN}` が空だった。**")
        if self.no_price:
            found.append(
                f"**{self.no_price:,} 銘柄月は、その月の調整前の終値が無かった。** "
                "**調整後では割らない**——1株配当はその時点の株数で書かれているので、"
                "分割比のぶん利回りが跳ねる。"
            )
        if self.stale:
            found.append(
                f"**{self.stale:,} 銘柄月は、直近の開示が {self.stale_days} 日より"
                "古かった。** **引き継いでいない**——会社予想の年間配当はその会計"
                "年度のものなので、越えて持ち歩くと**終わった年度の予想**になる。"
            )
        if self.implausible:
            share = self.implausible / self.observations if self.observations else 0.0
            found.append(
                f"**{self.implausible:,} 銘柄月は利回りが {IMPLAUSIBLE_YIELD:.0%} を超えた**"
                f"（{share:.2%}）。**外していない**——無配への訂正前か、単位の取り違えか、"
                "株価の異常である。**どれかは、中身を見るまで決めない。**"
                "\n  **この候補は分位に並べるだけなので、利回りの値そのものは SD に"
                "入らない。** 効くのは**入る分位を間違えること**で、その大きさは"
                "**その月の分位に占める割合**で決まる。"
                "\n  `checks\\高すぎる利回りを見る.bat` が、**開示から組み替えまでの"
                "分割の比**（その銘柄の調整の倍率から引く）と、**月ごとの最大の割合**"
                "を出す。**それでも直す**——判定では、間違った分位の銘柄がそのまま"
                "取り高に入る。"
            )
        # **突き合わせが空振りしたことは、列の検査では捕まらない。**
        # 列は全部読めていて、`no_symbol` も 0 のまま観測が出ない。
        if self.symbols and self.priced_symbols and not self.matched_symbols:
            found.append(
                f"**財務の {self.symbols:,} 銘柄と、価格の {self.priced_symbols:,} 銘柄が"
                "1つも噛み合わなかった。** **銘柄コードの綴りが違う**"
                "——`four_digit_code` を片側にしか通していないと、こうなる。"
            )
        if not self.observations:
            found.append("**1つも作れなかった。** 壁を出せない。")
        return found


def dividend_yields(
    directory: Path,
    prices: dict[tuple[str, pd.Period], tuple[dt.date, float]],
    column: str = DIVIDEND_COLUMN,
    stale_days: int = DIVIDEND_STALE_DAYS,
) -> tuple[dict[tuple[str, pd.Period], tuple[dt.date, float]], YieldCensus]:
    """会社予想の配当利回りを、**銘柄月ごとに1つ**作る（候補14）。

    ## 畳み方は、壁を測る前に1つに決めてある

    | | |
    |---|---|
    | 材料 | `/fins/summary` の :data:`DIVIDEND_COLUMN` ÷ **調整前**の月末終値 |
    | 並べ方 | 利回りの順 |
    | 1観測 | 1ヶ月 |

    ## 埋まっていない 44% をどう扱うか（**測る前に決めた**）

    実データで :data:`DIVIDEND_COLUMN` が埋まっているのは **56%** である
    （2026-09-21）。**残りをどうするかは設計の決定で、選択肢は3つあった。**

    | | どうなるか |
    |---|---|
    | 除く | **断面が3分の1に痩せる。** しかも「直前に開示した会社」だけが残り、**暦の産物になる** |
    | 実績で埋める | **予想と実績が混ざる**（`jquants_valuation` の `Fwd` と同じ禁則） |
    | **引き継ぐ** | **採った。** その時点で実際に知れていた値である |

    **引き継ぎには期限を置く**（:data:`DIVIDEND_STALE_DAYS`）。会社予想の
    年間配当は**その会計年度のもの**なので、1年を超えて持ち歩くと
    **終わった年度の予想を今年の利回りとして使う。**

    **期限が無いと、2010年に開示をやめた銘柄が 2026年まで同じ配当を持ち
    歩く。例外は出ない——利回りが静かに古くなるだけである。**

    ## 分母は調整前である

    **1株配当は、その時点の株数で書かれている。** 分割調整後の終値で割ると
    **分割比のぶん利回りが跳ねる**——実データで 1:10 の分割を挟むと
    **1.0% が 10.0% になった**（2026-09-20）。`prices` には
    :attr:`Materials.raw_price_level` を渡すこと。

    **残る限界を書いておく。** 開示から組み替えまでに分割が起きると、
    分子（開示時点の株数）と分母（いまの株数）がずれる。

    **「四半期ごとに出し直されるので、ずれは3ヶ月以内」と書いていた。
    外れだった**（2026-09-25、実データ）。予想が埋まっているのは開示の
    56% だけで、**出し直されない四半期がある。** 8328 は 2008-08-07 の
    開示が 2009-07 まで、1605 は 2013-02-06 の開示が 2013-10・11 に
    使われていた——**引き継ぎの期限（1年）いっぱいまで、分割をまたげる。**
    :func:`split_ratio_between` が、その間の分割比を測る。

    ## 先読みを外す

    ``開示日 <= 組み替え日`` の中で**いちばん新しいもの**を採る。
    `quantile_series.value_on` が最後にもう一度その関門を通すが、
    **ここでも月に畳む時点で切っている。**

    Args:
        directory: 原本の置き場所。
        prices: ``(銘柄, 月) -> (日, 調整前の終値)``。
        column: 使う配当の列。
        stale_days: 開示からこれより古くなったら使わない。

    Returns:
        ``((銘柄, 月) -> (日, 利回り), 数えたもの)``。
    """
    from stock_ai.data.jquants_bulk import records_from_csv
    from stock_ai.data.jquants_margin import parse_date, parse_number
    from stock_ai.data.universe import four_digit_code

    # **銘柄ごとに (開示日, 額) を集めてから畳む。** 行を見ながら分類すると、
    # **あとから来た行が前の行の情報を上書きする**（`docs/POSTMORTEMS.md`）。
    disclosed: dict[str, list[tuple[dt.date, float, float | None]]] = {}
    rows = no_symbol = no_date = no_amount = 0

    for key, payload in _summary_files(directory):
        try:
            found = records_from_csv(payload)
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            logger.warning("財務情報の原本を読めなかった: %s: %s", key, exc)
            continue
        for row in found:
            rows += 1
            symbol = next(
                (
                    code
                    for name in DIVIDEND_CODE_COLUMNS
                    if (code := four_digit_code((row.get(name) or "").strip()))
                ),
                None,
            )
            when = next(
                (
                    parsed
                    for name in DIVIDEND_DATE_COLUMNS
                    if (parsed := parse_date(row.get(name))) is not None
                ),
                None,
            )
            amount = parse_number(row.get(column))
            # **実績は利回りに使わない。** 監査で並べるだけである。
            actual = parse_number(row.get(DIVIDEND_ACTUAL_COLUMN))
            # **列ごとに独立に数える。** 直列に並べると、手前が全部を弾いた
            # とき後ろの数字が 0 のまま「異常なし」の顔をする（`docs/POSTMORTEMS.md`）。
            if symbol is None:
                no_symbol += 1
            if when is None:
                no_date += 1
            if amount is None:
                no_amount += 1
            if symbol is None or when is None or amount is None or amount < 0:
                continue
            disclosed.setdefault(symbol, []).append((when, amount, actual))

    # **銘柄で引けるように畳んでから回す。** `prices` を銘柄ごとに全部
    # なめると、**4千銘柄 × 40万銘柄月**になって終わらない。
    by_symbol: dict[str, list[tuple[pd.Period, dt.date, float]]] = {}
    for (symbol, month), (rebalance, close) in prices.items():
        by_symbol.setdefault(symbol, []).append((month, rebalance, close))

    values: dict[tuple[str, pd.Period], tuple[dt.date, float]] = {}
    no_price = implausible = stale = 0
    worst: list[ImplausibleYield] = []
    for symbol, entries in disclosed.items():
        entries.sort(key=lambda item: item[0])
        days = [day for day, _amount, _actual in entries]
        for month, rebalance, close in by_symbol.get(symbol, ()):
            # **その組み替え日までに開示されたうち、いちばん新しいもの。**
            position = bisect_right(days, rebalance)
            if position == 0:
                continue
            when, amount, actual = entries[position - 1]
            # **引き継ぎに期限を置く。** 無いと、開示をやめた銘柄が
            # いつまでも同じ配当を持ち歩く。
            if (rebalance - when).days > stale_days:
                stale += 1
                continue
            if close <= 0:
                no_price += 1
                continue
            found = amount / close
            if found > IMPLAUSIBLE_YIELD:
                implausible += 1
                # **中身を持って返る。** 件数だけでは、どちらの読み違いか
                # 追えない——「見る道具を置いたのに見えない」を3度やった。
                worst.append(
                    ImplausibleYield(
                        symbol=symbol,
                        month=str(month),
                        disclosed_on=when,
                        forecast=amount,
                        actual=actual,
                        close=close,
                        yielded=found,
                    )
                )
            # **観測した日は開示日である。** 組み替え日に置き換えると、
            # `value_on` の関門が何も弾かなくなる。
            values[(symbol, month)] = (when, found)

    return values, YieldCensus(
        rows=rows,
        symbols=len(disclosed),
        observations=len(values),
        no_symbol=no_symbol,
        no_date=no_date,
        no_amount=no_amount,
        no_price=no_price,
        implausible=implausible,
        priced_symbols=len(by_symbol),
        matched_symbols=len(set(disclosed) & set(by_symbol)),
        stale=stale,
        stale_days=stale_days,
        # **利回りの大きい順に、上限まで。** 貼られる前提で作る。
        worst=tuple(
            sorted(worst, key=lambda item: item.yielded, reverse=True)[:MAX_IMPLAUSIBLE_KEPT]
        ),
    )


def _summary_files(directory: Path):
    """財務情報の原本を ``(鍵, 中身)`` で。**取りには行かない。**"""
    from stock_ai.data.jquants_archive import path_for, read_manifest
    from stock_ai.data.jquants_read import endpoint_of, read_archived

    for key in sorted(read_manifest(directory)):
        if endpoint_of(key) != "/fins/summary":
            continue
        try:
            yield key, read_archived(path_for(directory, key))
        except Exception as exc:  # noqa: BLE001 - どこで開けないかが記録に値する
            logger.warning("財務情報の原本を開けなかった: %s: %s", key, exc)


#: 空売り比率の高低を測る、振り返りの営業日数（候補16）。
#:
#: **出典は無い。決めの値である**（約3ヶ月）。
#:
#: **これは壁に効かない。** 向きの決め方が変わっても ±1 倍は SD を変えない
#: ので、**ここで決めても答えを先に見たことにならない**——候補11 と同じ
#: 理屈である。
SHORT_RATIO_WINDOW = 60

#: 候補16 の保有営業日数。**候補6 と同じ**——同じ説の族だからである。
#:
#: **重ならないように、この間隔で入る。** 膨張は、要る情報比を下げられる
#: 3つのうちの1つである（`docs/PASSING.md` §2）。**毎日入ると √20 倍に
#: なり、壁がそのぶん上がる。**
SHORT_RATIO_HOLDING = 20


@dataclasses.dataclass(frozen=True)
class ShortRatios:
    """日ごとの空売り比率と、**読めなかったぶんの数。**"""

    levels: dict[dt.date, float]
    """``日 -> 空売り金額 ÷ 売り総額``。**33業種を金額で合計してから割る。**"""

    rows: int
    sectors: dict[str, int]
    """``業種 -> 行数``。**在った業種を全部数える**——無いことは出力に出ない。"""

    no_total: int
    """売り総額が 0 以下で、割れなかった日。"""

    def by_year(self) -> list[tuple[int, int]]:
        """``(年, 比率を作れた日数)``。**年の順。**"""
        found: dict[int, int] = {}
        for when in self.levels:
            found[when.year] = found.get(when.year, 0) + 1
        return sorted(found.items())

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.rows:
            return "空売り比率の原本が1行も読めなかった。**材料が無い。**"
        span = ""
        if self.levels:
            span = f"（{min(self.levels)} 〜 {max(self.levels)}）"
        return (
            f"空売り比率 {self.rows:,} 行、業種 {len(self.sectors)} 種類。"
            f"**比率を作れた日が {len(self.levels):,}**{span}。"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**早期 return しない。**"""
        found: list[str] = []
        if not self.rows:
            return ["**空売り比率の原本が1行も読めなかった。**"]
        if self.no_total:
            found.append(
                f"**{self.no_total:,} 日は売り総額が 0 以下だった。** 割れないので使っていない。"
            )
        if not self.levels:
            found.append("**1日も作れなかった。** 壁を出せない。")
        return found


def short_ratios(directory: Path) -> ShortRatios:
    """業種別の原本から、**市場ぜんぶの空売り比率**を日ごとに作る（候補16）。

    ## 畳み方は、壁を測る前に1つに決めてある

    ``(価格規制あり + 規制なし) ÷ (空売り以外の売り + 規制あり + 規制なし)``
    を、**33業種を金額で合計してから**割る。

    **業種ごとの比率を平均しない。** 小さい業種と大きい業種が同じ重みになる
    ——**「市場ぜんぶの空売り比率」ではなくなる。**

    ## 業種を絞らない

    **原本は業種別だが、この説は市場ぜんぶの悲観を見る。** 業種を選べば、
    **選び方が設計になる**——そこは決めていないので、全部足す。

    Args:
        directory: 原本の置き場所。

    Returns:
        :class:`ShortRatios`。
    """
    from stock_ai.data.jquants_bulk import records_from_csv
    from stock_ai.data.jquants_margin import parse_date, parse_number

    # **日ごとに足してから割る。** 業種ごとに割って平均すると、重みが崩れる。
    shorted: dict[dt.date, float] = {}
    total: dict[dt.date, float] = {}
    sectors: dict[str, int] = {}
    rows = 0

    for key, payload in _short_ratio_files(directory):
        try:
            found = records_from_csv(payload)
        except Exception as exc:  # noqa: BLE001 - どこで読めないかが記録に値する
            logger.warning("空売り比率の原本を読めなかった: %s: %s", key, exc)
            continue
        for row in found:
            rows += 1
            sectors[(row.get("S33") or "").strip()] = (
                sectors.get((row.get("S33") or "").strip(), 0) + 1
            )
            when = parse_date(row.get("Date"))
            if when is None:
                continue
            plain = parse_number(row.get("SellExShortVa")) or 0.0
            restricted = parse_number(row.get("ShrtWithResVa")) or 0.0
            free = parse_number(row.get("ShrtNoResVa")) or 0.0
            shorted[when] = shorted.get(when, 0.0) + restricted + free
            total[when] = total.get(when, 0.0) + plain + restricted + free

    levels: dict[dt.date, float] = {}
    no_total = 0
    for when in sorted(total):
        if total[when] <= 0:
            no_total += 1
            continue
        levels[when] = shorted[when] / total[when]

    return ShortRatios(levels=levels, rows=rows, sectors=sectors, no_total=no_total)


def spaced_entries(
    days: Iterable[dt.date],
    holding: int = SHORT_RATIO_HOLDING,
) -> list[dt.date]:
    """``holding`` 営業日ごとに1つだけ採って、**窓が重ならないようにする。**

    **毎日入ると膨張が `√holding` 倍になる。** 膨張は、要る情報比を下げら
    れる3つのうちの1つである（`docs/PASSING.md` §2）——**重ならない設計に
    寄せるのは、設計の型として先に決めてある。**

    **観測数は `holding` 分の1になるが、要る情報比は動かない。**
    n も SD も情報比には効かない。

    Args:
        days: 候補の日（順不同でよい）。
        holding: 保有営業日数。

    Returns:
        重ならないように間引いた日。**古い順。**

    Raises:
        ValueError: ``holding`` が 1 未満。
    """
    if holding < 1:
        raise ValueError(f"holding must be at least 1; got {holding}.")
    return sorted(days)[::holding]


def _short_ratio_files(directory: Path):
    """空売り比率の原本を ``(鍵, 中身)`` で。**取りには行かない。**"""
    from stock_ai.data.jquants_archive import path_for, read_manifest
    from stock_ai.data.jquants_read import endpoint_of, read_archived

    for key in sorted(read_manifest(directory)):
        if endpoint_of(key) != "/markets/short-ratio":
            continue
        try:
            yield key, read_archived(path_for(directory, key))
        except Exception as exc:  # noqa: BLE001 - どこで開けないかが記録に値する
            logger.warning("空売り比率の原本を開けなかった: %s: %s", key, exc)
