"""一括ダウンロードの一覧・URL取得（`stock_ai.data.jquants_bulk`）。

ここで固定しているのは、**間違えても例外が出ない**種類の点である。

- 覆っている範囲をファイル名から読めること。読めないまま乗り換えると、
  一括のほうが期間が短くても気付けない。
- ページングを最後まで辿ること。1ページで止めると、本数が黙って減る。
- `bulk/get` は key と endpoint+date の排他。両方渡せてしまうと、どちらが
  効いたのか出力から分からなくなる。
"""

from __future__ import annotations

import logging
from pathlib import Path

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


# --- CSV から取り込む ---------------------------------------------------------


def test_records_from_csv_keeps_the_api_field_names() -> None:
    """列名は JSON API と同じ。取り込み側に別の対応表を作らない。"""
    from stock_ai.data.jquants_bulk import records_from_csv

    payload = b"DiscDate,DiscTime,Code,CurPerType,FSales,FNP\n2024-05-10,15:30:00,86970,FY,100,20\n"

    records = records_from_csv(payload)

    assert records == [
        {
            "DiscDate": "2024-05-10",
            "DiscTime": "15:30:00",
            "Code": "86970",
            "CurPerType": "FY",
            "FSales": "100",
            "FNP": "20",
        }
    ]


def test_records_from_csv_strips_a_utf8_bom() -> None:
    """BOM が残ると、最初の列名が ``\\ufeffDiscDate`` になって誰も見つけられない。"""
    from stock_ai.data.jquants_bulk import records_from_csv

    records = records_from_csv("﻿Code,FSales\n86970,100\n".encode())

    assert records[0]["Code"] == "86970"


def test_group_by_symbol_uses_the_projects_four_digit_code() -> None:
    """**5桁のまま入れない。** データベースに2種類のコードが混ざる。"""
    from stock_ai.data.jquants_bulk import group_by_symbol

    grouped = group_by_symbol([{"Code": "86970"}, {"Code": "72030"}, {"Code": "86970"}])

    assert sorted(grouped) == ["7203", "8697"]
    assert len(grouped["8697"]) == 2


def test_group_by_symbol_drops_class_shares_rather_than_folding_them_in() -> None:
    """末尾が ``0`` でない5桁は優先株・種類株。**普通株に混ぜない。**"""
    from stock_ai.data.jquants_bulk import group_by_symbol

    grouped = group_by_symbol([{"Code": "86970"}, {"Code": "86975"}, {"Code": ""}, {}])

    assert sorted(grouped) == ["8697"]
    assert len(grouped["8697"]) == 1


def test_group_by_symbol_totals_reveal_the_dropped_rows() -> None:
    """呼ぶ側が突き合わせられること。黙って減るのがいちばん高く付く。"""
    from stock_ai.data.jquants_bulk import group_by_symbol

    records = [{"Code": "86970"}, {"Code": "86975"}, {"Code": ""}]
    grouped = group_by_symbol(records)

    assert len(records) - sum(len(rows) for rows in grouped.values()) == 2


def test_the_bulk_and_the_json_path_share_one_symbol_rule() -> None:
    """取り込みが自前の変換を持つと、片方だけ直したときに気付けない。"""
    from stock_ai.data.jquants_bulk import group_by_symbol
    from stock_ai.data.universe import four_digit_code

    for code in ("86970", "86975", "8697", "130A0", ""):
        expected = four_digit_code(code or None)
        grouped = group_by_symbol([{"Code": code}])
        assert list(grouped) == ([expected] if expected else [])


# --- 自己資本と純資産は別の量である ------------------------------------------
#
# 実測（トヨタ 7203、2026-09-04）: 一括取り込みが `Eq` を書いた結果、全年で
# 自己資本が 0.91〜1.10兆 増えた。**差はちょうど非支配株主持分。** EDINET が
# 入れていた親会社持分（26.2兆）が純資産（27.15兆）に化けていた。
# 同じ行の BPS は親会社持分ベースのままなので、1行に定義が2つ入った。


def _equity_record(**extra: str) -> dict[str, str]:
    return {"Code": "72030", "DiscDate": "2022-05-11", "CurPerType": "FY", **extra}


def test_equity_prefers_shareholders_equity_over_net_assets() -> None:
    """列は「自己資本」。``ShEq`` がそれで、``Eq`` は純資産である。"""
    from stock_ai.data.jquants_fundamentals import normalize_statements

    reports = normalize_statements("7203", [_equity_record(ShEq="262460", Eq="271548")])

    assert reports[0].equity == pytest.approx(262460)


