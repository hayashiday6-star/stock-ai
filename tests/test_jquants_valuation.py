"""1株あたりの指標と時価総額を読む。

**時価総額を自前で組み立てない**ための読み口である。株価 × 発行済株式数 は、
分割を跨ぐと尺度が変わる——このプロジェクトが繰り返し踏んでいる形そのもの。

ここで押さえるのは3つ。

1. **空を 0 にしない。** PBR が空の銘柄と PBR が 0 の銘柄は別のもので、0 を
   入れると「いちばん割安」な顔をして選別に入ってくる。
2. **実績と会社予想を混ぜない。** 混ぜれば、発表前から予想を知っていたことに
   なる。
3. **このファイルだけで中身を確かめられる。** `PER × EPS` と `PBR × BPS` は
   どちらも終値を指す。合わなければ列の意味が想像と違う。
"""

from __future__ import annotations

import datetime as dt
import gzip
from pathlib import Path

from stock_ai.data.jquants_valuation import (
    IDENTITY_FLOOR,
    census,
    decimals_seen,
    digit_spread,
    explain_gap,
    from_archive,
    half_widths,
    identity_check,
    implied_shares,
    parse_valuation,
    resolve,
    rounding_bound,
    unknown_columns,
)
from stock_ai.data.schema import DATE

HEADER = "Date,Code,EPS,FwdEPS,BPS,ROE,FwdROE,PER,FwdPER,PBR,MktCap"


def _csv(*rows: str) -> bytes:
    return ("\n".join([HEADER, *rows]) + "\n").encode("utf-8")


#: 終値 2,500 円をちょうど指す行。`PER × EPS` も `PBR × BPS` も 2,500。
CONSISTENT = "2026-08-03,13010,100,120,2000,5.0,6.0,25.0,20.8,1.25,250000000000"


class TestReadingTheElevenColumns:
    """列は原本から数えた11。**想像で足さない。**"""

    def test_the_actual_and_forecast_columns_land_in_separate_places(self) -> None:
        frame = parse_valuation(_csv(CONSISTENT))

        row = frame.iloc[0]
        assert row["eps"] == 100
        assert row["forward_eps"] == 120
        assert row["per"] == 25.0
        assert row["forward_per"] == 20.8

    def test_the_code_becomes_the_four_digit_symbol(self) -> None:
        assert parse_valuation(_csv(CONSISTENT)).iloc[0]["symbol"] == "1301"

    def test_the_date_is_a_date(self) -> None:
        assert parse_valuation(_csv(CONSISTENT)).iloc[0][DATE] == dt.date(2026, 8, 3)

    def test_the_market_cap_is_read_as_given(self) -> None:
        # **単位を推測して割らない。** 原本の値をそのまま持つ。
        assert parse_valuation(_csv(CONSISTENT)).iloc[0]["market_cap"] == 250_000_000_000


class TestEmptyIsNotZero:
    """**空の PBR を 0 にすると、いちばん割安な顔をして選別に入ってくる。**"""

    def test_a_blank_stays_missing(self) -> None:
        blank = "2008-06-02,13010,,,,,,,,,"
        frame = parse_valuation(_csv(blank))

        assert frame.iloc[0]["pbr"] is None or frame.iloc[0]["pbr"] != frame.iloc[0]["pbr"]
        assert frame.iloc[0]["market_cap"] != 0

    def test_a_real_zero_is_kept(self) -> None:
        zero = "2026-08-03,13010,0,0,0,0,0,0,0,0,0"
        assert parse_valuation(_csv(zero)).iloc[0]["pbr"] == 0


class TestRowsThatCannotBeKeyed:
    def test_a_preferred_share_code_is_dropped(self) -> None:
        # 5桁で末尾が 0 でないものは普通株ではない。判断は `four_digit_code`
        # に借りている——**符号の規則を2つ持たない。**
        odd = "2026-08-03,13015,100,120,2000,5.0,6.0,25.0,20.8,1.25,1"
        assert parse_valuation(_csv(odd)).empty

    def test_a_row_without_a_date_is_dropped(self) -> None:
        undated = ",13010,100,120,2000,5.0,6.0,25.0,20.8,1.25,1"
        assert parse_valuation(_csv(undated)).empty

    def test_an_empty_file_gives_the_columns_anyway(self) -> None:
        # **空の表でも列があること。** 無いと、呼ぶ側が KeyError で落ちる。
        frame = parse_valuation(_csv())
        assert "market_cap" in frame.columns
        assert frame.empty


