"""J-Quants の一括ダウンロード（`/bulk`）を読むための最小の層。

**なぜ要るか。** いまの財務取得は `params = {"code": symbol}` で、銘柄ごとに
1リクエストである。3,700銘柄なら3,700回で、実際に 429 で止まった。J-Quants
自身の資料が、この形を「よくある間違い」として名指ししている。

``/fins/summary`` と ``/equities/bars/daily`` は一括対応で、月ごとの gzip CSV
を1本落とせば全銘柄が入る。解約期限（2026-09-22）までに取り切る必要がある
会社予想（FSales/FOP/FNP/FEPS）と開示時刻（DiscTime）は ``/fins/summary`` に
乗っているので、ここが効く。

**このモジュールは「一覧」と「URL取得」しかしない。** 取り込みは、実際に何が
何個あるかを見てから作る。粒度を推測して作ると、少ない行数が黙って入る——
このプロジェクトで繰り返している型の不具合そのものになる。

認証ヘッダは ``x-api-key`` で、既存の取得経路と同じである。**専用の CLI を
入れる必要は無い**（鍵の写しがもう1つディスクに増えることも無い）。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
from typing import Any

from pydantic import SecretStr

from stock_ai.core.exceptions import DataError
from stock_ai.core.logging import get_logger
from stock_ai.data.http import raise_for_status

logger = get_logger(__name__)

_BASE = "https://api.jquants.com/v2"
BULK_LIST_URL = f"{_BASE}/bulk/list"
BULK_GET_URL = f"{_BASE}/bulk/get"

#: 一括対応のエンドポイント。J-Quants の資料に載っているもの。
#:
#: 検証に使うためではなく、**下見で「何が取れるのか」を人に見せるため**に
#: 持っている。ここに無いものを渡しても、拒否せずAPIに聞きに行く——
#: 対応表が古くなったときに、こちらの表のほうを信じて取り逃すのを避ける。
BULK_ENDPOINTS: tuple[str, ...] = (
    "/equities/master",
    "/equities/bars/daily",
    "/equities/bars/minute",
    "/equities/trades",
    "/equities/investor-types",
    "/fins/summary",
    "/fins/details",
    "/fins/dividend",
    "/indices/bars/daily/topix",
    "/indices/bars/daily",
    "/derivatives/bars/daily/options/225",
    "/derivatives/bars/daily/futures",
    "/derivatives/bars/daily/options",
    "/markets/margin-interest",
    "/markets/short-ratio",
    "/markets/short-sale-report",
    "/markets/margin-alert",
    "/markets/breakdown",
    "/markets/calendar",
)

#: 解約前に取り切りたいもの。`docs/JQUANTS_EXIT.md` の期限作業に対応する。
DEADLINE_ENDPOINTS: tuple[str, ...] = ("/fins/summary", "/equities/bars/daily")

#: プラン別の1分あたりリクエスト上限。出典は J-Quants 同梱の
#: `.claude/skills/jquants-cli-usage/SKILL.md`（Rate Limits）。
#:
#: **`BulkIngester` の既定 `throttle_seconds=0.5` は 120回／分**、つまり
#: Standard の上限ちょうどで余裕が無く、Light の上限の2倍である。銘柄ごとの
#: 取得が 84件で止まったのは、これで説明が付く可能性がある。
PLAN_REQUESTS_PER_MINUTE: dict[str, int] = {
    "Free": 5,
    "Light": 60,
    "Standard": 120,
    "Premium": 500,
}

#: プラン別に遡れる年数。同じ出典。**一括も個別APIも同じ範囲である。**
#:
#: 一覧が覆っている期間からプランを言い当てられる、という意味でもある。
#: 契約内容を人に思い出してもらうより、返ってきたファイルを数えるほうが早い。
PLAN_HISTORY_YEARS: dict[str, int] = {
    "Light": 5,
    "Standard": 10,
    "Premium": 20,
}

#: 上限に対してどれだけ余裕を取るか。**上限ちょうどを狙わない。**
#:
#: 大幅超過が続くと約5分アクセスが完全に遮断される（同出典）。取り切るまでの
#: 総時間で見れば、2割遅いほうが5分止まるより速い。
RATE_LIMIT_HEADROOM = 1.2


def recommended_throttle(plan: str) -> float | None:
    """そのプランで銘柄ごとに叩くときの、1件あたりの間隔（秒）。"""
    limit = PLAN_REQUESTS_PER_MINUTE.get(plan)
    if not limit:
        return None
    return 60.0 / limit * RATE_LIMIT_HEADROOM


def infer_plan(span_years: float) -> str | None:
    """覆っている年数から、契約しているプランを言い当てる。

    **当てずっぽうではなく、返ってきたファイルから読む。** 5年ぶんしか無ければ
    Light である。プランが分かれば1分あたりの上限が決まり、上限が決まれば
    銘柄ごとに叩くときの間隔が決まる。

    Args:
        span_years: 一覧が覆っている年数。

    Returns:
        いちばん近いプラン名。判断できなければ ``None``。
    """
    if span_years <= 0:
        return None
    # 上の段から見て、覆っている年数がその段に届いていれば、そのプラン。
    # 境目ちょうどで下に落ちないよう、少しだけ甘く見る。
    for plan in ("Premium", "Standard", "Light"):
        if span_years >= PLAN_HISTORY_YEARS[plan] * 0.9:
            return plan
    return None


#: `bulk/get` が返す署名付きURLの寿命。**5分**。
#:
#: 一覧を全部取ってから順に落とすと、後ろのURLが死ぬ。1本ずつ「取得して
#: すぐ落とす」しかない。取り込みを書くときにここを間違えると、途中から
#: 403 が並ぶ。
PRESIGNED_URL_TTL = dt.timedelta(minutes=5)

#: ファイル名の末尾に埋まっている年月（＋あれば日）を読む。
#:
#: 実物は2通りある。**片方しか見ない型を書いて、実際に取り逃した。**
#:
#:   fins/summary/historical/2021/fins_summary_202109.csv.gz   → 202109
#:   fins/summary/live/fins_summary_20260904.csv.gz            → 20260904
#:
#: 最初の型は8桁を探す型に掛からず、``/2021/`` は年しか持たないので月を
#: 探す型にも掛からなかった。結果、**historical の73本が黙って落ち**、
#: 5年ぶんのはずの範囲が「2026-08〜2026-09」と出た。例外は出ない。
#:
#: 日付はファイル名の側だけを見る。``/2021/`` のようなディレクトリまで
#: 拾うと、同じ日付を二重に数えることになる。
_STAMP = re.compile(r"(\d{4})(0[1-9]|1[0-2])(\d{2})?(?!\d)")


@dataclasses.dataclass(frozen=True)
class BulkFile:
    """一括ダウンロードで落とせるファイル1本。"""

    key: str
    last_modified: str
    size: int

    @property
    def megabytes(self) -> float:
        """サイズをメガバイトで。桁を人が読めるようにするためだけのもの。"""
        return self.size / 1_000_000


def _headers(api_key: SecretStr | None) -> dict[str, str]:
    return {"x-api-key": api_key.get_secret_value()} if api_key else {}


def _normalize_endpoint(endpoint: str | None) -> str | None:
    """先頭の ``/`` を補う。``fins/summary`` と書いても通るように。"""
    if endpoint is None:
        return None
    trimmed = endpoint.strip()
    if not trimmed:
        return None
    return trimmed if trimmed.startswith("/") else f"/{trimmed}"


def list_files(
    api_key: SecretStr | None,
    *,
    endpoint: str | None = None,
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[BulkFile]:
    """落とせるファイルを列挙する。**落としはしない。**

    Args:
        api_key: J-Quants の API キー。
        endpoint: ``/fins/summary`` のような絞り込み。省略すると全部。
        date: ``YYYY-MM`` または ``YYYY-MM-DD``。
        start: 期間の始め（``from`` はPythonの予約語なので改名している）。
        end: 期間の終わり。

    Returns:
        ``Key`` の昇順に並べたファイル。
    """
    import httpx

    params: dict[str, str] = {}
    normalized = _normalize_endpoint(endpoint)
    if normalized:
        params["endpoint"] = normalized
    if date:
        params["date"] = date
    if start:
        params["from"] = start
    if end:
        params["to"] = end

    files: list[BulkFile] = []
    pagination_key: str | None = None
    with httpx.Client(timeout=60.0) as client:
        while True:
            query = dict(params)
            if pagination_key:
                query["pagination_key"] = pagination_key
            response = client.get(BULK_LIST_URL, headers=_headers(api_key), params=query)
            raise_for_status(response, f"bulk list for {normalized or 'all endpoints'}")
            payload = response.json()
            for record in payload.get("data") or []:
                files.append(_as_file(record))
            pagination_key = payload.get("pagination_key")
            if not pagination_key:
                break

    logger.info("bulk/list returned %d file(s) for %s", len(files), normalized or "all")
    return sorted(files, key=lambda item: item.key)


def _as_file(record: dict[str, Any]) -> BulkFile:
    key = record.get("Key") or record.get("key")
    if not key:
        raise DataError(f"bulk/list returned a record with no Key: {sorted(record)}")
    raw_size = record.get("Size", record.get("size", 0))
    try:
        size = int(float(raw_size))
    except (TypeError, ValueError):
        size = 0
    return BulkFile(
        key=str(key),
        last_modified=str(record.get("LastModified") or record.get("last_modified") or ""),
        size=size,
    )


def presigned_url(
    api_key: SecretStr | None,
    *,
    key: str | None = None,
    endpoint: str | None = None,
    date: str | None = None,
) -> str:
    """1本分の署名付きURLを取る。**寿命は5分なので、取ったらすぐ落とす。**

    ``key`` か ``endpoint`` + ``date`` のどちらかを渡す。両方は渡せない。
    """
    import httpx

    if key and (endpoint or date):
        raise DataError("bulk/get は key と endpoint+date のどちらか一方しか受けない。")
    if not key and not endpoint:
        raise DataError("bulk/get には key か endpoint が要る。")

    params: dict[str, str] = {}
    if key:
        params["key"] = key
    normalized = _normalize_endpoint(endpoint)
    if normalized:
        params["endpoint"] = normalized
    if date:
        params["date"] = date

    with httpx.Client(timeout=60.0) as client:
        response = client.get(BULK_GET_URL, headers=_headers(api_key), params=params)
        raise_for_status(response, f"bulk get for {key or normalized}")
        payload = response.json()

    url = payload.get("url") or payload.get("Url") or payload.get("URL")
    if not url:
        raise DataError(f"bulk/get returned no url (keys: {sorted(payload)}).")
    return str(url)


def coverage(files: list[BulkFile]) -> tuple[str, str] | None:
    """ファイル名から、覆っている範囲を ``YYYY-MM`` で返す。

    **これを出さずに一括へ乗り換えない。** 一括が銘柄ごとのAPIより短い期間しか
    覆っていなければ、行数が黙って減る。例外は出ない——このプロジェクトで
    繰り返し起きているのは、その形の不具合である。

    月と日が混ざるので、**そろえてから比べる。** ``"202612"`` と
    ``"20260904"`` を文字列のまま比べると、前者のほうが大きいことになる
    （4文字目まで同じで、次が ``1`` 対 ``0``）。粒度の粗いほうに寄せる。

    Args:
        files: 一覧で返ってきたファイル。

    Returns:
        ``("2021-09", "2026-09")`` の形。1本も日付が読めなければ ``None``。
    """
    months: list[str] = []
    for item in files:
        name = item.key.rsplit("/", 1)[-1]
        months.extend(f"{year}-{month}" for year, month, _day in _STAMP.findall(name))
    if not months:
        return None
    return min(months), max(months)


def span_years(files: list[BulkFile]) -> float | None:
    """覆っている範囲を年で返す。プランを言い当てるのに使う。

    月まで数える。年だけを引き算すると、2021-09〜2026-09 も 2021-01〜2026-12 も
    同じ「5年」になり、プランの境目（5 / 10 / 20年）の判定が揺れる。
    """
    span = coverage(files)
    if span is None:
        return None
    first, last = span
    months = (int(last[:4]) - int(first[:4])) * 12 + (int(last[5:]) - int(first[5:]))
    return months / 12
