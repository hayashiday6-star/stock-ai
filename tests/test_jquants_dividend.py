"""配当金情報の読み取り。

固定データは配布サンプル `sample_data_v2` の `Cash Dividend Data.csv`
**そのまま**である。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path

import pytest

from stock_ai.data.jquants_dividend import (
    Dividend,
    latest_by_term,
    parse_dividends,
    straddles_a_split,
)

SAMPLE = Path(__file__).parent / "fixtures" / "jquants_dividend_sample.csv"


def dataclasses_replace(item: Dividend, **changes: object) -> Dividend:
    return dataclasses.replace(item, **changes)  # type: ignore[arg-type]


def _sample() -> list[Dividend]:
    return parse_dividends(SAMPLE.read_bytes())


def test_the_distributed_sample_parses() -> None:
    items = _sample()

    assert len(items) == 6
    assert {item.symbol for item in items} == {"8697"}
    assert items[0].published_on == dt.date(2022, 3, 22)
    assert items[0].published_at == dt.time(12, 37)


def test_one_publication_day_carries_several_terms() -> None:
    """**足すと年間配当が3倍になる。**

    2022-04-26 の3行は `2022-09` / `2023-03` / `2022-03` で、別の配当である。
    桁も変わらないので、利回りの高い会社として並ぶだけになる。
    """
    same_day = [item for item in _sample() if item.published_on == dt.date(2022, 4, 26)]

    assert len(same_day) == 3
    assert len({item.term for item in same_day}) == 3


def test_the_reference_number_is_what_separates_rows_on_one_day() -> None:
    """同じ公表日・同じ時刻の行を区別できる唯一の列である。"""
    same_day = [item for item in _sample() if item.published_on == dt.date(2022, 4, 26)]

    assert len({item.reference for item in same_day}) == 3


def test_keeping_one_row_per_term_collapses_the_double_count() -> None:
    by_term = latest_by_term(_sample())

    assert len(by_term) == len({item.term for item in _sample()})


def test_a_correction_replaces_the_earlier_row_for_the_same_term() -> None:
    """**新しいほうだけを使う。** 両方使うと同じ期を2回数える。"""
    early = Dividend(
        symbol="8697",
        published_on=dt.date(2022, 4, 26),
        published_at=dt.time(12, 38),
        reference="A",
        term="2023-03",
        rate=46.0,
        ordinary_rate=None,
        special_rate=None,
        ex_date=None,
        record_date=None,
        pay_date=None,
        status_code="1",
    )
    late = dataclasses_replace(early, published_on=dt.date(2022, 5, 10), reference="B", rate=50.0)

    assert latest_by_term([early, late])[("8697", "2023-03")].rate == 50.0
    assert latest_by_term([late, early])[("8697", "2023-03")].rate == 50.0


def test_a_same_day_correction_falls_back_to_the_reference_number() -> None:
    """同じ日の中の順序を決める材料が他に無い。**推測であることを試験に残す。**"""
    first = Dividend(
        symbol="8697",
        published_on=dt.date(2022, 4, 26),
        published_at=dt.time(12, 38),
        reference="202204261B00019",
        term="2022-03",
        rate=46.0,
        ordinary_rate=None,
        special_rate=None,
        ex_date=None,
        record_date=None,
        pay_date=None,
        status_code="1",
    )
    second = dataclasses_replace(first, reference="202204261B00021", rate=47.0)

    assert latest_by_term([first, second])[("8697", "2022-03")].rate == 47.0


def test_the_status_codes_stay_codes() -> None:
    """`StatCode` の意味はサンプルからは分からない。**名前を付けない。**"""
    assert {item.status_code for item in _sample()} == {"1", "2"}


def test_a_dividend_that_straddles_a_split_is_named_not_fixed() -> None:
    """**倍率で直せる種類の間違いではない。**

    どちらの尺度の値なのかが決まらない。黙って使うと、分割した会社の配当が
    半分または倍で並ぶ。
    """
    item = Dividend(
        symbol="8697",
        published_on=dt.date(2022, 3, 22),
        published_at=None,
        reference="A",
        term="2022-03",
        rate=46.0,
        ordinary_rate=None,
        special_rate=None,
        ex_date=dt.date(2022, 3, 30),
        record_date=dt.date(2022, 3, 31),
        pay_date=None,
        status_code="2",
    )

    assert straddles_a_split(item, [dt.date(2022, 3, 31)])
    assert not straddles_a_split(item, [dt.date(2022, 4, 15)])


def test_a_dividend_without_dates_is_not_reported_as_straddling() -> None:
    """**分からないことを「問題なし」にしない**——ここでは日付が無いだけで、
    分割をまたいでいないと言い切ってはいない。呼ぶ側が日付の有無を見る。
    """
    item = Dividend(
        symbol="8697",
        published_on=dt.date(2022, 3, 22),
        published_at=None,
        reference="A",
        term="2022-03",
        rate=46.0,
        ordinary_rate=None,
        special_rate=None,
        ex_date=None,
        record_date=None,
        pay_date=None,
        status_code="2",
    )

    assert not straddles_a_split(item, [dt.date(2022, 3, 31)])
    assert item.ex_date is None  # 呼ぶ側はこれを見る


class TestHowManyExDatesTheArchiveCanSupply:
    """#9（窓は埋まる）の設計が、これに掛かっている。

    **3% の下窓は、権利落ちがまさにそう見える。** 外せなければ、事象の定義が
    配当を拾う。

    ここで押さえるのは2つ。

    1. **列ごとに独立に数える。** 行が読めたことと、`ExDate` が埋まっている
       ことは別である
    2. **外す対象は（銘柄, 日）**であって、行ではない。同じ日に複数行ある
    """

    @staticmethod
    def _rows(*changes: dict[str, str]) -> str:
        """**実物から作る。** 列名を発明すると、読み口に届かない。

        配布サンプルの1行目を写し、変えたい列だけ差し替える。
        """
        import csv
        import io

        text = SAMPLE.read_text(encoding="utf-8-sig").splitlines()
        reader = csv.DictReader(text)
        template = next(iter(reader))
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=list(template))
        writer.writeheader()
        for change in changes:
            writer.writerow({**template, **change})
        return out.getvalue()

    @classmethod
    def _archive(cls, tmp_path, rows: str):
        import gzip

        from stock_ai.data.jquants_archive import MANIFEST, MANIFEST_COLUMNS

        key = "fins/dividend/dividend_2015.csv.gz"
        target = tmp_path / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(rows.encode("utf-8")))
        (tmp_path / MANIFEST).write_text(
            ",".join(MANIFEST_COLUMNS) + "\n" + f"/{key},1,1,x,,2026-09-19\n",
            encoding="utf-8",
        )
        return tmp_path

    def test_it_counts_rows_and_dates_separately(self, tmp_path) -> None:
        from stock_ai.data.jquants_dividend import ex_date_coverage

        body = self._rows(
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "1"},
            {"Code": "13020", "ExDate": "", "RefNo": "2"},
        )
        found = ex_date_coverage(self._archive(tmp_path, body))

        assert found.rows == 2
        assert found.with_ex_date == 1

    def test_the_missing_column_is_warned_about(self, tmp_path) -> None:
        """**0 を「読めた」と読まない。** 表の1行では気付かない。"""
        from stock_ai.data.jquants_dividend import ex_date_coverage

        body = self._rows(
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "1"},
            {"Code": "13020", "ExDate": "", "RefNo": "2"},
        )
        found = ex_date_coverage(self._archive(tmp_path, body))

        assert any("`ExDate` が空" in line for line in found.warnings())

    def test_the_same_symbol_and_day_counts_once(self, tmp_path) -> None:
        """**外す対象は（銘柄, 日）である。** 行で数えると多く見える。"""
        from stock_ai.data.jquants_dividend import ex_date_coverage

        body = self._rows(
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "1"},
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "2"},
        )
        found = ex_date_coverage(self._archive(tmp_path, body))

        assert found.rows == 2
        assert found.days == 1

    def test_it_splits_the_two_halves(self, tmp_path) -> None:
        from stock_ai.data.jquants_dividend import ex_date_coverage

        body = self._rows(
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "1"},
            {"Code": "13020", "ExDate": "2020-03-30", "RefNo": "2"},
        )
        found = ex_date_coverage(self._archive(tmp_path, body))

        assert found.in_is == 1
        assert found.in_oos == 1

    def test_an_empty_archive_says_so_rather_than_returning_zero_quietly(self, tmp_path) -> None:
        from stock_ai.data.jquants_dividend import ex_date_coverage

        found = ex_date_coverage(tmp_path)

        assert found.rows == 0
        assert "1行も読めなかった" in found.summary()
        assert found.warnings()

    def test_one_sided_coverage_is_warned_about(self, tmp_path) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        from stock_ai.data.jquants_dividend import ex_date_coverage

        body = self._rows({"Code": "13010", "ExDate": "2015-03-30", "RefNo": "1"})
        found = ex_date_coverage(self._archive(tmp_path, body))

        assert any("OOS" in line for line in found.warnings())
        assert not any("IS（〜2017-12）に権利落ちが1件も無い" in line for line in found.warnings())

    def test_the_command_refuses_when_there_is_nothing_to_exclude(self, tmp_path) -> None:
        """**手前で止まる側も通す。**"""
        import typer

        from stock_ai import cli

        with pytest.raises(typer.Exit):
            cli.ex_date_coverage_command(directory=str(tmp_path))

    def test_the_command_runs_on_a_real_archive(self, tmp_path) -> None:
        """**本物のコマンドを、中身の入った原本で1本通す。**"""
        from stock_ai import cli

        body = self._rows(
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "1"},
            {"Code": "13020", "ExDate": "2020-03-30", "RefNo": "2"},
        )
        cli.ex_date_coverage_command(directory=str(self._archive(tmp_path, body)))


class TestTheHalvesAreCountedInTheSameUnitAsTheRowAbove:
    """**同じ列に、行と（銘柄 × 日）を混ぜていた**（2026-09-19、ユーザーが発見）。

    `IS 92,831 + OOS 206,515 = 299,346` は**行**の数で、すぐ上に出ている
    「別々の権利落ち 114,942」とは **2.6倍**違っていた。**同じ列に2つの単位が
    並んでいた。**

    `CLAUDE.md`「独立な観測を、件数で数えない」「系列を作るときの単位と、
    検出力を計算するときの単位を揃える」に当たる形である。**例外は出ない。**
    """

    _MAKE = TestHowManyExDatesTheArchiveCanSupply

    def test_two_rows_for_the_same_day_count_once_in_the_half(self, tmp_path) -> None:
        """**直す前のコードなら 2 になる。** 行で数えていたので。"""
        from stock_ai.data.jquants_dividend import ex_date_coverage

        body = self._MAKE._rows(
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "1"},
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "2"},
        )
        found = ex_date_coverage(self._MAKE._archive(tmp_path, body))

        assert found.rows == 2
        assert found.days == 1
        assert found.in_is == 1

    def test_the_parts_add_up(self, tmp_path) -> None:
        """**足して合わなければ生成時に落ちる。** それが、この形の見張りである。"""
        from stock_ai.data.jquants_dividend import ex_date_coverage

        body = self._MAKE._rows(
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "1"},
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "2"},
            {"Code": "13020", "ExDate": "2020-03-30", "RefNo": "3"},
            {"Code": "13030", "ExDate": "2027-03-30", "RefNo": "4"},
        )
        found = ex_date_coverage(self._MAKE._archive(tmp_path, body))

        assert found.in_is + found.in_oos + found.after_oos == found.days

    def test_a_date_past_the_judgement_window_has_its_own_bucket(self, tmp_path) -> None:
        """**在る。** 配当は前もって公表されるので、2027年の権利落ちが原本に入る。"""
        from stock_ai.data.jquants_dividend import ex_date_coverage

        body = self._MAKE._rows({"Code": "13030", "ExDate": "2027-08-30", "RefNo": "1"})
        found = ex_date_coverage(self._MAKE._archive(tmp_path, body))

        assert found.after_oos == 1
        assert found.in_oos == 0
        assert any("OOS より後" in line for line in found.warnings())

    def test_parts_that_do_not_add_up_are_refused(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        from stock_ai.data.jquants_dividend import ExDateCoverage

        with pytest.raises(ValueError, match="単位が混ざっている"):
            ExDateCoverage(
                files=1,
                rows=2,
                with_ex_date=2,
                symbols=1,
                days=1,
                first=dt.date(2015, 3, 30),
                last=dt.date(2015, 3, 30),
                in_is=2,
                in_oos=0,
                after_oos=0,
            )

    def test_the_table_says_which_rows_are_which_unit(self) -> None:
        """**読む側が単位を取り違えない形にする。**"""
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.ex_date_coverage_command)

        assert "外す対象の単位ではない" in body
        assert "銘柄 × 日。**行ではない**" in body


class TestReadingTheAmountDoesNotAddRowsUp:
    """**足すと年間配当が3倍になる。**

    その注意書きは `latest_by_term` の説明に既に書いてあり、それを読まずに
    2つ目を書いて踏んだ（2026-09-20）。実データで**下げ −1.27% に対し利回り
    3.09%** と出て、2.4倍合わなかった。
    """

    _rows = staticmethod(TestHowManyExDatesTheArchiveCanSupply._rows)
    _archive = classmethod(TestHowManyExDatesTheArchiveCanSupply._archive.__func__)

    def test_two_rows_on_one_ex_date_are_not_added(self, tmp_path) -> None:
        """同じ権利落ち日の2行は、**最後の公表を1行だけ**採る。"""
        from stock_ai.data.jquants_dividend import ex_dividend_rates

        body = self._rows(
            {
                "Code": "13010",
                "ExDate": "2015-03-30",
                "RefNo": "1",
                "PubDate": "2015-02-01",
                "DivRate": "10",
                "IFTerm": "2015-03",
            },
            {
                "Code": "13010",
                "ExDate": "2015-03-30",
                "RefNo": "2",
                "PubDate": "2015-03-01",
                "DivRate": "12",
                "IFTerm": "2015-03",
            },
        )

        found = ex_dividend_rates(self._archive(tmp_path, body))

        assert found.rates["1301"][dt.date(2015, 3, 30)].rate == 12.0, "**足している。**"
        assert found.multi_row == 1
        assert found.max_rows == 2
        assert any("2行以上" in line for line in found.warnings())

    def test_one_row_per_date_says_nothing(self, tmp_path) -> None:
        """**両向きに置く。** 常に鳴る旗は何も区別しない。"""
        from stock_ai.data.jquants_dividend import ex_dividend_rates

        body = self._rows(
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "1", "DivRate": "10"},
            {"Code": "13020", "ExDate": "2015-03-30", "RefNo": "2", "DivRate": "20"},
        )

        found = ex_dividend_rates(self._archive(tmp_path, body))

        assert found.multi_row == 0
        assert found.ex_dates == 2
        assert not any("2行以上" in line for line in found.warnings())

    def test_a_zero_amount_is_kept_and_counted(self, tmp_path) -> None:
        """**無配の公表にも `ExDate` は入る。** 落とさずに数える。"""
        from stock_ai.data.jquants_dividend import ex_dividend_rates

        body = self._rows(
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "1", "DivRate": "0"},
        )

        found = ex_dividend_rates(self._archive(tmp_path, body))

        assert found.rates["1301"][dt.date(2015, 3, 30)].is_zero
        assert found.zero_rate == 1
        assert any("額が 0" in line for line in found.warnings())


class TestAZeroDividendIsNotAnExDate:
    """**無配の公表にも `ExDate` は入る。**

    額を見ずに外すと、**落ちるものが無い日で事象を外す。** `#16` が急落
    371 件（判定できた分の 56.1%）をそれで外していた（2026-09-20、ユーザーが
    指摘）。**`#15` も同じ口を使っている。**
    """

    _rows = staticmethod(TestHowManyExDatesTheArchiveCanSupply._rows)
    _archive = classmethod(TestHowManyExDatesTheArchiveCanSupply._archive.__func__)

    def test_a_zero_amount_date_is_dropped(self, tmp_path) -> None:
        from stock_ai.data.jquants_dividend import ex_dates_known_by

        body = self._rows(
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "1", "DivRate": "0"},
        )

        found = ex_dates_known_by(self._archive(tmp_path, body))

        assert found.by_symbol == {}
        assert found.dropped_zero == 1
        assert found.kept == 0

    def test_a_positive_amount_date_is_kept(self, tmp_path) -> None:
        """**両向きに置く。** 全部落とす形に倒しても緑にならないように。"""
        from stock_ai.data.jquants_dividend import ex_dates_known_by

        body = self._rows(
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "1", "DivRate": "10"},
        )

        found = ex_dates_known_by(self._archive(tmp_path, body))

        assert found.by_symbol["1301"] == [(dt.date(2022, 3, 22), dt.date(2015, 3, 30))]
        assert found.dropped_zero == 0
        assert found.kept == 1

    def test_an_unknown_amount_is_kept_and_named(self, tmp_path) -> None:
        """**額が未公表なら外す側に倒す。** ただし黙って混ぜない。"""
        from stock_ai.data.jquants_dividend import ex_dates_known_by

        body = self._rows(
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "1", "DivRate": ""},
        )

        found = ex_dates_known_by(self._archive(tmp_path, body))

        assert found.kept == 1
        assert found.unknown_amount == 1
        assert any("未公表" in line for line in found.warnings())

    def test_a_later_zero_does_not_undo_an_earlier_positive(self, tmp_path) -> None:
        """**その時点で正の額が公表されていれば、権利落ちとして扱う。**

        後から 0 に訂正されたことを使えば**先読み**になる。
        """
        from stock_ai.data.jquants_dividend import ex_dates_known_by

        body = self._rows(
            {
                "Code": "13010",
                "ExDate": "2015-03-30",
                "RefNo": "1",
                "PubDate": "2015-02-01",
                "DivRate": "10",
            },
            {
                "Code": "13010",
                "ExDate": "2015-03-30",
                "RefNo": "2",
                "PubDate": "2015-03-01",
                "DivRate": "0",
            },
        )

        found = ex_dates_known_by(self._archive(tmp_path, body))

        assert found.by_symbol["1301"] == [(dt.date(2015, 2, 1), dt.date(2015, 3, 30))]
        assert found.dropped_zero == 0

    def test_each_date_appears_once(self, tmp_path) -> None:
        """同じ権利落ち日を2度入れない。**件数が二重になる。**"""
        from stock_ai.data.jquants_dividend import ex_dates_known_by

        body = self._rows(
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "1", "DivRate": "10"},
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "2", "DivRate": ""},
        )

        found = ex_dates_known_by(self._archive(tmp_path, body))

        assert len(found.by_symbol["1301"]) == 1
        assert found.kept == 1
        assert found.unknown_amount == 0

    def test_a_blank_row_does_not_rescue_a_zero(self, tmp_path) -> None:
        """**「分からない」と「0 と書いてある」を、行単位で混ぜない。**

        同じ権利落ち日に平均2.4行ある。最初の版は**額が空の行が1つでも
        あれば未公表扱い**にしていて、**落とすのは全部の行が 0 のときだけ**
        になっていた。実データで 81 件がそこから漏れた（2026-09-20、
        ユーザーが発見）。

        **別の行が 0 と言っているなら、分かっている。**
        """
        from stock_ai.data.jquants_dividend import ex_dates_known_by

        body = self._rows(
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "1", "DivRate": "0"},
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "2", "DivRate": ""},
        )

        found = ex_dates_known_by(self._archive(tmp_path, body))

        assert found.by_symbol == {}, "**空の行が 0 を打ち消している。**"
        assert found.dropped_zero == 1
        assert found.unknown_amount == 0

    def test_a_date_with_no_amount_at_all_is_still_kept(self, tmp_path) -> None:
        """**両向きに置く。** 額が1行も無ければ、やはり外す側に倒す。"""
        from stock_ai.data.jquants_dividend import ex_dates_known_by

        body = self._rows(
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "1", "DivRate": ""},
            {"Code": "13010", "ExDate": "2015-03-30", "RefNo": "2", "DivRate": ""},
        )

        found = ex_dates_known_by(self._archive(tmp_path, body))

        assert found.kept == 1
        assert found.unknown_amount == 1
        assert found.dropped_zero == 0


class TestTheRawRowsComeBackWhole:
    """**読み口が捨てている列は、読み口からは見えない。**

    `parse_dividends` は 23 列のうち 7 列しか採らず、``DeemDiv``（みなし配当）
    ・``DeemCapGains``・``NetAssetDecRatio``・``DistAmt``・``RetEarn`` を
    捨てている。**「額が前日終値以上」の 53 件を追うには、そこが要る**
    （2026-09-20。実際の段差の 24〜85倍が記録されていて、**倍率が銘柄ごとに
    ばらばら**なので単一の係数では説明が付かない）。
    """

    @staticmethod
    def _archive(tmp_path, body: str):
        import gzip

        from stock_ai.data.jquants_archive import MANIFEST, MANIFEST_COLUMNS

        key = "fins/dividend/dividend_2014.csv.gz"
        target = tmp_path / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(body.encode("utf-8")))
        (tmp_path / MANIFEST).write_text(
            ",".join(MANIFEST_COLUMNS) + "\n" + f"/{key},1,1,x,,2026-09-20\n",
            encoding="utf-8",
        )
        return tmp_path

    @staticmethod
    def _body() -> str:
        """**実物の配布サンプルの列**を使う。列名を手で書き写さない。"""
        header = SAMPLE.read_text(encoding="utf-8-sig").splitlines()[0]
        names = header.split(",")
        wanted = {
            "Code": "21310",
            "PubDate": "2014-02-14",
            "ExDate": "2014-03-27",
            "DivRate": "5600.0",
            "DeemDiv": "12.34",
            "NetAssetDecRatio": "0.5",
            "StatCode": "1",
        }
        row = ",".join(wanted.get(name, "") for name in names)
        other = ",".join(
            {"Code": "99990", "PubDate": "2014-02-14", "ExDate": "2014-03-27"}.get(name, "")
            for name in names
        )
        return "\n".join([header, row, other]) + "\n"

    def test_it_returns_every_column_including_the_discarded_ones(self, tmp_path) -> None:
        import datetime as dt

        from stock_ai.data.jquants_dividend import raw_rows

        found = raw_rows(self._archive(tmp_path, self._body()), [("2131", dt.date(2014, 3, 27))])

        assert len(found) == 1
        symbol, ex_date, key, row = found[0]
        assert symbol == "2131"
        assert ex_date == dt.date(2014, 3, 27)
        assert key.endswith("dividend_2014.csv.gz")
        # **読み口が採っている列。**
        assert row["DivRate"] == "5600.0"
        # **読み口が捨てている列。ここが出ないなら、この道具は要らない。**
        assert row["DeemDiv"] == "12.34"
        assert row["NetAssetDecRatio"] == "0.5"
        assert "DistAmt" in row
        assert "RetEarn" in row
        assert "DeemCapGains" in row
        assert "IFCode" in row

    def test_it_leaves_out_what_was_not_asked_for(self, tmp_path) -> None:
        """**両向きに置く。** 全部返すなら、絞れていない。"""
        import datetime as dt

        from stock_ai.data.jquants_dividend import raw_rows

        found = raw_rows(self._archive(tmp_path, self._body()), [("2131", dt.date(2014, 3, 27))])

        assert [symbol for symbol, *_rest in found] == ["2131"]

    def test_asking_for_nothing_reads_nothing(self, tmp_path) -> None:
        from stock_ai.data.jquants_dividend import raw_rows

        assert raw_rows(self._archive(tmp_path, self._body()), []) == []

    def test_every_matching_row_comes_back(self, tmp_path) -> None:
        """**1行に畳まない。** どれを採ったかが見えなくなる。"""
        import datetime as dt

        from stock_ai.data.jquants_dividend import raw_rows

        header = SAMPLE.read_text(encoding="utf-8-sig").splitlines()[0]
        names = header.split(",")

        def _row(ref: str, rate: str) -> str:
            values = {
                "Code": "21310",
                "PubDate": "2014-02-14",
                "ExDate": "2014-03-27",
                "RefNo": ref,
                "DivRate": rate,
            }
            return ",".join(values.get(name, "") for name in names)

        body = "\n".join([header, _row("1", "5600.0"), _row("2", "56.0")]) + "\n"

        found = raw_rows(self._archive(tmp_path, body), [("2131", dt.date(2014, 3, 27))])

        assert len(found) == 2
        assert {row["DivRate"] for *_rest, row in found} == {"5600.0", "56.0"}