class TestNoticingANewColumn:
    """**向こうが列を増やしても例外は出ない。** 黙って読み飛ばす。"""

    def test_an_unknown_column_is_named(self) -> None:
        payload = (HEADER + ",EV\n2026-08-03,13010,1,1,1,1,1,1,1,1,1,9\n").encode("utf-8")
        assert unknown_columns(payload) == ["EV"]

    def test_the_known_eleven_raise_nothing(self) -> None:
        assert unknown_columns(_csv(CONSISTENT)) == []


class TestCheckingTheFileAgainstItself:
    """``PER × EPS`` と ``PBR × BPS`` は、どちらも終値を指す。"""

    def test_a_consistent_row_agrees(self) -> None:
        report = identity_check(parse_valuation(_csv(CONSISTENT)))

        assert report.checked == 1
        assert report.agreed == 1
        assert report.rate == 1.0

    def test_a_row_read_wrong_does_not_agree(self) -> None:
        # PBR だけ倍にすると、2つの掛け算が別の終値を指す。
        broken = "2026-08-03,13010,100,120,2000,5.0,6.0,25.0,20.8,2.50,250000000000"
        report = identity_check(parse_valuation(_csv(broken)))

        assert report.checked == 1
        assert report.agreed == 0
        assert report.worst and report.worst[0][1] == "1301"

    def test_a_blank_row_is_not_counted_as_a_disagreement(self) -> None:
        # **「合わない」と「判定できない」を分ける。** 分けないと、2008年頃の
        # 空の多さがそのまま不一致率として出て、読み違いと見分けが付かない。
        blank = "2008-06-02,13010,,,,,,,,,"
        report = identity_check(parse_valuation(_csv(blank)))

        assert report.checked == 0
        assert report.skipped_missing == 1
        assert report.rate == 0.0

    def test_a_near_zero_eps_is_not_counted_either(self) -> None:
        # EPS が 0 近傍だと、PER の丸め誤差が何倍にもなって出てくる。
        tiny = f"2026-08-03,13010,{IDENTITY_FLOOR / 100},1,2000,5.0,6.0,25.0,20.8,1.25,1"
        report = identity_check(parse_valuation(_csv(tiny)))

        assert report.checked == 0
        assert report.skipped_small == 1

    def test_rounding_alone_still_agrees(self) -> None:
        # 終値は円で丸められ、EPS・BPS は小数である。ぴったりは一致しない。
        rounded = "2026-08-03,13010,100,120,1999,5.0,6.0,25.0,20.8,1.2506,1"
        assert identity_check(parse_valuation(_csv(rounded))).agreed == 1

    def test_an_empty_frame_reports_nothing_rather_than_dividing_by_zero(self) -> None:
        assert identity_check(parse_valuation(_csv())).rate == 0.0


