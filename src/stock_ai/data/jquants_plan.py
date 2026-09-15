"""どのプランで何が取れるか、そのうち何が手元にあるか。

**解約してよいかは、感触では決められない。** Free に落とすと、取引カレンダー
を除いて一括ダウンロードが丸ごと止まり、API で見える範囲も「12週間前〜2年
12週間前」という細い窓になる。つまり **いま原本として持っていないものは、
再契約するまで二度と取れない。**

このモジュールは2つを突き合わせる。

1. 公式の「プラン別 API 利用可否・データ取得範囲」（出典は下の
   :data:`MINIMUM_PLAN`）。
2. 原本の目録に実際に何本あるか。

**「取れるはずだった」と「取ってある」を並べる。** 片方だけを見ていると、
一覧に名前があることを取得したことと取り違える——このプロジェクトで繰り返し
起きているのは、まさにその形の間違いである。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from stock_ai.data.jquants_archive import DEFAULT_ARCHIVE_DIR, key_period, read_manifest
from stock_ai.data.jquants_bulk import ARCHIVE_ENDPOINTS
from stock_ai.data.jquants_read import endpoint_of

#: 下から上へ。**上位は下位を全部含む**（公式表の「プラン階層」）。
PLAN_ORDER: tuple[str, ...] = ("Free", "Light", "Standard", "Premium")

#: エンドポイントごとの最低必要プラン。
#:
#: 出典: `.claude/skills/jquants-cli-usage/references/plans.md`（さらにその
#: 出典は https://jpx-jquants.com/ja/spec/data-spec ）の「最低必要プラン
#: 早見表」。
#:
#: **`/equities/valuation` だけは、その表に載っていない。** 2026-09-15 に
#: ユーザーが貼った公式の「契約ごとに利用可能なAPIとデータ格納期間」に一括の
#: 口があり、Light 以上と読めた。表そのものが手元のファイルに無いので、
#: **ここが唯一の出典である**ことを記しておく。
MINIMUM_PLAN: dict[str, str] = {
    "/equities/master": "Free",
    "/equities/bars/daily": "Free",
    "/fins/summary": "Free",
    "/fins/earnings-date": "Free",
    "/markets/calendar": "Free",
    "/equities/investor-types": "Light",
    "/indices/bars/daily/topix": "Light",
    "/equities/valuation": "Light",
    "/markets/margin-interest": "Standard",
    "/markets/short-ratio": "Standard",
    "/markets/short-sale-report": "Standard",
    "/markets/margin-alert": "Standard",
    "/indices/bars/daily": "Standard",
    "/derivatives/bars/daily/options/225": "Standard",
    "/fins/details": "Premium",
    "/fins/dividend": "Premium",
    "/markets/breakdown": "Premium",
    "/derivatives/bars/daily/futures": "Premium",
    "/derivatives/bars/daily/options": "Premium",
}

#: Free だけの特別扱い。**プランの順番だけでは言い表せない。**
#:
#: 公式表の「Free プランの制限事項」より:
#:
#: 1. 取引カレンダーを除き、CSV／一括ダウンロードが使えない。
#: 2. 見える範囲が「12週間前〜2年12週間前」のローリング窓になる。直近12週も、
#:    2年12週より古いところも取れない。
#:
#: つまり **Free では、ここに挙げた1本を除いて原本が1本も増えない。**
#: 「Free でも `/equities/master` は使える」は API の話であって、保存の話では
#: ない。両者を混ぜると、取れないものを「取れる」と案内することになる。
FREE_BULK_ALLOWED: frozenset[str] = frozenset({"/markets/calendar"})

#: 歴史を持たないもの。**保存しても「実行した日の分」しか残らない。**
#:
#: 決算発表予定日は全プランで「直近のみ」（公式表）。原本を何本持っていても、
#: 過去のある日に何が予定されていたかは戻ってこない。解約で失うものの一覧に
#: 入れるときは、そこを混ぜないこと。
NO_HISTORY: frozenset[str] = frozenset({"/fins/earnings-date"})

#: 一括の口が無く、原本として残せないもの。**解約の判断では別枠にする。**
#:
#: 前場四本値（`eq am`）は Premium 専用で、かつ「直近のみ」。一括の口は公式表
#: に無く、REST のパスも手元の資料に載っていない（`commands-eq.md` は CLI の
#: 呼び方しか書いていない）ので、**パスは推測せずに空けてある。**
#:
#: 失うのは *貯めたもの* ではなく *これから取れること* である。再契約すれば
#: その日から戻る。
UNARCHIVABLE: dict[str, str] = {
    "前場四本値（eq am）": "Premium 専用。直近のみで歴史が無く、一括の口も無い。",
}


def covers(plan: str, endpoint: str) -> bool:
    """``plan`` で ``endpoint`` が使えるか。知らない名前は使えない側に倒す。

    **広いほうに倒さない。** 広く見積もると、取れないものを「あとで取れる」
    と案内して、解約の判断を誤らせる。
    """
    needed = MINIMUM_PLAN.get(endpoint)
    if needed is None or plan not in PLAN_ORDER:
        return False
    return PLAN_ORDER.index(plan) >= PLAN_ORDER.index(needed)


def archivable(plan: str, endpoint: str) -> bool:
    """``plan`` で ``endpoint`` の**原本が増やせる**か。

    :func:`covers` との違いが Free の制限そのものである。API で見えることと、
    一括で落として残せることは別。
    """
    if not covers(plan, endpoint):
        return False
    if plan == "Free":
        return endpoint in FREE_BULK_ALLOWED
    return True


@dataclasses.dataclass
class EndpointCoverage:
    """1エンドポイントぶんの、公式の可否と手元の実数。"""

    endpoint: str
    minimum_plan: str
    files: int
    bytes: int
    first: str
    """いちばん古い原本の期間（``YYYYMMDD``）。無ければ空文字。"""

    last: str

    @property
    def stored(self) -> bool:
        """原本が1本でもあるか。"""
        return self.files > 0


@dataclasses.dataclass
class PlanCoverage:
    """原本の目録を、公式のプラン表に当てた結果。"""

    plan: str
    entries: list[EndpointCoverage]
    unknown_keys: int
    """どのエンドポイントにも当てはまらなかった鍵の数。**0 でないなら表が古い。**"""

    def blockers(self, target: str) -> list[EndpointCoverage]:
        """``target`` に落とすと取れなくなり、しかも**手元に1本も無い**もの。

        ここが空でないなら、落とす前に取りに行く先がある。空なら、失うのは
        「これから取れること」だけで、再契約すれば戻る。
        """
        return [
            entry
            for entry in self.entries
            if entry.files == 0
            and archivable(self.plan, entry.endpoint)
            and not archivable(target, entry.endpoint)
        ]

    def losing(self, target: str) -> list[EndpointCoverage]:
        """``target`` に落とすと**増やせなくなる**もの。手元にあるかは問わない。"""
        return [
            entry
            for entry in self.entries
            if archivable(self.plan, entry.endpoint) and not archivable(target, entry.endpoint)
        ]


def coverage(
    plan: str,
    directory: Path = DEFAULT_ARCHIVE_DIR,
    endpoints: tuple[str, ...] = ARCHIVE_ENDPOINTS,
) -> PlanCoverage:
    """原本の目録を数えて、エンドポイントごとに並べる。**取りには行かない。**

    Args:
        plan: いま契約しているプラン名。
        directory: 原本の置き場所。
        endpoints: 数える対象。既定は原本として残すことにしてあるもの全部。

    Returns:
        :class:`PlanCoverage`。一覧には**0本のものも残す**——0 を消すと、
        取り逃したものが表から消えて見えなくなる。
    """
    manifest = read_manifest(directory)
    buckets: dict[str, list[tuple[str, int]]] = {endpoint: [] for endpoint in endpoints}
    unknown = 0
    for key, entry in manifest.items():
        found = endpoint_of(key, list(endpoints))
        if found is None:
            unknown += 1
            continue
        buckets[found].append((key_period(key), entry.bytes_written))

    entries = []
    for endpoint in endpoints:
        rows = buckets[endpoint]
        periods = sorted(period for period, _ in rows if period)
        entries.append(
            EndpointCoverage(
                endpoint=endpoint,
                minimum_plan=MINIMUM_PLAN.get(endpoint, "不明"),
                files=len(rows),
                bytes=sum(size for _, size in rows),
                first=periods[0] if periods else "",
                last=periods[-1] if periods else "",
            )
        )
    return PlanCoverage(plan=plan, entries=entries, unknown_keys=unknown)
