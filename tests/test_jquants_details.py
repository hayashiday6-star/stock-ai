"""財務諸表（BS/PL/CF）の読み取り。

Premium 専用で20年ぶん。**いまの `fins/summary` には貸借対照表もキャッシュ
フローも無い**ので、ここが入ると EDINET の有報を1日1リクエストで数百日ぶん
走査する経路が要らなくなる。

固定データは配布サンプル `sample_data_v2` の
`Financial Statement Data(BSPLCF).csv` **そのまま**（cp932・CRLF）である。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from stock_ai.data.jquants_details import (
    StatementDetail,
    field_census,
    parse_details,
    parse_doc_type,
    parse_values,
)

SAMPLE = Path(__file__).parent / "fixtures" / "jquants_details_sample.csv"


def _sample() -> list[StatementDetail]:
    return parse_details(SAMPLE.read_bytes())


def test_the_distributed_sample_parses() -> None:
    """実物で通ること。4件、すべて日本取引所グループ。"""
    items = _sample()

    assert len(items) == 4
    assert {item.symbol for item in items} == {"8697"}
    assert items[0].disclosed_on == dt.date(2022, 1, 27)
    assert items[0].disclosed_at == dt.time(12, 0, 0)


def test_the_sample_is_cp932_like_the_rest() -> None:
    """**UTF-8 決め打ちだと、ここで例外が出て止まる。**"""
    raw = SAMPLE.read_bytes()

    assert b"\r\n" in raw
    assert "株式会社日本取引所グループ" in raw.decode("cp932")


def test_the_numbers_ride_in_one_dictionary_not_in_columns() -> None:
    """列が数百に分かれているのではない。1つの辞書である。"""
    items = _sample()

    assert len(items[0].values) == 69
    assert items[1].values["Assets (IFRS)"]


def test_the_annual_disclosure_carries_more_fields_than_a_quarter() -> None:
    """**件数が同じなら、通期を四半期と取り違えても気付けない。**"""
    quarterly, annual = _sample()[0], _sample()[1]

    assert quarterly.period == "3Q"
    assert annual.period == "FY"
    assert len(annual.values) > len(quarterly.values)


def test_consolidated_and_the_standard_come_from_the_doc_type() -> None:
    """**数字を見ても分からない。** `DocType` にしか書いていない。"""
    assert parse_doc_type("3QFinancialStatements_Consolidated_IFRS") == ("3Q", True, "IFRS")
    assert parse_doc_type("FYFinancialStatements_NonConsolidated_JP") == ("FY", False, "JP")


def test_an_unreadable_doc_type_says_it_does_not_know() -> None:
    """**`False` を入れない。**

    「単体だと分かっている」と「連結かどうか分からない」は別である。`False`
    にすると、分からないものが単体として集計に入る。
    """
    period, consolidated, standard = parse_doc_type("SomethingElse")

    assert period is None
    assert consolidated is None
    assert standard is None


def test_the_raw_doc_type_is_kept_beside_what_was_read_from_it() -> None:
    """読み違えたときに元へ戻れること。"""
    assert _sample()[0].doc_type == "3QFinancialStatements_Consolidated_IFRS"


def test_the_field_dictionary_is_python_not_json() -> None:
    """単引用符である。`margin-alert` の `PubReason` と同じ形。"""
    values = parse_values("{'Assets (IFRS)': '100', 'Equity (IFRS)': '40'}")

    assert values == {"Assets (IFRS)": "100", "Equity (IFRS)": "40"}


def test_a_lookup_is_exact_and_never_a_prefix() -> None:
    """**前方一致を許すと、少数株主分の有無が例外なしで入れ替わる。**

    `Equity (IFRS)` は資本全体、`Equity attributable to owners of parent
    (IFRS)` は親会社所有者帰属分。サンプルでは 311,381 と 303,261 百万円で、
    **どちらももっともらしい大きさである。**
    """
    item = _sample()[0]

    assert item.number_of("Equity (IFRS)") == 311_381_000_000
    assert item.number_of("Equity attributable to owners of parent (IFRS)") == 303_261_000_000
    assert item.value("Equity") is None  # 前方一致で拾わない


def test_a_missing_field_is_none_and_not_zero() -> None:
    """`0` を返すと「資産ゼロの会社」が並ぶ。合計も平均も通る。"""
    item = _sample()[0]

    assert item.number_of("Net sales") is None
    assert item.value("Net sales") is None


def test_the_alternatives_are_tried_in_the_order_given() -> None:
    """会計基準ごとの別名を並べるための順序。**先に書いたものが勝つ。**"""
    item = _sample()[0]

    assert item.number_of("Net sales", "Assets (IFRS)") == 62_076_519_000_000


def test_values_stay_strings_so_the_notation_does_not_become_a_type() -> None:
    """`'55967000000.0'` と `'62076519000000'` が混ざっている。"""
    values = _sample()[0].values

    assert values["Operating profit (loss) (IFRS)"] == "55967000000.0"
    assert values["Assets (IFRS)"] == "62076519000000"


def test_an_empty_string_counts_as_missing() -> None:
    """空欄を `0.0` にしない。"""
    item = StatementDetail(
        symbol="7203",
        disclosed_on=dt.date(2024, 1, 1),
        disclosed_at=None,
        number="1",
        doc_type="",
        period=None,
        consolidated=None,
        standard=None,
        values={"Assets": "  "},
    )

    assert item.value("Assets") is None


def test_the_census_groups_by_standard_and_consolidation() -> None:
    """**日本基準の鍵の名前は、サンプルからは分からない。**

    見ていないものを対応表に書けば出典の無い数字になる。原本を落としたあとに
    これを回して、証拠から書く。
    """
    census = field_census(_sample())

    assert list(census) == [("IFRS", True)]
    keys = dict(census[("IFRS", True)])
    assert keys["Assets (IFRS)"] == 4  # 4件すべてに出る
    assert keys["Net cash provided by (used in) operating activities (IFRS)"] == 1


def test_cash_flow_only_shows_up_in_the_annual_disclosure() -> None:
    """**四半期にキャッシュフローは無い。**

    サンプルの4件のうち、CF の鍵が出るのは通期の1件だけである。四半期の CF を
    使う因子を作ると、**例外は出ないまま値が1つも埋まらない**——年1回しか
    観測が無いことに、件数が少ないという形でしか気付けない。
    """
    quarterly, annual = _sample()[0], _sample()[1]
    field = "Net cash provided by (used in) operating activities (IFRS)"

    assert annual.number_of(field) is not None
    assert quarterly.number_of(field) is None


def test_the_census_puts_the_common_fields_first() -> None:
    """対応表を書くときに見る順序である。"""
    (first, _count), *_rest = field_census(_sample())[("IFRS", True)]

    assert first  # 名前は問わない。件数で並んでいることだけ見る
    counts = [count for _key, count in field_census(_sample())[("IFRS", True)]]
    assert counts == sorted(counts, reverse=True)


def test_a_row_without_a_disclosure_date_is_dropped() -> None:
    payload = b"DiscDate,DiscTime,Code,DiscNo,DocType,FS\n,12:00:00,86970,1,x,{}\n"

    assert parse_details(payload) == []


def test_an_unreadable_dictionary_does_not_take_the_row_with_it() -> None:
    """**開示があったことは残す。** 数字が読めないのとは別である。"""
    payload = b"DiscDate,DiscTime,Code,DiscNo,DocType,FS\n2022-01-27,12:00,86970,1,x,{'a':\n"

    (item,) = parse_details(payload)

    assert item.symbol == "8697"
    assert item.values == {}


class TestOfficialDocumentTypes:
    """公式の書類種別一覧（45通り）。

    出典は J-Quants の `j-quants-doc-mcp`（`reference_data.json`、コミット
    4f9e404）。**手で写していない。**

    最初は配布サンプルにあった `IFRS` だけを見て `IFRS` / `JP` / `US` の3つを
    書いていた。実際には `JMIS`・`Foreign`・`REIT` があり、**16通りで会計基準が
    読めていなかった。** 期間も `OtherPeriod` を落としていて8通り。どちらも
    例外は出ず、`None` が並ぶだけである。
    """

    def test_every_official_financial_statement_type_parses_completely(self) -> None:
        """**45通り全部を通す。** 1つでも読めなければ、そこが黙って欠ける。"""
        from stock_ai.data.jquants_details import DOCUMENT_TYPES

        unread = [
            name
            for name in DOCUMENT_TYPES
            if "FinancialStatements" in name and None in parse_doc_type(name)
        ]

        assert not unread

    def test_the_types_the_first_version_could_not_read(self) -> None:
        """実際に落としていた形。**もっともらしく見えるから落とした。**"""
        assert parse_doc_type("OtherPeriodFinancialStatements_Consolidated_JP")[0] == "OtherPeriod"
        assert parse_doc_type("FYFinancialStatements_Consolidated_JMIS")[2] == "JMIS"
        assert parse_doc_type("FYFinancialStatements_Consolidated_Foreign")[2] == "Foreign"
        assert parse_doc_type("FYFinancialStatements_Consolidated_REIT")[2] == "REIT"

    def test_the_period_match_prefers_the_longer_name(self) -> None:
        """`OtherPeriod` を短いものより後に置くと、先に当たった側が勝つ。"""
        from stock_ai.data.jquants_details import KNOWN_PERIODS

        assert KNOWN_PERIODS[0] == "OtherPeriod"

    def test_a_forecast_revision_has_no_period_and_that_is_correct(self) -> None:
        """`EarnForecastRevision` は決算短信ではない。**期も基準も無い。**"""
        assert parse_doc_type("EarnForecastRevision") == (None, None, None)

    def test_a_type_outside_the_official_list_is_flagged(self) -> None:
        """**載っていなければ、読み取りは推測である。**

        一覧が増えたときに黙って `None` を並べるのではなく、ここで分かる。
        """
        from stock_ai.data.jquants_details import describe_doc_type, is_known_doc_type

        assert is_known_doc_type("EarnForecastRevision")
        assert not is_known_doc_type("SomethingNew_Consolidated_JP")
        assert describe_doc_type("EarnForecastRevision") == "業績予想の修正"

    def test_the_sample_type_is_in_the_official_list(self) -> None:
        """実物と定義表が噛み合っていること。"""
        from stock_ai.data.jquants_details import is_known_doc_type

        assert is_known_doc_type(_sample()[0].doc_type)


class TestRevisionCensus:
    """予想修正が、決算発表と**別の日に**出ているか。

    **説#5 を閉じた理由そのものを測る。** 記録にはこうある。

        予想修正は**独立した開示として取得できない**。イベント日が決算発表日
        と重なる。

    公式の書類種別一覧には `EarnForecastRevision` が**独立した種別として載って
    いる。** 載っていることと、別の日に出ることは別で、**後者が問題である。**
    """

    def _item(self, symbol: str, day: str, doc_type: str) -> StatementDetail:
        return StatementDetail(
            symbol=symbol,
            disclosed_on=dt.date.fromisoformat(day),
            disclosed_at=None,
            number="1",
            doc_type=doc_type,
            period=None,
            consolidated=None,
            standard=None,
            values={},
        )

    def test_a_revision_on_its_own_day_counts_as_standalone(self) -> None:
        """**これが独立イベントである。**"""
        from stock_ai.data.jquants_details import revision_census

        items = [
            self._item("7203", "2024-01-15", "EarnForecastRevision"),
            self._item("7203", "2024-02-05", "3QFinancialStatements_Consolidated_JP"),
        ]

        census = revision_census(items)

        assert census.revisions == 1
        assert census.standalone == 1
        assert census.on_statement_day == 0

    def test_a_revision_on_the_earnings_day_is_not_independent(self) -> None:
        """**#5 を閉じた理由は、こちらが大半であることだった。**"""
        from stock_ai.data.jquants_details import revision_census

        items = [
            self._item("7203", "2024-02-05", "EarnForecastRevision"),
            self._item("7203", "2024-02-05", "3QFinancialStatements_Consolidated_JP"),
        ]

        census = revision_census(items)

        assert census.on_statement_day == 1
        assert census.standalone == 0

    def test_the_same_day_at_another_symbol_does_not_count(self) -> None:
        """**銘柄をまたいで「同じ日」と数えない。**

        決算発表は日ごとに何百件もある。日付だけで突き合わせると、ほぼ全部が
        「決算と同じ日」になり、独立イベントが消える。
        """
        from stock_ai.data.jquants_details import revision_census

        items = [
            self._item("7203", "2024-02-05", "EarnForecastRevision"),
            self._item("6758", "2024-02-05", "3QFinancialStatements_Consolidated_JP"),
        ]

        census = revision_census(items)

        assert census.standalone == 1

    def test_dividend_revisions_count_too(self) -> None:
        from stock_ai.data.jquants_details import revision_census

        census = revision_census([self._item("7203", "2024-01-15", "DividendForecastRevision")])

        assert census.revisions == 1

    def test_a_statement_is_not_a_revision(self) -> None:
        from stock_ai.data.jquants_details import revision_census

        census = revision_census(
            [self._item("7203", "2024-02-05", "FYFinancialStatements_Consolidated_JP")]
        )

        assert census.revisions == 0
        assert census.doc_types["FYFinancialStatements_Consolidated_JP"] == 1

    def test_counting_across_files_adds_up(self) -> None:
        """月ごとの原本を跨いで数える。**ファイルごとに数え直さない。**"""
        from stock_ai.data.jquants_details import RevisionCensus, revision_census

        census = RevisionCensus()
        revision_census([self._item("7203", "2024-01-15", "EarnForecastRevision")], census)
        revision_census([self._item("6758", "2024-02-15", "EarnForecastRevision")], census)

        assert census.revisions == 2
        assert census.symbols == {"7203", "6758"}

    def test_no_revisions_says_so_rather_than_dividing_by_zero(self) -> None:
        from stock_ai.data.jquants_details import revision_census

        census = revision_census([])

        assert "1件も無い" in census.summary()