class TestAProductThatComesOutZero:
    """**1,588万行で落ちた形。** fixture が小さすぎて出なかった。

    `pbr` が 0 なら、`bps` がどれだけ大きくても `pbr × bps` は 0 になる。
    割り算の分母になるので、そこを `pd.NA` で塞いだら列が object 型になり、
    並べ替えが `TypeError` で落ちた（2026-09-15）。

    **コメントには「片方が 0 になる行は判定から外れているはず」と書いてあった。**
    書いたが、確かめていなかった。積に床を当てれば前提そのものが要らない。
    """

    def _zero_pbr(self) -> str:
        # bps は十分大きいが、pbr が 0。積は 0 になる。
        return "2026-08-03,13020,100,120,2000,5.0,6.0,25.0,20.8,0,1"

    def test_it_does_not_raise(self) -> None:
        frame = parse_valuation(_csv(CONSISTENT, self._zero_pbr()))

        identity_check(frame)  # 落ちないこと自体が主張である

    def test_a_zero_product_is_not_counted_as_a_disagreement(self) -> None:
        """**「合わない」と「判定できない」を分ける。** 0 は判定できない側。"""
        report = identity_check(parse_valuation(_csv(CONSISTENT, self._zero_pbr())))

        assert report.checked == 1
        assert report.agreed == 1
        assert report.skipped_small == 1
        assert report.rate == 1.0

    def test_the_worst_list_still_sorts_when_a_zero_row_is_present(self) -> None:
        """並べ替えの列が数のままであること。**ここが落ちた箇所である。**"""
        broken = "2026-08-04,13030,100,120,2000,5.0,6.0,25.0,20.8,2.50,1"
        report = identity_check(parse_valuation(_csv(CONSISTENT, self._zero_pbr(), broken)))

        assert report.agreed == 1
        assert [symbol for _, symbol, _, _ in report.worst] == ["1303"]

    def test_a_zero_eps_row_lands_in_the_same_bucket(self) -> None:
        zero_eps = "2026-08-05,13040,0,120,2000,5.0,6.0,25.0,20.8,1.25,1"
        report = identity_check(parse_valuation(_csv(CONSISTENT, zero_eps)))

        assert report.skipped_small == 1
        assert report.checked == 1


class TestAColumnThatIsEmptyInOneWholeFile:
    """**2度落ちた形。** fixture が1ファイル分しか無かったので通らなかった。

    2008年のファイルは `EPS` と `PER` が1件も埋まっていない（census で 0%）。
    全部 `None` の列は **object 型**になり、`concat` すると他のファイルの
    float 列まで引きずられる。

    **掛け算も割り算も例外を出さない。** 並べ替えのところで初めて落ちる。
    型を読み口で決めれば、どのファイルから来ても同じになる。
    """

    def test_a_column_with_nothing_in_it_is_still_numeric(self) -> None:
        frame = parse_valuation(_csv("2008-07-08,13010,,,2000,,,,,1.25,1"))

        assert frame["eps"].dtype.kind == "f", frame["eps"].dtype

    def test_every_numeric_column_is_numeric_even_when_all_empty(self) -> None:
        frame = parse_valuation(_csv("2008-07-08,13010,,,,,,,,,"))

        for column in ("eps", "bps", "roe", "per", "pbr", "market_cap"):
            assert frame[column].dtype.kind == "f", (column, frame[column].dtype)

    def test_concatenating_two_files_keeps_the_type(self) -> None:
        import pandas as pd

        empty = parse_valuation(_csv("2008-07-08,13010,,,2000,,,,,1.25,1"))
        full = parse_valuation(_csv(CONSISTENT))

        assert pd.concat([empty, full])["eps"].dtype.kind == "f"


