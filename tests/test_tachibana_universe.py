"""立花の銘柄マスタが J-Quants の銘柄一覧の代わりになるか。

レコードの形は本番の実測（2026-09-03、v4r10）から取っている。マニュアルの
例ではなく、実際に返ってきた値を使う。マニュアルには項目名しか書いておらず、
値が入っているかは別の話だった（上場廃止日は 4,441 件中 4,418 件が空）。
"""

from __future__ import annotations

import pytest

from stock_ai.core.exceptions import DataError
from stock_ai.data.sectors import Sector
from stock_ai.data.tachibana_universe import TachibanaUniverse, normalize_masters
from stock_ai.data.universe import Segment


def _kabu(code: str, *, name: str = "極　　洋", sector: str = "0050") -> dict[str, str]:
    return {
        "sIssueCode": code,
        "sIssueName": name,
        "sIssueNameRyaku": name.replace("　", ""),
        "sIssueNameKana": "キヨクヨウ",
        "sIssueNameEizi": "KYOKUYO",
        "sTokuteiF": "1",
        "sZyouzyouHakkouKabusu": "12078283",
        "sBaibaiTani": "100",
        "sBaibaiTaniYoku": "100",
        # 実測では 4,441 件中 4,255 件がこの半角スペースだった。空文字ではない。
        "sBaibaiTeisiC": " ",
        "sHosyoukinDaiyouKakeme": "80.000000",
        "sDaiyouHyoukaTanka": "4700.000000",
        "sYusenSizyou": "00",
        "sGyousyuCode": sector,
    }


def _sizyou(code: str, *, kubun: str = "01", delisted: str = "00000000") -> dict[str, str]:
    return {
        "sIssueCode": code,
        "sZyouzyouSizyou": "00",
        "sNehabaMin": "4000.000000",
        "sNehabaMax": "5400.000000",
        "sIssueKubunC": " ",
        "sSinyouC": "1",
        "sSinkiZyouzyouDay": "00000000",
        "sIssueBubetuC": "1",
        "sZenzituOwarine": "4700.000000",
        "sZyouzyouKubun": kubun,
        "sZyouzyouHaisiDay": delisted,
        "sYobineTaniNumber": "101",
        "sYobineTaniNumberYoku": "101",
    }


def test_the_listing_segment_comes_from_the_market_master() -> None:
    # 上場区分は銘柄市場マスタ側にしかない。銘柄マスタだけでは区分で絞れない。
    kabu = [_kabu("1301"), _kabu("4385"), _kabu("7203")]
    sizyou = [
        _sizyou("1301", kubun="01"),  # プライム
        _sizyou("4385", kubun="09"),  # グロース
        _sizyou("7203", kubun="02"),  # スタンダード
    ]

    prime = normalize_masters(kabu, sizyou, Segment.PRIME)
    growth = normalize_masters(kabu, sizyou, Segment.GROWTH)

    assert [p.symbol for p in prime] == ["1301"]
    assert [p.symbol for p in growth] == ["4385"]


def test_sector_uses_the_same_tse33_codes_as_jquants() -> None:
    # sGyousyuCode は J-Quants と同じ4桁コードなので、変換表を作り直さない。
    # 別に作ると、片方だけ更新されて黙ってずれる。
    found = normalize_masters([_kabu("6501", sector="3650")], [_sizyou("6501")], Segment.PRIME)

    assert found[0].sector == str(Sector.TECHNOLOGY)
    assert found[0].industry == "3650"


def test_etfs_and_reits_are_excluded_by_their_listing_kubun() -> None:
    # 実測では上場区分 00 が 543 件、TPM(21) が 187 件あった。ETF・REIT などで、
    # 事業会社ではない。Segment.ALL でもこれらは入れない。
    kabu = [_kabu("1301"), _kabu("1306", sector="9999"), _kabu("9999")]
    sizyou = [
        _sizyou("1301", kubun="01"),
        _sizyou("1306", kubun="00"),  # ETF
        _sizyou("9999", kubun="21"),  # TPM
    ]

    assert [p.symbol for p in normalize_masters(kabu, sizyou, Segment.ALL)] == ["1301"]