def test_equity_falls_back_to_net_assets_but_says_so(caplog) -> None:
    """代用してよいが、**黙って代用しない。** ROE と PBR がこの列を読む。"""
    import logging

    from stock_ai.data.jquants_fundamentals import normalize_statements

    with caplog.at_level(logging.WARNING):
        reports = normalize_statements("7203", [_equity_record(Eq="271548")])

    assert reports[0].equity == pytest.approx(271548)
    assert any("ShEq" in message and "Eq" in message for message in caplog.messages)


def test_no_warning_when_shareholders_equity_is_present(caplog) -> None:
    """毎回警告が出ると、本当に混ざった回に誰も気付かない。"""
    import logging

    from stock_ai.data.jquants_fundamentals import normalize_statements

    with caplog.at_level(logging.WARNING):
        normalize_statements("7203", [_equity_record(ShEq="262460", Eq="271548")])

    assert not any("ShEq" in message for message in caplog.messages)


def test_the_two_equity_fields_are_not_treated_as_synonyms() -> None:
    """同じ意味なら順番はどうでもよい。**違う意味だから順番がある。**"""
    from stock_ai.data.jquants_fundamentals import (
        _EQUITY_FALLBACK_KEYS,
        _EQUITY_KEYS,
    )

    assert "ShEq" in _EQUITY_KEYS
    assert "Eq" in _EQUITY_FALLBACK_KEYS
    assert not set(_EQUITY_KEYS) & set(_EQUITY_FALLBACK_KEYS)


def test_the_snapshot_and_the_statements_use_the_same_equity() -> None:
    """**同じ records から出る2つの値が食い違わないこと。**

    片方だけ直したせいで、同じ銘柄の snapshot が純資産、statements が自己資本
    という状態が続いていた。部品はどちらも通っていて、突き合わせが無かった。

    snapshot は自己資本を列に持たないので、PBR の分母として取り出す。
    """
    import datetime as dt

    from stock_ai.data.jquants_fundamentals import normalize_statement, normalize_statements

    records = [_equity_record(ShEq="262460", Eq="271548", ShOutFY="1000")]

    snapshot = normalize_statement("7203", records, dt.date(2026, 9, 5), price=500.0)
    reports = normalize_statements("7203", records)

    assert snapshot.pbr is not None and snapshot.market_cap is not None
    assert snapshot.market_cap / snapshot.pbr == pytest.approx(reports[0].equity)


def test_the_snapshot_prefers_shareholders_equity_over_net_assets() -> None:
    """純資産のほうを掴むと、PBR の分母が非支配株主持分だけ大きくなる。"""
    import datetime as dt

    from stock_ai.data.jquants_fundamentals import normalize_statement

    snapshot = normalize_statement(
        "7203",
        [_equity_record(ShEq="262460", Eq="271548", ShOutFY="1000")],
        dt.date(2026, 9, 5),
        price=500.0,
    )

    # 純資産を掴んでいたら 500*1000/271548 = 1.841。例外は出ないので値で押さえる。
    assert snapshot.pbr == pytest.approx(500.0 * 1000 / 262460)
    assert snapshot.pbr != pytest.approx(500.0 * 1000 / 271548)


def test_the_snapshot_says_when_it_borrowed_net_assets(caplog) -> None:
    """代用してよいが、**黙って代用しない。**"""
    import datetime as dt
    import logging

    from stock_ai.data.jquants_fundamentals import normalize_statement

    with caplog.at_level(logging.INFO):
        snapshot = normalize_statement(
            "7203",
            [_equity_record(Eq="271548", ShOutFY="1000")],
            dt.date(2026, 9, 5),
            price=500.0,
        )

    assert snapshot.pbr == pytest.approx(500.0 * 1000 / 271548)
    assert any("純資産" in message for message in caplog.messages)


def test_the_snapshot_walks_back_for_equity_but_still_prefers_shareholders_equity() -> None:
    """新しい開示に自己資本があるなら、古い開示の純資産を掴まない。"""
    import datetime as dt

    from stock_ai.data.jquants_fundamentals import normalize_statement

    records = [
        {"Code": "72030", "DiscDate": "2021-05-11", "CurPerType": "FY", "Eq": "999999"},
        {
            "Code": "72030",
            "DiscDate": "2022-05-11",
            "CurPerType": "FY",
            "ShEq": "262460",
            "ShOutFY": "1000",
        },
    ]

    snapshot = normalize_statement("7203", records, dt.date(2026, 9, 5), price=500.0)

    assert snapshot.pbr == pytest.approx(500.0 * 1000 / 262460)