class TestReadingAnArchiveThatMixesEmptyAndFullFiles:
    """**実データそのものの形。** 2008年のファイルと、近年のファイルが並ぶ。

    ここを通していれば、1,588万行で落ちる前に落ちていた。`from_archive` を
    呼ぶテストが `worst` まで届いていなかった。
    """

    def _archive(self, tmp_path: Path, files: dict[str, list[str]]) -> None:
        import datetime as dt

        from stock_ai.data.jquants_archive import archive
        from stock_ai.data.jquants_bulk import BulkFile

        bodies = {key: gzip.compress(_csv(*rows)) for key, rows in files.items()}
        archive(
            [BulkFile(key=key, last_modified="", size=len(body)) for key, body in bodies.items()],
            lambda key: bodies[key],
            tmp_path,
            on=dt.date(2026, 9, 15),
        )

    def _both(self, tmp_path: Path) -> None:
        self._archive(
            tmp_path,
            {
                # 2008: EPS も PER も1件も無い
                "equities/valuation/historical/2008/eq_valuation_200807.csv.gz": [
                    "2008-07-08,13010,,,2000,,,,,1.25,1",
                    "2008-07-09,13020,,,2000,,,,,1.25,1",
                ],
                # 近年: 埋まっていて、しかも食い違いが1件ある
                "equities/valuation/historical/2026/eq_valuation_202608.csv.gz": [
                    CONSISTENT,
                    "2026-08-04,13030,100,120,2000,5.0,6.0,25.0,20.8,2.50,1",
                ],
            },
        )

    def test_the_columns_stay_numeric_across_files(self, tmp_path: Path) -> None:
        self._both(tmp_path)

        assert from_archive(tmp_path)["eps"].dtype.kind == "f"

    def test_the_check_runs_to_the_end(self, tmp_path: Path) -> None:
        """**落ちないこと自体が主張である。** ここが2度落ちた。"""
        self._both(tmp_path)

        report = identity_check(from_archive(tmp_path))

        assert report.checked == 2
        assert report.agreed == 1
        assert [symbol for _, symbol, _, _ in report.worst] == ["1303"]

    def test_the_empty_year_shows_as_empty_rather_than_as_a_disagreement(
        self, tmp_path: Path
    ) -> None:
        self._both(tmp_path)
        found = census(from_archive(tmp_path))

        assert found.share(2008, "eps") == 0.0
        assert found.thin_years("eps") == [2008]


class TestCountingWhatIsEmptyByYear:
    """**公式の注意書きを引き写さない。** 手元のファイルが答える。"""

    def test_the_thin_years_are_named(self) -> None:
        frame = parse_valuation(
            _csv(
                "2008-06-02,13010,,,,,,,,,",
                "2008-06-03,13020,,,,,,,,,",
                "2026-08-03,13010,100,120,2000,5.0,6.0,25.0,20.8,1.25,1",
            )
        )
        found = census(frame)

        assert found.rows_by_year == {2008: 2, 2026: 1}
        assert found.share(2008, "market_cap") == 0.0
        assert found.share(2026, "market_cap") == 1.0
        assert found.thin_years("market_cap") == [2008]

    def test_the_span_and_symbol_count_come_out(self) -> None:
        frame = parse_valuation(
            _csv(
                "2008-06-02,13010,1,1,1,1,1,1,1,1,1",
                "2026-08-03,13020,1,1,1,1,1,1,1,1,1",
            )
        )
        found = census(frame)

        assert (found.first, found.last) == (dt.date(2008, 6, 2), dt.date(2026, 8, 3))
        assert found.symbols == 2

    def test_an_empty_frame_counts_nothing(self) -> None:
        assert census(parse_valuation(_csv())).rows_by_year == {}


class TestReadingTheArchive:
    def _archive(self, tmp_path: Path, keys: dict[str, bytes]) -> None:
        import datetime as dt

        from stock_ai.data.jquants_archive import archive
        from stock_ai.data.jquants_bulk import BulkFile

        archive(
            [BulkFile(key=key, last_modified="", size=len(body)) for key, body in keys.items()],
            lambda key: keys[key],
            tmp_path,
            on=dt.date(2026, 9, 15),
        )

    def test_the_same_day_in_two_files_is_counted_once(self, tmp_path: Path) -> None:
        # 月次の `historical` と日次の `live` は重なる。**残すと同じ銘柄日が
        # 2度数えられる。**
        body = gzip.compress(_csv(CONSISTENT))
        self._archive(
            tmp_path,
            {
                "equities/valuation/historical/2026/eq_valuation_202608.csv.gz": body,
                "equities/valuation/live/eq_valuation_20260803.csv.gz": body,
            },
        )

        assert len(from_archive(tmp_path)) == 1

    def test_nothing_archived_gives_an_empty_frame_with_columns(self, tmp_path: Path) -> None:
        frame = from_archive(tmp_path)
        assert frame.empty
        assert "market_cap" in frame.columns


