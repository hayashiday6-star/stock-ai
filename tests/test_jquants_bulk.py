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


def test_coverage_reads_daily_stamps_out_of_the_key() -> None:
    """日次ファイルは名前に ``YYYYMMDD`` が入っている。"""
    files = [
        BulkFile("equities/bars/daily/2021/09/bars_daily_20210903.csv.gz", "", 1),
        BulkFile("equities/bars/daily/2026/03/bars_daily_20260302.csv.gz", "", 1),
    ]

    assert coverage(files) == ("20210903", "20260302")


def test_coverage_falls_back_to_the_month_in_the_path() -> None:
    """月次のものは日を持たない。``/2024/01/`` から読む。"""
    files = [
        BulkFile("fins/summary/2024/01/summary.csv.gz", "", 1),
        BulkFile("fins/summary/2025/12/summary.csv.gz", "", 1),
    ]

    assert coverage(files) == ("202401", "202512")


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