class TestBulkCsvEncoding:
    """一括 CSV の文字コード。**UTF-8 とは限らない。**

    配布サンプル（`sample_data_v2`）を実測すると、日本語を含むファイルは
    cp932 だった。日本語を含まないファイルだけが UTF-8 に見えている——
    **ASCII はどちらでも同じバイト列だからで、UTF-8 だと確かめられたわけでは
    ない。**

    いままで当たらなかったのは、一括で読んでいたのが `fins/summary` と
    `equities/bars/daily` の2つだけで、どちらにも日本語が無いためである。
    """

    SAMPLE = Path(__file__).parent / "fixtures" / "jquants_master_sample.csv"

    def test_the_distributed_master_is_cp932(self) -> None:
        """**配り方で文字コードが違う。**

        配布サンプルは cp932。**一括ファイルのほうは UTF-8 である**
        （2026-09-07 に実測。保存した385本の7エンドポイントすべてが
        `utf-8-sig` で、会社名の入る `/equities/master` も含む）。

        片方だけを見て決め打ちすると、もう片方で落ちる。**両方を固定する。**
        """
        raw = self.SAMPLE.read_bytes()

        with pytest.raises(UnicodeDecodeError):
            raw.decode("utf-8")
        assert "日本取引所グループ" in raw.decode("cp932")

    def test_a_cp932_company_name_comes_back_unmangled(self) -> None:
        """`utf-8-sig` 決め打ちだと、ここで例外が出て取り込みが止まる。"""
        from stock_ai.data.jquants_bulk import records_from_csv

        (row,) = records_from_csv(self.SAMPLE.read_bytes())

        assert row["CoName"] == "日本取引所グループ"
        assert row["MrgnNm"] == "貸借"

    def test_the_encoding_that_worked_is_reported(self) -> None:
        """何で読めたかを捨てない。**表に出せないと、化けても気付けない。**"""
        from stock_ai.data.jquants_bulk import decode_csv

        _text, encoding = decode_csv(self.SAMPLE.read_bytes())

        assert encoding == "cp932"

    def test_the_real_bulk_file_is_utf8(self) -> None:
        """実測（2026-09-07）を固定する。**サンプルとは違う。**

        `/equities/master` の一括ファイルには会社名が入っているのに
        `utf-8-sig` で読めた。cp932 だと決め打ちしていたら、ここで落ちていた。
        """
        from stock_ai.data.jquants_bulk import decode_csv

        payload = "Date,Code,CoName\n2026-09-07,86970,日本取引所グループ\n".encode()

        text, encoding = decode_csv(payload)

        assert encoding == "utf-8-sig"
        assert "日本取引所グループ" in text

    def test_utf8_is_tried_first(self) -> None:
        """cp932 はほぼ何でも読めてしまう。**先に試す順序に意味がある。**

        UTF-8 の日本語を cp932 として読むと、例外を出さずに化ける。順序を
        入れ替えると、いま通っている `fins/summary` まで静かに壊れる。
        """
        from stock_ai.data.jquants_bulk import decode_csv

        payload = "Code,CoName\n86970,日本取引所グループ\n".encode()
        text, encoding = decode_csv(payload)

        assert encoding == "utf-8-sig"
        assert "日本取引所グループ" in text

    def test_a_byte_order_mark_is_not_left_in_the_first_column_name(self) -> None:
        """BOM が残ると列名が `\\ufeffCode` になり、`row["Code"]` が空になる。"""
        from stock_ai.data.jquants_bulk import records_from_csv

        (row,) = records_from_csv("Code,Value\n86970,1\n".encode("utf-8-sig"))

        assert row["Code"] == "86970"

    def test_unreadable_bytes_are_read_but_warned_about(self, caplog) -> None:
        """最後の手段は置換だが、**黙って置換しない。**

        置換して黙ると、化けた会社名が表に並ぶ。例外は出ないし行数も合うので、
        気付く手掛かりが無くなる。
        """
        from stock_ai.data.jquants_bulk import decode_csv

        payload = b"Code,CoName\n86970," + bytes([0x81, 0x20, 0xFF, 0xFE]) + b"\n"
        with caplog.at_level(logging.WARNING):
            _text, encoding = decode_csv(payload)

        assert encoding == "utf-8/replace"
        assert caplog.records