class TestNotDecidingFromTheAgreementRateAlone:
    """**「96.9% 一致」は「違う」ではない。**

    大半が合っていて少数が外れているとき、丸めなのか意味の違いなのかで、
    次にやることが正反対になる。丸めなら許容幅の問題でデータは使える。
    意味が違うなら、その列を使う説を止める。

    **断定の前に、原因の候補ごとに数える。**
    """

    def _row(
        self, day: str, code: str, true_eps: float, close: float, digits: int, bps: float = 2000.0
    ) -> str:
        """EPS を ``digits`` 桁で丸めて書く。**PER は丸める前の EPS から作る。**

        原本はそういう形である——`PER` は向こうが正しい値から計算し、`EPS` は
        表示の桁で丸めて載せる。**こちらが掛け戻すと、丸めたぶんだけずれる。**
        相対誤差は EPS が小さいほど大きい。
        """
        shown = round(true_eps, digits)
        per = close / true_eps
        pbr = close / bps
        return f"{day},{code},{shown},{shown},{bps},5.0,6.0,{per:.6f},20.8,{pbr:.10f},1"

    def test_rounding_shows_up_as_a_slope_across_eps_sizes(self) -> None:
        rows = []
        for index in range(60):
            # 小さい EPS: 1.449 → 1.4 と書かれる。掛け戻すと 3.4% ずれる。
            rows.append(self._row("2020-06-01", f"{1300 + index}0", 1.449, 2500.0, 1))
        for index in range(60):
            # 大きい EPS: 144.9 → 144.9 のまま。ずれない。
            rows.append(self._row("2020-06-02", f"{1400 + index}0", 144.9, 2500.0, 1))

        profile = explain_gap(parse_valuation(_csv(*rows)))

        assert profile.rate("〜2") == 0.0, profile.by_eps_size
        assert profile.rate("50〜") == 1.0, profile.by_eps_size
        assert profile.eps_size_matters()

    def test_a_flat_profile_does_not_by_itself_mean_anything(self) -> None:
        """**平らであることは、丸めを否定しない。**

        一度これを「丸めではない」と読んで間違えた（2026-09-15）。実データは
        どの区分も 96% 台で平らだったが、効いていたのは `PBR` の丸めで、
        それは `EPS` の大小と関係が無い。

        この旗が立つのは、**EPS 由来の丸めが上乗せで効いているときだけ**で
        ある。立たないことは、何も意味しない。
        """
        rows = []
        for index, eps in enumerate((1.5, 5.0, 20.0, 100.0) * 15):
            code = f"{1300 + index}0"
            # PBR を倍にして、大きさに依らず外す
            per = round(2500.0 / eps, 4)
            rows.append(f"2020-06-01,{code},{eps},{eps},2000,5.0,6.0,{per},20.8,2.5,1")

        profile = explain_gap(parse_valuation(_csv(*rows)))

        assert not profile.eps_size_matters()

    def test_a_forecast_based_per_is_detected(self) -> None:
        """東証の PER は会社予想 EPS で計算する。**そこを取り違えたら言う。**"""
        rows = []
        for index in range(20):
            code = f"{1300 + index}0"
            actual, forecast, close, bps = 100.0, 125.0, 2500.0, 2000.0
            per = close / forecast
            rows.append(f"2020-06-01,{code},{actual},{forecast},{bps},5.0,6.0,{per},20.8,1.25,1")

        profile = explain_gap(parse_valuation(_csv(*rows)))

        assert profile.forward_rescues == 20

    def test_loss_making_rows_are_counted_separately(self) -> None:
        rows = [
            self._row("2020-06-01", "13010", 100.0, 2500.0, 4),
            "2020-06-02,13020,-50.0,-50.0,2000,5.0,6.0,-50.0,20.8,1.25,1",
        ]

        profile = explain_gap(parse_valuation(_csv(*rows)))

        assert profile.negative_eps[0] == 1

    def test_an_empty_frame_gives_zeros_rather_than_raising(self) -> None:
        profile = explain_gap(parse_valuation(_csv()))

        assert profile.negative_eps == (0, 0)
        assert profile.forward_rescues == 0
        assert not profile.eps_size_matters()


