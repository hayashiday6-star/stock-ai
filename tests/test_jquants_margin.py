"""日々公表信用取引残高の読み取り。

**候補9 の材料である。** 遡って手に入る経路はここしか見つかっていないので、
読み違えても比べる相手がいない。**例外を出さずに間違う形**を1つずつ押さえる。

固定データ（`tests/fixtures/jquants_margin_alert_sample.csv`）は J-Quants の
配布サンプル `sample_data_v2` の `Daily Margin Interest.csv` **そのまま**で
ある。作り物ではない。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from stock_ai.data.jquants_margin import (
    REASONS,
    MarginAlert,
    onsets,
    parse_alerts,
    parse_number,
    parse_reasons,
)

SAMPLE = Path(__file__).parent / "fixtures" / "jquants_margin_alert_sample.csv"


def _sample() -> bytes:
    return SAMPLE.read_bytes()


def test_the_distributed_sample_parses() -> None:
    """実物で通ること。**fixture は実物から作る**（プロジェクトの規則）。"""
    alerts = parse_alerts(_sample())

    assert [item.symbol for item in alerts] == ["1321", "1321"]
    assert [item.published for item in alerts] == [dt.date(2023, 7, 5), dt.date(2023, 7, 6)]


def test_the_publication_date_and_the_as_of_date_stay_apart() -> None:
    """**1営業日ずれる。** 時点をイベント日に置くと、まだ公表されていない日の
    値動きを使うことになる。
    """
    first = parse_alerts(_sample())[0]

    assert first.published == dt.date(2023, 7, 5)
    assert first.as_of == dt.date(2023, 7, 4)
    assert first.as_of < first.published


def test_a_dash_is_missing_and_not_zero() -> None:
    """**「増減なし」と「前の値が無い」は違う。**

    サンプルの1行目は増減が `-`、2行目は `9464.0`。`-` を `0` にすると、
    規制の掛かった日の残高変化が「変化なし」として並ぶ。表は埋まるし、例外も
    出ない。
    """
    first, second = parse_alerts(_sample())

    assert first.short_change is None  # `-` である
    assert second.short_change == 9464.0


def test_a_real_zero_is_kept_as_zero() -> None:
    """欠測を `None` にする話が、**本物の 0 まで消して**は逆に壊れる。"""
    assert parse_number("0.0") == 0.0
    assert parse_number("0") == 0.0


def test_an_asterisk_is_missing_too() -> None:
    """比率の列は `*` で欠測を書く。**別の記号だからと素通りさせない。**"""
    assert parse_number("*") is None


def test_the_reason_field_is_python_not_json() -> None:
    """単引用符の辞書である。`json.loads` では読めない。

    `json` は例外を出すので気付けるが、**正規表現で拾おうとすると黙って全部
    落ちる**——旗が1つも立っていない、という結果になる。
    """
    text = "{'Restricted': '0', 'DailyPublication': '1', 'Monitoring': '0'}"

    assert parse_reasons(text) == frozenset({"DailyPublication"})


def test_only_the_flags_that_are_on_come_back() -> None:
    """**`'0'` を「有る」に数えない。** 辞書をそのまま真偽に通すと `'0'` は真。"""
    first = parse_alerts(_sample())[0]

    assert first.reasons == frozenset({"PrecautionByJSF"})
    assert not first.restricted


def test_every_documented_flag_appears_in_the_sample() -> None:
    """名前を写し間違えていないこと。**旗の名前は出典のある値である。**"""
    text = SAMPLE.read_text(encoding="utf-8")

    for name in REASONS:
        assert f"'{name}'" in text


def test_an_unreadable_reason_does_not_take_the_row_with_it() -> None:
    """1つの壊れた値で、その日ぶんの残高まで捨てない。"""
    assert parse_reasons("{'Restricted': ") == frozenset()
    assert parse_reasons(None) == frozenset()
    assert parse_reasons("-") == frozenset()


def test_the_regulation_class_keeps_its_leading_zero() -> None:
    """`001` を数にすると `1` になり、`010` と区別が付かなくなる。"""
    first = parse_alerts(_sample())[0]

    assert first.regulation == "001"


def _alert(symbol: str, day: str, *flags: str) -> MarginAlert:
    return MarginAlert(
        symbol=symbol,
        published=dt.date.fromisoformat(day),
        as_of=None,
        reasons=frozenset(flags),
        regulation=None,
        short_outstanding=None,
        long_outstanding=None,
        short_change=None,
        long_change=None,
        ratio=None,
    )


def test_only_the_day_the_flag_turns_on_is_an_event() -> None:
    """規制は何十日も続く。**続いている日を全部数えると、1回が何十件になる。**"""
    rows = [
        _alert("7203", "2024-01-04"),
        _alert("7203", "2024-01-05", "Restricted"),
        _alert("7203", "2024-01-09", "Restricted"),
        _alert("7203", "2024-01-10", "Restricted"),
    ]

    assert onsets(rows) == [("7203", dt.date(2024, 1, 5))]


def test_the_first_observation_is_never_an_onset() -> None:
    """**データの先頭は「そこで立った」ではなく「そこから見え始めた」である。**

    数えると取り込み開始日に人為的な山ができる。例外は出ない。件数が増えて
    見栄えはむしろ良くなる——だから気付けない。
    """
    rows = [
        _alert("7203", "2024-01-04", "Restricted"),
        _alert("7203", "2024-01-05", "Restricted"),
    ]

    assert onsets(rows) == []


def test_the_flag_going_off_and_on_again_is_two_events() -> None:
    """外れてから掛け直された規制は、別の回である。"""
    rows = [
        _alert("7203", "2024-01-04"),
        _alert("7203", "2024-01-05", "Restricted"),
        _alert("7203", "2024-02-01"),
        _alert("7203", "2024-03-01", "Restricted"),
    ]

    assert onsets(rows) == [("7203", dt.date(2024, 1, 5)), ("7203", dt.date(2024, 3, 1))]


def test_symbols_do_not_bleed_into_each_other() -> None:
    """銘柄をまたいで「前の日」を見ると、隣の銘柄の状態で判定してしまう。"""
    rows = [
        _alert("7203", "2024-01-04", "Restricted"),
        _alert("6758", "2024-01-05", "Restricted"),
    ]

    assert onsets(rows) == []


def test_rows_arriving_out_of_order_still_read_in_order() -> None:
    """月ごとのファイルを繋ぐと、日付順に並んでいるとは限らない。"""
    rows = [
        _alert("7203", "2024-01-05", "Restricted"),
        _alert("7203", "2024-01-04"),
    ]

    assert onsets(rows) == [("7203", dt.date(2024, 1, 5))]


def test_a_different_flag_can_be_asked_for() -> None:
    """増担保だけが規制ではない。日々公表・監理でも同じ数え方が要る。"""
    rows = [
        _alert("7203", "2024-01-04"),
        _alert("7203", "2024-01-05", "DailyPublication"),
    ]

    assert onsets(rows, reason="DailyPublication") == [("7203", dt.date(2024, 1, 5))]
    assert onsets(rows, reason="Restricted") == []


def test_a_row_without_a_publication_date_is_dropped() -> None:
    """日付の無いイベントは並べようがない。**`date.min` を入れると先頭に固まる。**"""
    payload = b"PubDate,Code,AppDate,PubReason\n,13210,2023-07-04,{}\n"

    assert parse_alerts(payload) == []


def test_a_share_class_code_is_dropped_rather_than_truncated() -> None:
    """5桁の末尾が `0` でないものは普通株ではない。**4桁に丸めない。**"""
    payload = b"PubDate,Code,AppDate,PubReason\n2023-07-05,13215,2023-07-04,{}\n"

    assert parse_alerts(payload) == []


def test_the_fixture_still_has_the_line_endings_it_arrived_with() -> None:
    """**整えたコピーで通しても、実物で通したことにはならない。**

    配布物は CRLF である。git は既定で LF に直してしまうので、`.gitattributes`
    で除外してある。その設定が外れても例外は出ない——テストは緑のまま、実物と
    違うものを読んでいることになる。ここで気付く。
    """
    assert b"\r\n" in SAMPLE.read_bytes()


class TestOfficialCodes:
    """公式の定義表から写した符号。

    出典は J-Quants の `j-quants-doc-mcp`（`reference_data.json`、コミット
    4f9e404）。**手で写していない**——前に一覧を手で写して6本綴りを間違えた。
    """

    def test_the_flag_names_match_what_the_sample_actually_contained(self) -> None:
        """公式の定義と、配布サンプルに出ていた6つが**一致すること。**

        片方だけ見ていると、名前が変わったことに気付けない。
        """
        from stock_ai.data.jquants_margin import REASON_DESCRIPTIONS

        text = SAMPLE.read_text(encoding="utf-8")

        assert len(REASON_DESCRIPTIONS) == 6
        for name in REASON_DESCRIPTIONS:
            assert f"'{name}'" in text

    def test_the_restriction_flag_is_not_the_daily_publication_flag(self) -> None:
        """**混ぜると、規制の掛かっていない銘柄がイベントに入る。**

        `Restricted` は東証の規制措置、`DailyPublication` は残高を毎日公表する
        指定であって、規制そのものではない。
        """
        from stock_ai.data.jquants_margin import REASON_DESCRIPTIONS

        assert "規制措置銘柄" in REASON_DESCRIPTIONS["Restricted"]
        assert "日々公表銘柄" in REASON_DESCRIPTIONS["DailyPublication"]

    def test_the_regulation_codes_are_described_not_ranked(self) -> None:
        """**`101` は「規制解除」である。**

        数として扱うと、解除が最も強い規制として並ぶ。
        """
        from stock_ai.data.jquants_margin import (
            MARGIN_REGULATION_CODES,
            REGULATION_RELEASED,
            describe_regulation,
        )

        assert len(MARGIN_REGULATION_CODES) == 8
        assert "解除" in MARGIN_REGULATION_CODES[REGULATION_RELEASED]
        assert describe_regulation("001") is not None
        assert describe_regulation("002") is not None

    def test_the_code_in_the_sample_is_one_of_the_official_ones(self) -> None:
        """実物と定義表が噛み合っていること。"""
        from stock_ai.data.jquants_margin import MARGIN_REGULATION_CODES

        first = parse_alerts(_sample())[0]

        assert first.regulation in MARGIN_REGULATION_CODES

    def test_an_unknown_code_is_not_read_as_no_regulation(self) -> None:
        """**知らない符号を「規制なし」と読まない。**

        増えたときに黙って落とすと、その銘柄だけイベントから消える。
        """
        from stock_ai.data.jquants_margin import describe_regulation

        assert describe_regulation("999") is None
        assert describe_regulation(None) is None