class TestArchiveEndpointNames:
    """原本に残すエンドポイントの綴り。

    **綴りが違うと `DataError` が返る。それは「プランに入っていない」ときと
    見分けが付かない。** 最初は手で写して6本間違えており、2026-09-06 の下見で
    Light でも取れるはずの取引カレンダーと TOPIX が落ちて初めて分かった。

    Premium の週に同じことが起きれば、「Premium にも無いのだ」と読んで取らずに
    終わる。**そのときは契約が終わっていて、確かめ直せない。**
    """

    def test_the_archive_list_is_taken_from_the_bulk_list(self) -> None:
        """**2つ持たない。** 片方だけ直したときに気付けない。"""
        from stock_ai.data.jquants_bulk import ARCHIVE_ENDPOINTS, BULK_ENDPOINTS

        assert set(ARCHIVE_ENDPOINTS) <= set(BULK_ENDPOINTS)

    def test_only_the_add_ons_are_left_out(self) -> None:
        """分足とティックは通常プランとは別契約。他を落とすなら理由が要る。"""
        from stock_ai.data.jquants_bulk import (
            ARCHIVE_ADDONS,
            ARCHIVE_ENDPOINTS,
            BULK_ENDPOINTS,
        )

        assert set(BULK_ENDPOINTS) - set(ARCHIVE_ENDPOINTS) == set(ARCHIVE_ADDONS)

    @pytest.mark.parametrize(
        "wrong",
        [
            "/indices/topix",
            "/indices/daily",
            "/markets/trading-calendar",
            "/derivatives/futures",
            "/derivatives/options",
            "/derivatives/options-225",
        ],
    )
    def test_the_names_that_were_actually_wrong_stay_out(self, wrong: str) -> None:
        """実際に間違えた6本。**もっともらしく見えるから間違えた。**

        `jquants` CLI の短い名前（`idx daily` / `deriv futures`）に引きずられて
        いる。API のパスは `/indices/bars/daily` のように `bars/daily` が入る。
        """
        from stock_ai.data.jquants_bulk import ARCHIVE_ENDPOINTS

        assert wrong not in ARCHIVE_ENDPOINTS

    def test_every_endpoint_the_deadline_work_needs_is_in_the_archive(self) -> None:
        """期限ものが漏れていないこと。"""
        from stock_ai.data.jquants_bulk import ARCHIVE_ENDPOINTS, DEADLINE_ENDPOINTS

        assert set(DEADLINE_ENDPOINTS) <= set(ARCHIVE_ENDPOINTS)


class TestAgainstTheOfficialClient:
    """一括対応エンドポイントを、公式クライアントの一覧と突き合わせる。

    固定データ（`tests/fixtures/jquants_bulk_endpoints.txt`）は
    `jquants-api-client-python` の `BulkEndpoint`（コミット 4f9e404）から
    **生成した**もので、手で写していない。

    この突き合わせで `/fins/earnings-date` が1本抜けているのが見つかった
    （2026-09-06）。**抜けていても `DataError` は出ない。一覧に無いものは
    そもそも聞きに行かないので、出力に何も現れない。**
    """

    OFFICIAL = Path(__file__).parent / "fixtures" / "jquants_bulk_endpoints.txt"

    def _official(self) -> set[str]:
        lines = self.OFFICIAL.read_text(encoding="utf-8").splitlines()
        return {line.strip() for line in lines if line.strip() and not line.startswith("#")}

    def test_nothing_the_official_client_knows_is_missing(self) -> None:
        """**取り逃すのは、聞かないからである。**"""
        from stock_ai.data.jquants_bulk import BULK_ENDPOINTS

        assert not self._official() - set(BULK_ENDPOINTS)

    def test_the_endpoint_that_was_actually_missing(self) -> None:
        """実際に抜けていた1本。決算発表**予定日**である。"""
        from stock_ai.data.jquants_bulk import BULK_ENDPOINTS

        assert "/fins/earnings-date" in BULK_ENDPOINTS

    def test_the_calendar_is_kept_even_though_the_reference_data_omits_it(self) -> None:
        """**実測が一覧に勝つ。**

        J-Quants の `reference_data.json` の一括一覧（18本）には
        `/markets/calendar` が載っていない。しかし 2026-09-06 の下見で
        **1本返ってきている。** 向こうの表を信じて外すと、取れるものを取り
        逃す。
        """
        from stock_ai.data.jquants_bulk import ARCHIVE_ENDPOINTS

        assert "/markets/calendar" in ARCHIVE_ENDPOINTS