class TestCheckingTheMarketCapAgainstSomethingElse:
    """時価総額を確かめる手立ては、**株式数を割り出すことしか無い。**

    株式数は原本に入っていない。`時価総額 ÷ 終値` で出る数が銘柄ごとに安定
    していれば、時価総額は終値と同じ尺度で作られている。桁が飛ぶなら、
    **単位が違うか、分割を跨いで尺度が変わっている。**
    """

    def test_a_consistent_file_gives_a_steady_share_count(self) -> None:
        # 終値 2,500 円、時価総額 2.5e11 → 1億株
        rows = [
            "2026-08-03,13010,100,120,2000,5.0,6.0,25.0,20.8,1.25,250000000000",
            "2026-08-04,13010,100,120,2000,5.0,6.0,25.0,20.8,1.25,250000000000",
        ]
        shares = implied_shares(parse_valuation(_csv(*rows)))

        assert shares.round(-6).nunique() == 1
        assert abs(shares.iloc[0] - 100_000_000) < 1

    def test_rows_without_a_market_cap_are_dropped_not_zeroed(self) -> None:
        rows = [
            "2026-08-03,13010,100,120,2000,5.0,6.0,25.0,20.8,1.25,250000000000",
            "2026-08-04,13020,100,120,2000,5.0,6.0,25.0,20.8,1.25,",
        ]
        assert len(implied_shares(parse_valuation(_csv(*rows)))) == 1

    def test_a_zero_close_does_not_become_an_infinite_share_count(self) -> None:
        rows = ["2026-08-03,13010,100,120,2000,5.0,6.0,25.0,20.8,0,250000000000"]

        assert implied_shares(parse_valuation(_csv(*rows))).empty


class TestTakingTheToleranceFromThePublishedDigits:
    """**許容幅を推測で決めない。** 桁は原本に書いてある。

    1% という決め打ちは、`PBR` の丸めが作る裾をちょうど切っていた。`PBR` は
    1 前後なので、小数2桁なら相対誤差は ±0.5% になる——**`EPS` の大小とは
    関係が無い。** そしてその裾を「列の意味が違う」と読んだ（2026-09-15）。

    桁から幅を出せば、「合わない」は本当に説明の付かないものだけになる。
    """

    def test_the_digits_are_counted_from_the_raw_text(self) -> None:
        payload = _csv("2024-06-03,13010,1.25,1.2,100.5,8.0,6.0,25.25,20.8,1.25,1")
        seen = decimals_seen(payload)

        assert seen["PBR"] == {2: 1}
        assert seen["BPS"] == {1: 1}
        assert seen["MktCap"] == {0: 1}

    def test_blanks_are_not_counted_as_zero_digits(self) -> None:
        # **空欄を0桁として数えると、幅が広がる。** 0桁は ±0.5 である。
        payload = _csv("2024-06-03,13010,,,,,,,,,")
        assert decimals_seen(payload)["PBR"] == {}

    def test_the_most_common_digit_count_wins_not_the_coarsest(self) -> None:
        """**末尾の 0 は落ちる。** `25.10` は `25.1` と書かれる。

        いちばん粗い桁を採ると、そういう行が1件あるだけで列全体の幅が10倍に
        なる。実データでは全9列が `±0.05` になり、`PBR` の相対幅が 5% に
        なった——**何をしても収まる幅**である（2026-09-15）。
        """
        payload = _csv(
            "2024-06-03,13010,1.25,1.2,100.5,8.0,6.0,25.25,20.8,1.25,1",
            "2024-06-04,13020,1.25,1.2,100.5,8.0,6.0,25.25,20.8,1.26,1",
            "2024-06-05,13030,1.25,1.2,100.5,8.0,6.0,25.25,20.8,1.2,1",
        )
        assert half_widths(decimals_seen(payload))["pbr"] == 0.005

    def test_the_spread_shows_whether_the_format_is_settled(self) -> None:
        """**採った桁が代表かどうかを見せる。** 割れていれば幅を信じない。"""
        payload = _csv(
            "2024-06-03,13010,1.25,1.2,100.5,8.0,6.0,25.25,20.8,1.25,1",
            "2024-06-04,13020,1.25,1.2,100.5,8.0,6.0,25.25,20.8,1.26,1",
            "2024-06-05,13030,1.25,1.2,100.5,8.0,6.0,25.25,20.8,1.2,1",
        )
        assert digit_spread(decimals_seen(payload), "PBR") == "2桁 67%、1桁 33%"

    def test_a_column_never_seen_says_so(self) -> None:
        assert digit_spread(decimals_seen(_csv("2024-06-03,13010,,,,,,,,,")), "PBR") == "無し"

    def test_two_decimals_give_half_a_hundredth(self) -> None:
        payload = _csv(CONSISTENT)
        assert half_widths(decimals_seen(payload))["bps"] == 0.5

    def test_no_digits_measured_means_no_width_rather_than_zero(self) -> None:
        """**分からないことを、分かったことにしない。** 幅0は「ぴったり合え」である。"""
        assert "pbr" not in half_widths(decimals_seen(_csv("2024-06-03,13010,,,,,,,,,")))


