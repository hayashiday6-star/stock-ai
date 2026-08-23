"""EDINET 書類本体プローブのコマンドライン。

``main()`` を通す。前に同じ道具で、関数だけ直してコマンドライン側の受け渡しが
落ちたまま利用者の初回実行が TypeError で止まったことがある。実際に叩かれる
経路で確かめる。
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_PROBE = pathlib.Path(__file__).resolve().parent.parent / "tools" / "edinet_xbrl_probe.py"


@pytest.fixture
def probe_module():
    """パス指定で読み込む。``tools/`` はパッケージではない。"""
    spec = importlib.util.spec_from_file_location("edinet_xbrl_probe", _PROBE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_passes_every_argument_probe_requires(probe_module, monkeypatch) -> None:
    """``main()`` は ``probe()`` のシグネチャを満たさなければならない。"""
    import inspect

    captured: dict = {}
    monkeypatch.setattr(
        probe_module, "probe", lambda *a, **k: captured.update(args=a, kwargs=k) or 0
    )
    monkeypatch.setenv("EDINET_API_KEY", "key")
    monkeypatch.setattr(sys, "argv", ["edinet_xbrl_probe.py"])

    assert probe_module.main() == 0
    inspect.signature(probe_module.probe).bind(*captured["args"], **captured["kwargs"])


def test_the_flags_reach_the_call(probe_module, monkeypatch, tmp_path) -> None:
    """パーサが受け付けるだけでなく、呼び出しに届くこと。"""
    captured: dict = {}
    monkeypatch.setattr(probe_module, "probe", lambda *a: captured.update(args=a) or 0)
    monkeypatch.setattr(
        sys,
        "argv",
        ["edinet_xbrl_probe.py", "--sec-code", "7203", "--days", "30", "--out", str(tmp_path)],
    )

    assert probe_module.main() == 0
    assert captured["args"] == ("7203", 30, tmp_path)


def test_a_missing_key_stops_before_any_request(probe_module, monkeypatch, tmp_path) -> None:
    """鍵が無いのは設定の問題で、401 を見せるより先に言う方が役に立つ。"""
    monkeypatch.setenv("EDINET_API_KEY", "   ")

    with pytest.raises(SystemExit) as excinfo:
        probe_module.probe("6501", 3, tmp_path)
    assert "EDINET_API_KEY" in str(excinfo.value)


def test_the_key_is_sent_both_ways(probe_module) -> None:
    """クエリとヘッダの両方。実機で通ることが確認済みの渡し方。"""
    params, headers = probe_module._auth("SECRET")

    assert params == {"Subscription-Key": "SECRET"}
    assert headers == {"Ocp-Apim-Subscription-Key": "SECRET"}


def test_every_document_type_is_tried(probe_module) -> None:
    """どれが使えるかを決めつけない。全部投げて観測する。"""
    assert set(probe_module.DOCUMENT_TYPES) == {"1", "2", "3", "4", "5"}


def test_a_zip_is_described_by_its_contents(probe_module, tmp_path, capsys) -> None:
    """``Content-Type`` の宣言ではなく、中身で判断する。"""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("XBRL/PublicDoc/jpcrp030000-asr-001.xbrl", b"x")

    probe_module.describe(buffer.getvalue(), tmp_path / "a.bin")

    out = capsys.readouterr().out
    assert "ZIP" in out
    assert "jpcrp030000-asr-001.xbrl" in out


def test_utf16_text_is_read_rather_than_reported_as_binary(probe_module, tmp_path, capsys) -> None:
    """EDINET の CSV は UTF-16。cp932 として読もうとすると化ける。"""
    body = "要素ID\t値\r\njppfs_cor:NetSales\t9728000000000\r\n".encode("utf-16")

    probe_module.describe(body, tmp_path / "b.bin")

    out = capsys.readouterr().out
    assert "utf-16" in out
    assert "jppfs_cor:NetSales" in out
    assert "不明な形式" not in out


def test_a_pdf_is_not_mistaken_for_text(probe_module, tmp_path, capsys) -> None:
    """先頭バイトで分かるものを、文字コードの推測に回さない。"""
    probe_module.describe(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3", tmp_path / "c.bin")

    assert "PDF" in capsys.readouterr().out
