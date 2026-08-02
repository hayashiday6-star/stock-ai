"""Tests for screening report building, export, and the ``screen`` CLI."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from stock_ai import cli
from stock_ai.core.exceptions import DataError
from stock_ai.data.types import Fundamentals
from stock_ai.database.engine import Database
from stock_ai.database.repository import FundamentalsRepository
from stock_ai.screening.report import build_report, write_report

runner = CliRunner()
_TODAY = dt.date(2024, 6, 30)


def _items() -> list[Fundamentals]:
    return [
        Fundamentals(symbol="AAPL", as_of=_TODAY, roe=1.4, per=35.0, pbr=42.0),
        Fundamentals(symbol="VALUE", as_of=_TODAY, roe=0.2, per=10.0, pbr=1.2),
    ]


def test_build_report_shape() -> None:
    report = build_report(_items())
    assert list(report["symbol"]) == ["AAPL", "VALUE"]
    assert "roe" in report.columns
    assert len(report) == 2


def test_write_csv_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "out.csv"
    write_report(build_report(_items()), path, "csv")
    back = pd.read_csv(path)
    assert list(back["symbol"]) == ["AAPL", "VALUE"]


def test_write_json_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "out.json"
    write_report(build_report(_items()), path, "json")
    back = pd.read_json(path)
    assert set(back["symbol"]) == {"AAPL", "VALUE"}


def test_write_xlsx_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "out.xlsx"
    write_report(build_report(_items()), path, "xlsx")
    back = pd.read_excel(path)
    assert list(back["symbol"]) == ["AAPL", "VALUE"]


def test_write_unsupported_format_raises(tmp_path: Path) -> None:
    with pytest.raises(DataError):
        write_report(build_report(_items()), tmp_path / "x.txt", "txt")


@pytest.fixture
def db() -> Iterator[Database]:
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as s:
        repo = FundamentalsRepository(s)
        repo.upsert_fundamentals(Fundamentals(symbol="AAPL", as_of=_TODAY, roe=1.4, pbr=42.0))
        repo.upsert_fundamentals(Fundamentals(symbol="MID", as_of=_TODAY, roe=0.6, pbr=1.8))
    yield database
    database.dispose()


def test_screen_cli_writes_file(
    db: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "Database", lambda: db)
    out = tmp_path / "screened.json"

    result = runner.invoke(
        cli.app,
        ["screen", "--min-roe", "0.5", "--max-pbr", "2.0", "--out", str(out), "--format", "json"],
    )
    assert result.exit_code == 0
    back = pd.read_json(out)
    assert list(back["symbol"]) == ["MID"]  # AAPL excluded by PBR


def test_screen_cli_requires_a_criterion(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "Database", lambda: db)
    result = runner.invoke(cli.app, ["screen"])
    assert result.exit_code != 0