class TestWhatTheRoundingBoundAllows:
    def _frame(self, close: float, eps: float, bps: float):
        row = ",".join(
            [
                "2024-06-03",
                "13010",
                f"{eps:.2f}",
                "",
                f"{bps:.2f}",
                "8.00",
                "",
                f"{close / eps:.2f}",
                "",
                f"{close / bps:.2f}",
                "1",
            ]
        )
        return parse_valuation(_csv(row))

    def test_a_row_rounded_to_two_places_fits_inside_the_bound(self) -> None:
        frame = self._frame(2500.0, 137.0, 2000.0)
        widths = {"per": 0.005, "eps": 0.005, "pbr": 0.005, "bps": 0.005}

        gap = ((frame["per"] * frame["eps"]) - (frame["pbr"] * frame["bps"])).abs() / (
            frame["pbr"] * frame["bps"]
        ).abs()

        assert (gap <= rounding_bound(frame, widths)).all()

    def test_a_genuinely_wrong_row_does_not_fit(self) -> None:
        # PBR を倍にする。丸めでは届かない差である。
        broken = parse_valuation(_csv("2024-06-03,13010,137.00,,2000.00,8.00,,18.25,,2.50,1"))
        widths = {"per": 0.005, "eps": 0.005, "pbr": 0.005, "bps": 0.005}

        gap = ((broken["per"] * broken["eps"]) - (broken["pbr"] * broken["bps"])).abs() / (
            broken["pbr"] * broken["bps"]
        ).abs()

        assert not (gap <= rounding_bound(broken, widths)).all()

    def test_a_coarser_pbr_widens_the_bound(self) -> None:
        """**PBR の桁がいちばん効く。** 1 前後の値だからである。"""
        frame = self._frame(2500.0, 137.0, 2000.0)
        fine = rounding_bound(frame, {"pbr": 0.005, "bps": 0.005}).iloc[0]
        coarse = rounding_bound(frame, {"pbr": 0.05, "bps": 0.005}).iloc[0]

        assert coarse > fine * 5

    def test_missing_widths_give_a_bound_of_zero_not_an_error(self) -> None:
        frame = self._frame(2500.0, 137.0, 2000.0)

        assert float(rounding_bound(frame, {}).iloc[0]) == 0.0


class TestNotCallingItAMixUpFromAHandfulOfRows:
    """**「ゼロでない」を根拠にしない。**

    合わない行の 2.5%（判定した行の 0.08%）で「列を取り違えている」と赤字を
    出していた。取り違えなら**ほとんどが**救われるはずである。
    """

    def test_a_few_rescues_do_not_make_a_majority(self) -> None:
        rows = []
        for index in range(40):
            code = f"{1300 + index}0"
            if index == 0:
                # 会社予想でなら合う1行
                rows.append(f"2020-06-01,{code},100,125,2000,5.0,6.0,20.0,20.8,1.25,1")
            else:
                # 丸めでは届かない外れ方
                rows.append(f"2020-06-01,{code},100,100,2000,5.0,6.0,10.0,20.8,1.25,1")

        profile = explain_gap(parse_valuation(_csv(*rows)))

        assert profile.forward_rescues == 1
        assert profile.forward_share < 0.5

    def test_a_real_mix_up_rescues_nearly_everything(self) -> None:
        rows = [
            f"2020-06-01,{1300 + index}0,100,125,2000,5.0,6.0,20.0,20.8,1.25,1"
            for index in range(20)
        ]
        profile = explain_gap(parse_valuation(_csv(*rows)))

        assert profile.forward_share == 1.0

    def test_no_disagreements_means_no_share_rather_than_a_crash(self) -> None:
        profile = explain_gap(parse_valuation(_csv(CONSISTENT)))

        assert profile.forward_share == 0.0


