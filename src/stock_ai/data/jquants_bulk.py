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

#: `bulk/get` が返す署名付きURLの寿命。**5分**。
#:
#: 一覧を全部取ってから順に落とすと、後ろのURLが死ぬ。1本ずつ「取得して
#: すぐ落とす」しかない。取り込みを書くときにここを間違えると、途中から
#: 403 が並ぶ。
PRESIGNED_URL_TTL = dt.timedelta(minutes=5)

#: ファイル名に埋まっている日付を読むための型。
_DAY = re.compile(r"(\d{8})")
_MONTH = re.compile(r"/(\d{4})/(\d{2})/")


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
    """ファイル名に埋まっている日付から、覆っている範囲を返す。

    **これを出さずに一括へ乗り換えない。** 一括が銘柄ごとのAPIより短い期間しか
    覆っていなければ、行数が黙って減る。例外は出ない——このプロジェクトで
    繰り返し起きているのは、その形の不具合である。
    """
    stamps: list[str] = []
    for item in files:
        day = _DAY.findall(item.key)
        if day:
            stamps.extend(day)
            continue
        # 日次でないものは ``.../2026/03/...`` のように月までしか持たない。
        stamps.extend(f"{year}{month}" for year, month in _MONTH.findall(item.key))
    if not stamps:
        return None
    return min(stamps), max(stamps)