def test_foreign_listings_map_onto_the_segment_they_trade_in() -> None:
    # 03/04/11 は外国銘柄で、実測では5件しかない。少ないことは黙って消してよい
    # 理由にならない。東証に上場しているのに universe に出てこなくなる。
    kabu = [_kabu("1301"), _kabu("1302")]
    sizyou = [_sizyou("1301", kubun="03"), _sizyou("1302", kubun="11")]

    assert [p.symbol for p in normalize_masters(kabu, sizyou, Segment.PRIME)] == ["1301"]
    assert [p.symbol for p in normalize_masters(kabu, sizyou, Segment.GROWTH)] == ["1302"]


def test_a_symbol_missing_from_the_stock_master_is_dropped_not_guessed() -> None:
    # 市場マスタにあって銘柄マスタに無い場合、名前も業種も取れない。
    # 推測で埋めるより落とす。
    found = normalize_masters([], [_sizyou("1301")], Segment.PRIME)

    assert found == []


def test_blank_padded_values_are_treated_as_missing() -> None:
    # 立花は「値が無い」を半角スペースで返すことがある。str として真だからと
    # いって通すと、空白を区分値として扱ってしまう。
    row = _sizyou("1301")
    row["sZyouzyouKubun"] = " "

    assert normalize_masters([_kabu("1301")], [row], Segment.ALL) == []


def test_the_universe_refuses_an_empty_master_rather_than_returning_nothing() -> None:
    # 0件を「その区分に銘柄が無い」として静かに返すと、認証や版の取り違えが
    # 空のユニバースとして通ってしまう。
    source = TachibanaUniverse(lambda: ([], []))

    with pytest.raises(DataError, match="銘柄市場マスタ"):
        source.profiles(Segment.PRIME)


def test_the_universe_sorts_by_code_and_honours_the_limit() -> None:
    codes = ["7203", "1301", "4385"]
    source = TachibanaUniverse(
        lambda: ([_kabu(c) for c in codes], [_sizyou(c) for c in codes]),
    )

    assert [p.symbol for p in source.profiles(Segment.PRIME)] == ["1301", "4385", "7203"]
    assert [p.symbol for p in source.profiles(Segment.PRIME, limit=2)] == ["1301", "4385"]
    assert source.name == "tachibana"


# --- 落ちる経路はすべて数える -------------------------------------------------
#
# 実測（2026-09-03）でプライムが見込み 1,556 に対して 1,549 だった。7件が
# どこで落ちたのかを、ログからは特定できなかった。**件数が合わないときに
# 推測するしかない状態になるのが、この実装のいちばんまずい欠陥である。**


def test_a_code_that_is_not_four_characters_is_counted_not_dropped_silently(
    caplog: pytest.LogCaptureFixture,
) -> None:
    row = _sizyou("1301")
    row["sIssueCode"] = "13010"  # 5桁

    with caplog.at_level("WARNING"):
        found = normalize_masters([_kabu("1301")], [row], Segment.PRIME)

    assert found == []
    assert "4桁でない 1 件" in caplog.text
    # 件数だけでは、普通株でないものか項目の形が変わったのかを決められない。
    assert "13010" in caplog.text


def test_a_duplicate_code_is_counted_because_a_dict_would_hide_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # dict に入れると後勝ちで静かに1件消える。合計が合わない原因になる。
    with caplog.at_level("WARNING"):
        found = normalize_masters(
            [_kabu("1301")], [_sizyou("1301"), _sizyou("1301")], Segment.PRIME
        )

    assert len(found) == 1
    assert "重複" in caplog.text


def test_a_bad_code_in_the_stock_master_is_counted_separately(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # 銘柄マスタ側で落ちると、市場マスタ側では「対応が無い」に化ける。
    # どちらで落ちたのかが分かるように、別々に数える。
    bad = _kabu("1301")
    bad["sIssueCode"] = "1"

    with caplog.at_level("WARNING"):
        normalize_masters([bad], [_sizyou("1301")], Segment.PRIME)

    assert "1 件（銘柄マスタ）" in caplog.text
    assert "銘柄マスタに対応が無い 1 件" in caplog.text