class TestTellingApartCannotSayFromAgrees:
    """**全体の中央値では、行ごとの無意味さを守れない。**

    幅の中央値が 0.55% でも、`EPS` が 0.01 の行は `PER` が 37,230 になり、
    `EPS` の ±0.005 が終値の **±36%** に化ける。その行は 31% ずれていても
    「収まった」に数えられていた（2026-09-15、6784）。

    **同じ形を3度踏んだ**——全体の数字が、個別の無意味さを隠す。
    """

    def _rows(self) -> list[str]:
        rows = []
        for index in range(200):
            close, bps, eps = 2500.0 + index, 2000.0 + index, 100.0 + index
            rows.append(
                f"2024-06-03,{1300 + index}0,{eps:.2f},,{bps:.2f},8.0000,,"
                f"{close / eps:.2f},,{close / bps:.2f},1.0"
            )
        return rows

    def _widths(self, payload: bytes) -> dict:
        return half_widths(decimals_seen(payload))

    def test_a_tiny_eps_row_is_unresolvable_not_agreeing(self) -> None:
        # 6784 の形。31% ずれているが、その行の幅は 36% ある。
        rows = [*self._rows(), "2013-07-16,67840,0.01,,2010.00,8.0000,,37230.00,,0.27,1.0"]
        payload = _csv(*rows)

        found = resolve(parse_valuation(payload), self._widths(payload))

        assert found.unresolvable == 1
        assert found.outside == 0

    def test_a_real_mismatch_is_counted_as_outside(self) -> None:
        # 丸めでは届かない外れ方。**ここだけが説明の付かない食い違いである。**
        rows = [*self._rows(), "2024-06-03,99990,100.00,,2000.00,8.0000,,25.00,,2.50,1.0"]
        payload = _csv(*rows)

        found = resolve(parse_valuation(payload), self._widths(payload))

        assert found.outside == 1
        assert found.unresolvable == 0

    def test_the_rate_is_taken_over_what_could_be_judged(self) -> None:
        """**判定できなかった行を分母に入れない。** 入れると割合が甘くなる。"""
        rows = [
            *self._rows(),
            "2013-07-16,67840,0.01,,2010.00,8.0000,,37230.00,,0.27,1.0",
            "2024-06-03,99990,100.00,,2000.00,8.0000,,25.00,,2.50,1.0",
        ]
        payload = _csv(*rows)

        found = resolve(parse_valuation(payload), self._widths(payload))

        assert found.judged == found.within + found.outside
        assert found.judged == 201
        assert found.rate == 200 / 201

    def test_the_summary_always_names_what_could_not_be_judged(self) -> None:
        rows = [*self._rows(), "2013-07-16,67840,0.01,,2010.00,8.0000,,37230.00,,0.27,1.0"]
        payload = _csv(*rows)

        assert "判定できない" in resolve(parse_valuation(payload), self._widths(payload)).summary()

    def test_no_widths_means_nothing_judged_rather_than_everything_agreeing(self) -> None:
        """**幅が分からないことを「全部合っている」にしない。**"""
        found = resolve(parse_valuation(_csv(CONSISTENT)), {})

        assert found.judged == 0
        assert found.rate == 0.0

    def test_an_empty_frame_does_not_raise(self) -> None:
        assert resolve(parse_valuation(_csv()), {"pbr": 0.005}).judged == 0
