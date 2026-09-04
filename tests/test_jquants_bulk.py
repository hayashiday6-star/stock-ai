"""一括ダウンロードの一覧・URL取得（`stock_ai.data.jquants_bulk`）。

ここで固定しているのは、**間違えても例外が出ない**種類の点である。

- 覆っている範囲をファイル名から読めること。読めないまま乗り換えると、
  一括のほうが期間が短くても気付けない。
- ページングを最後まで辿ること。1ページで止めると、本数が黙って減る。
- `bulk/get` は key と endpoint+date の排他。両方渡せてしまうと、どちらが
  効いたのか出力から分からなくなる。
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from stock_ai.core.exceptions import DataError
from stock_ai.data.jquants_bulk import (
    BULK_ENDPOINTS,
    BULK_GET_URL,
    BULK_LIST_URL,
    DEADLINE_ENDPOINTS,
    BulkFile,
    coverage,
    list_files,
    presigned_url,
)


class _Response:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.text = ""
        self.headers: dict[str, str] = {}

    def json(self) -> dict:
        return self._payload


class _Client:
    """`httpx.Client` の代わり。呼ばれた URL と params を記録する。"""

    def __init__(self, pages: list[dict]) -> None:
        self._pages = list(pages)
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get(self, url: str, headers: dict, params: dict) -> _Response:
        self.calls.append((url, dict(params)))
        return _Response(self._pages.pop(0))


@pytest.fixture
def patched(monkeypatch):
    """`httpx.Client` を差し替えて、返すページを test 側から決める。"""
    holder: dict[str, _Client] = {}

    def install(pages: list[dict]) -> _Client:
        client = _Client(pages)
        import httpx

        monkeypatch.setattr(httpx, "Client", lambda **_kwargs: client)
        holder["client"] = client
        return client

    return install


def test_list_files_follows_pagination_to_the_end(patched) -> None:
    """1ページで止めると、本数が黙って減る。"""
    client = patched(
        [
            {
                "data": [{"Key": "fins/summary/2024/01/a.csv.gz", "Size": 10, "LastModified": "x"}],
                "pagination_key": "next",
            },
            {
                "data": [{"Key": "fins/summary/2024/02/b.csv.gz", "Size": 20, "LastModified": "y"}],
            },
        ]
    )

    files = list_files(SecretStr("k"), endpoint="/fins/summary")

    assert [item.key for item in files] == [
        "fins/summary/2024/01/a.csv.gz",
        "fins/summary/2024/02/b.csv.gz",
    ]
    assert len(client.calls) == 2
    assert client.calls[0][0] == BULK_LIST_URL
    assert "pagination_key" not in client.calls[0][1]
    assert client.calls[1][1]["pagination_key"] == "next"


def test_list_files_normalizes_a_missing_leading_slash(patched) -> None:
    """``fins/summary`` と書いても ``/fins/summary`` として送ること。"""
    client = patched([{"data": []}])

    list_files(SecretStr("k"), endpoint="fins/summary")

    assert client.calls[0][1]["endpoint"] == "/fins/summary"


def test_list_files_sends_from_and_to_under_the_api_names(patched) -> None:
    """``from`` は Python の予約語なので ``start`` で受けるが、送る名前は ``from``。"""
    client = patched([{"data": []}])

    list_files(SecretStr("k"), endpoint="/fins/summary", start="2024-01-01", end="2024-03-31")

    params = client.calls[0][1]
    assert params["from"] == "2024-01-01"
    assert params["to"] == "2024-03-31"


# 本番の `bulk/list` が返した実物のキー（2026-09-04）。**作り物ではない。**
#
# historical は月（6桁）、live は日（8桁）で、名前の形が違う。最初に書いた型は
# 8桁しか見ておらず、``/2021/`` は年しか持たないので月の型にも掛からなかった。
# **historical の73本が黙って落ち、5年ぶんの範囲が「2026-08〜2026-09」と出た。**
# 例外は出ない。この関数が防ぐために書かれた、まさにその形の間違いである。
_REAL_KEYS = [
    "fins/summary/historical/2021/fins_summary_202109.csv.gz",
    "fins/summary/historical/2021/fins_summary_202110.csv.gz",
    "fins/summary/historical/2021/fins_summary_202111.csv.gz",
    "fins/summary/historical/2021/fins_summary_202112.csv.gz",
    "fins/summary/historical/2022/fins_summary_202201.csv.gz",
    "fins/summary/live/fins_summary_20260831.csv.gz",
    "fins/summary/live/fins_summary_20260901.csv.gz",
    "fins/summary/live/fins_summary_20260904.csv.gz",
]


def test_coverage_reads_both_the_historical_and_the_live_naming() -> None:
    """片方の型しか見ないと、覆っている範囲が黙って縮む。"""
    files = [BulkFile(key, "", 1) for key in _REAL_KEYS]

    assert coverage(files) == ("2021-09", "2026-09")


def test_coverage_does_not_lose_the_historical_files_to_the_live_ones() -> None:
    """live だけを数えると 2026-08 始まりに見える。**これが実際に起きた。**"""
    only_live = [BulkFile(key, "", 1) for key in _REAL_KEYS if "/live/" in key]
    both = [BulkFile(key, "", 1) for key in _REAL_KEYS]

    assert coverage(only_live) == ("2026-08", "2026-09")
    assert coverage(both)[0] == "2021-09"


def test_coverage_orders_months_and_days_on_the_same_scale() -> None:
    """``"202612"`` と ``"20260904"`` を素で比べると、前者が大きいことになる。"""
    files = [
        BulkFile("x/live/f_20260904.csv.gz", "", 1),
        BulkFile("x/historical/2026/f_202612.csv.gz", "", 1),
    ]

    assert coverage(files) == ("2026-09", "2026-12")


def test_coverage_ignores_a_year_that_carries_no_month() -> None:
    """``/2021/`` のようなディレクトリを日付として数えない。"""
    assert coverage([BulkFile("fins/summary/historical/2021/summary.csv.gz", "", 1)]) is None


def test_span_years_counts_months_not_just_the_year_digits() -> None:
    """年だけ引くと、プランの境目（5 / 10 / 20年）の判定が揺れる。"""
    from stock_ai.data.jquants_bulk import span_years

    files = [BulkFile(key, "", 1) for key in _REAL_KEYS]

    # 2021-09 〜 2026-09 はちょうど5年。**Light の上限である。**
    assert span_years(files) == pytest.approx(5.0)
    assert span_years([]) is None


def test_coverage_returns_none_when_the_name_carries_no_date() -> None:
    """**読めないときは黙って嘘をつかない。** 呼び出し側が「読めない」と出せる。"""
    assert coverage([BulkFile("equities/master/master.csv.gz", "", 1)]) is None
    assert coverage([]) is None


def test_presigned_url_refuses_key_and_endpoint_together(patched) -> None:
    """どちらが効いたのか分からない呼び方を通さない。"""
    patched([{"url": "https://example.invalid/x.gz"}])

    with pytest.raises(DataError):
        presigned_url(SecretStr("k"), key="a.gz", endpoint="/fins/summary")

    with pytest.raises(DataError):
        presigned_url(SecretStr("k"))


def test_presigned_url_returns_the_url(patched) -> None:
    client = patched([{"url": "https://example.invalid/x.gz"}])

    url = presigned_url(SecretStr("k"), endpoint="/fins/summary", date="2024-01")

    assert url == "https://example.invalid/x.gz"
    assert client.calls[0][0] == BULK_GET_URL
    assert client.calls[0][1] == {"endpoint": "/fins/summary", "date": "2024-01"}


def test_a_record_without_a_key_is_an_error_not_an_empty_file(patched) -> None:
    """欠けた ``Key`` を空文字で通すと、後段が空のファイルを取りに行く。"""
    patched([{"data": [{"Size": 10}]}])

    with pytest.raises(DataError):
        list_files(SecretStr("k"), endpoint="/fins/summary")


def test_the_deadline_set_is_a_subset_of_the_bulk_endpoints() -> None:
    """期限で取り切るものが、一括対応の一覧に載っていること。"""
    assert set(DEADLINE_ENDPOINTS) <= set(BULK_ENDPOINTS)
    assert "/fins/summary" in DEADLINE_ENDPOINTS


# --- プランを覆っている年数から言い当てる ------------------------------------
#
# 契約を思い出してもらうより、返ってきたファイルを数えるほうが速い。
# プランが決まれば1分あたりの上限が決まり、上限が決まれば叩く間隔が決まる。


def test_infer_plan_reads_the_span_back_to_a_plan() -> None:
    from stock_ai.data.jquants_bulk import infer_plan

    assert infer_plan(20) == "Premium"
    assert infer_plan(10) == "Standard"
    assert infer_plan(5) == "Light"


def test_infer_plan_does_not_round_a_short_span_up() -> None:
    """5年ぶんしか無いのを Standard と読むと、上限を2倍に見誤る。"""
    from stock_ai.data.jquants_bulk import infer_plan

    assert infer_plan(6) == "Light"
    assert infer_plan(0) is None
    assert infer_plan(1) is None


def test_recommended_throttle_leaves_headroom_under_the_ceiling() -> None:
    """上限ちょうどを狙わない。大幅超過が続くと約5分まるごと遮断される。"""
    from stock_ai.data.jquants_bulk import (
        PLAN_REQUESTS_PER_MINUTE,
        recommended_throttle,
    )

    for plan, limit in PLAN_REQUESTS_PER_MINUTE.items():
        interval = recommended_throttle(plan)
        assert interval is not None
        # その間隔で1分間叩き続けても、上限を超えないこと。
        assert 60.0 / interval < limit, plan

    assert recommended_throttle("Nonexistent") is None


def test_the_default_throttle_is_too_fast_for_light() -> None:
    """``BulkIngester`` の既定 0.5 秒は 120回／分。Light の上限の2倍である。

    ここは「直したことを固定する」テストではなく、**既定とプランの上限が
    ずれていることを記録する**テストである。取り込みが 84 件で止まった
    説明として、いちばんもっともらしい。
    """
    import inspect

    from stock_ai.data.bulk import BulkIngester
    from stock_ai.data.jquants_bulk import PLAN_REQUESTS_PER_MINUTE

    # **既定値は書き写さず、実物から読む。** 書き写すと、既定が動いたあとも
    # このテストは古い値について通り続ける。
    throttle = inspect.signature(BulkIngester.__init__).parameters["throttle_seconds"].default
    assert throttle > 0
    default_per_minute = 60.0 / throttle

    assert default_per_minute > PLAN_REQUESTS_PER_MINUTE["Light"]
    assert default_per_minute >= PLAN_REQUESTS_PER_MINUTE["Standard"]
