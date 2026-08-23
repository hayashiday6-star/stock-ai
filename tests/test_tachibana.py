"""立花証券・ｅ支店・ＡＰＩ のプロバイダ。

ここにあるテストは、実データ（6501 の 6,279 件）を見て分かったことを固定する
ためのもので、思いつきの網羅ではない。それぞれ、間違えると**例外を出さずに
結果だけが狂う**箇所に対応している。
"""

from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import pytest
from pydantic import SecretStr

from stock_ai.core.exceptions import DataError, NoDataError
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, OHLCV_COLUMNS, OPEN, VOLUME
from stock_ai.data.tachibana import (
    Session,
    TachibanaClient,
    TachibanaPriceProvider,
    base_url,
    default_version,
    normalize_history,
    version_warning,
)


def bar(date: str, close: float, adj: float, volume: int = 1000) -> dict[str, str]:
    """1日ぶんのレコード。実際の応答と同じく値はすべて文字列。"""
    return {
        "sDate": date,
        "pDOP": str(close),
        "pDHP": str(close),
        "pDLP": str(close),
        "pDPP": str(close),
        "pDOPxK": str(adj),
        "pDHPxK": str(adj),
        "pDLPxK": str(adj),
        "pDPPxK": str(adj),
        "pDV": str(volume),
        "pDVxK": str(volume),
    }


# --- 正規化 -------------------------------------------------------------------


def test_adj_close_comes_from_the_split_adjusted_column() -> None:
    """``close`` は実際の取引価格、``adj_close`` は分割調整済み。

    6501 の 2018-09-26（5→1 の併合）は、生の終値が 777.8 から 3894 へ5倍に
    飛ぶ一方、調整済みは 777.8 から 778.8 とほぼ変わらない。解析層はいずれも
    ``adj_close`` を読むので、ここを取り違えるとリターンが分割日に5倍になる。
    """
    frame = normalize_history([bar("20180925", 777.8, 777.8), bar("20180926", 3894, 778.8)], "6501")

    assert list(frame.columns) == OHLCV_COLUMNS
    assert frame[CLOSE].tolist() == [777.8, 3894.0]
    assert frame[ADJ_CLOSE].tolist() == [777.8, 778.8]

    jump = frame[CLOSE].pct_change().iloc[-1]
    smooth = frame[ADJ_CLOSE].pct_change().iloc[-1]
    assert jump > 4.0  # 生の系列は飛ぶ。それが正しい
    assert abs(smooth) < 0.05  # 調整済みは飛ばない


def test_prices_keep_their_decimals() -> None:
    """整数として読むと、併合前の 777.8 が 777 になる。"""
    frame = normalize_history([bar("20180925", 777.8, 777.8)], "6501")

    assert frame[CLOSE].iloc[0] == pytest.approx(777.8)
    assert frame[OPEN].iloc[0] == pytest.approx(777.8)
    assert frame.dtypes[CLOSE] == "float64"
    assert frame.dtypes[VOLUME] == "int64"


def test_a_day_the_exchange_never_opened_is_dropped() -> None:
    """全項目 0 は「取引なし」の印であって、値段 0 ではない。

    6501 の 2020-10-01 がそれで、東証が終日売買を停止した日。0 を価格として
    受け取ると、その日へ -100%、翌日へ +inf のリターンが生まれ、窓に触れる
    移動平均・ボラティリティ・モメンタムがまとめて壊れる。
    """
    records = [
        bar("20200930", 3543, 708.6),
        {  # 東証システム障害の日。生データそのままの形
            "sDate": "20201001",
            "pDOP": "0",
            "pDHP": "0",
            "pDLP": "0",
            "pDPP": "0",
            "pDV": "0",
            "pDOPxK": "0",
            "pDHPxK": "0",
            "pDLPxK": "0",
            "pDPPxK": "0",
            "pDVxK": "0",
        },
        bar("20201002", 3508, 701.6),
    ]

    frame = normalize_history(records, "6501")

    assert len(frame) == 2
    assert pd.Timestamp("2020-10-01") not in frame.index
    # 前日値で埋めてもいけない。出来高 0 の売買が有ったことになる。
    assert frame[ADJ_CLOSE].tolist() == [708.6, 701.6]
    returns = frame[ADJ_CLOSE].pct_change().dropna()
    assert returns.abs().max() < 0.5


def test_unsorted_and_duplicated_days_are_resolved() -> None:
    """順序と重複はデータ側の都合で、下流に持ち込まない。"""
    frame = normalize_history(
        [bar("20240103", 3, 3), bar("20240101", 1, 1), bar("20240101", 9, 9)], "6501"
    )

    assert [d.strftime("%Y%m%d") for d in frame.index] == ["20240101", "20240103"]
    assert frame[CLOSE].tolist() == [9.0, 3.0]  # 同じ日は後の行が勝つ


def test_an_unreadable_row_does_not_cost_the_whole_symbol() -> None:
    """1行の欠けで銘柄ごと失うのは割に合わない。"""
    frame = normalize_history(
        [bar("20240101", 1, 1), {"sDate": "20240102"}, bar("20240103", 3, 3)], "6501"
    )

    assert len(frame) == 2


def test_no_usable_rows_is_a_no_data_error() -> None:
    """空を静かに返すと、呼び出し側は「新しい足が無い」と読む。"""
    with pytest.raises(NoDataError):
        normalize_history([], "6501")


# --- プロバイダ ---------------------------------------------------------------


class FakeClient:
    """通信の代わり。要求された銘柄を記録する。"""

    def __init__(self, records: list[dict[str, str]]) -> None:
        self.records = records
        self.asked: list[str] = []

    def price_history(self, symbol: str) -> list[dict[str, str]]:
        self.asked.append(symbol)
        return self.records


def test_start_is_ignored_so_the_whole_series_is_replaced() -> None:
    """``xK`` は最新の分割を基準にした相対値で、分割のたびに過去が変わる。

    新しい行だけを保存すると、分割をまたいだ瞬間に古い行だけ古い基準のまま
    残り、系列が不連続になる。API 側に日付範囲の指定が無く毎回全期間が返る
    以上、切り捨てても通信量は減らず、失うのは正しさだけ。
    """
    records = [bar(f"2024010{d}", d, d) for d in range(1, 6)]
    provider = TachibanaPriceProvider(None, client=FakeClient(records))

    # 「最後の1日だけ」を求めても、全期間が返る。
    frame = provider.fetch_prices("6501", dt.date(2024, 1, 5), dt.date(2024, 1, 5))

    assert len(frame) == 5
    assert frame.index[0] == pd.Timestamp("2024-01-01")


def test_end_is_respected_because_as_of_runs_depend_on_it() -> None:
    """過去時点の再現（``--as-of``）が意味を持つために、終端は切る。"""
    records = [bar(f"2024010{d}", d, d) for d in range(1, 6)]
    provider = TachibanaPriceProvider(None, client=FakeClient(records))

    frame = provider.fetch_prices("6501", dt.date(2020, 1, 1), dt.date(2024, 1, 3))

    assert frame.index[-1] == pd.Timestamp("2024-01-03")
    assert len(frame) == 3


def test_nothing_on_or_before_end_is_a_no_data_error() -> None:
    """空フレームを返すと、上流は「取れた」と扱ってしまう。"""
    provider = TachibanaPriceProvider(None, client=FakeClient([bar("20240105", 5, 5)]))

    with pytest.raises(NoDataError):
        provider.fetch_prices("6501", dt.date(2020, 1, 1), dt.date(2024, 1, 1))


def test_a_backwards_range_is_rejected(tmp_path) -> None:
    """呼び出し側の誤りを、空の結果として飲み込まない。"""
    provider = TachibanaPriceProvider(None, client=FakeClient([bar("20240101", 1, 1)]))

    with pytest.raises(DataError):
        provider.fetch_prices("6501", dt.date(2024, 1, 5), dt.date(2024, 1, 1))


def test_a_missing_auth_id_names_what_to_do(tmp_path) -> None:
    """「認証エラー」より「.env に何を書くか」の方が役に立つ。"""
    with pytest.raises(DataError) as excinfo:
        TachibanaPriceProvider(None, tmp_path / "k.pem")
    assert "TACHIBANA_AUTH_ID" in str(excinfo.value)


def test_a_missing_private_key_names_the_path(tmp_path) -> None:
    """鍵が無いのか、鍵が違うのかは別の失敗。"""
    with pytest.raises(DataError) as excinfo:
        TachibanaPriceProvider(SecretStr("id"), tmp_path / "missing.pem")
    assert "missing.pem" in str(excinfo.value)


# --- クライアント -------------------------------------------------------------
#
# ここは模擬サーバを立てて実際に HTTP を通す。要求の形（p_no / p_sd_date /
# sJsonOfmt）はマニュアルの機能説明には無く、公式サンプルにしか書かれていない。
# 欠けると要求そのものが通らないので、送っている中身まで確かめる。


@pytest.fixture
def eshiten(tmp_path):
    """立花を模したサーバ。受け取った要求を記録して返す。"""
    import base64
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    state: dict = {"requests": [], "login_flags": {}, "bars": [bar("20240101", 1, 1)]}

    def encrypt(text: str) -> str:
        # 実サーバ同様、平文に改行を付けてから暗号化する。
        blob = key.public_key().encrypt(
            (text + "\r\n").encode("ascii"),
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
        return base64.b64encode(blob).decode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: D102 - テスト中は黙らせる
            pass

        def do_POST(self):  # noqa: N802, D102 - BaseHTTPRequestHandler の規約
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            request = json.loads(body.decode("utf-8"))
            state["requests"].append(
                {"path": self.path, "body": request, "ctype": self.headers.get("Content-Type")}
            )
            if request.get("sCLMID") == "CLMAuthLoginRequest":
                answer = {
                    "sCLMID": "CLMAuthLoginAck",
                    "p_errno": "0",
                    "p_err": "",
                    "sResultCode": "0",
                    "sResultText": "",
                    "sUrlRequest": encrypt(f"{state['base']}/request"),
                    "sUrlPrice": encrypt(f"{state['base']}/price"),
                    "sUrlEventWebSocket": encrypt("wss://price.example/ws"),
                    **state["login_flags"],
                }
            else:
                answer = {
                    "sCLMID": request["sCLMID"],
                    "p_errno": "0",
                    "p_err": "",
                    "sResultCode": "0",
                    "sResultText": "",
                    "aCLMMfdsMarketPriceHistory": state["bars"],
                }
            payload = json.dumps(answer, ensure_ascii=False).encode("shift-jis")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    state["base"] = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["private_pem"] = private_pem
    state["session_file"] = tmp_path / "session.json"
    yield state
    server.shutdown()


def _client(eshiten) -> TachibanaClient:
    """模擬サーバに向いたクライアント。"""
    return TachibanaClient(
        SecretStr("AUTHID"),
        eshiten["private_pem"],
        base=eshiten["base"],
        session=Session(eshiten["session_file"]),
    )


def test_the_request_carries_the_fields_only_the_sample_documents(eshiten) -> None:
    """``p_no`` / ``p_sd_date`` / ``sJsonOfmt`` が欠けると要求が通らない。"""
    import re

    _client(eshiten).price_history("6501")

    sent = eshiten["requests"]
    assert [r["body"]["sCLMID"] for r in sent] == [
        "CLMAuthLoginRequest",
        "CLMMfdsGetMarketPriceHistory",
    ]
    assert [r["body"]["p_no"] for r in sent] == ["1", "2"], "p_no は要求ごとに増える"
    assert all(r["body"]["sJsonOfmt"] == "5" for r in sent)
    assert all(r["ctype"] == "application/json" for r in sent)
    for request in sent:
        # ミリ秒はサンプルどおり .000 固定。
        stamp = request["body"]["p_sd_date"]
        assert re.fullmatch(r"\d{4}\.\d{2}\.\d{2}-\d{2}:\d{2}:\d{2}\.000", stamp)


def test_the_session_is_reused_instead_of_logging_in_again(eshiten) -> None:
    """仮想ＵＲＬは当日限り。銘柄ごとにログインし直すのは仕様の誤読。"""
    client = _client(eshiten)
    client.price_history("6501")
    client.price_history("7203")
    client.price_history("8306")

    logins = [r for r in eshiten["requests"] if r["body"]["sCLMID"] == "CLMAuthLoginRequest"]
    assert len(logins) == 1
    # p_no は通し番号で、銘柄をまたいでも巻き戻らない。
    assert [r["body"]["p_no"] for r in eshiten["requests"]] == ["1", "2", "3", "4"]


def test_a_second_process_continues_the_same_numbering(eshiten) -> None:
    """``p_no`` はプロセスをまたいで続く。保存しないと同じ日に番号が重なる。"""
    _client(eshiten).price_history("6501")
    _client(eshiten).price_history("7203")

    assert [r["body"]["p_no"] for r in eshiten["requests"]] == ["1", "2", "3"]
    logins = [r for r in eshiten["requests"] if r["body"]["sCLMID"] == "CLMAuthLoginRequest"]
    assert len(logins) == 1, "2つ目のプロセスも保存済みの仮想ＵＲＬを使う"


def test_a_business_error_is_not_read_as_success(eshiten) -> None:
    """伝送層 ``p_errno`` と業務層 ``sResultCode`` は別物。片方だけ見ると素通しする。"""
    eshiten["login_flags"] = {"sResultCode": "10035", "sResultText": "暗証番号違い"}

    with pytest.raises(DataError) as excinfo:
        _client(eshiten).price_history("6501")
    assert "10035" in str(excinfo.value)
    # ShiftJIS の応答が読めていることも、ここで同時に確かめている。
    assert "暗証番号違い" in str(excinfo.value)


def test_an_unread_disclosure_document_is_named_as_the_cause(eshiten) -> None:
    """このとき仮想ＵＲＬは空で返る。「復号できません」では原因に届かない。"""
    eshiten["login_flags"] = {"sKinsyouhouMidokuFlg": "1"}

    with pytest.raises(DataError) as excinfo:
        _client(eshiten).price_history("6501")
    assert "金商法" in str(excinfo.value)


def test_the_session_file_never_shows_up_in_an_error(eshiten) -> None:
    """保存された仮想ＵＲＬは資格情報。例外に混ぜて回さない。"""
    client = _client(eshiten)
    client.price_history("6501")
    saved = json.loads(eshiten["session_file"].read_text(encoding="utf-8"))

    assert saved["urls"]["sUrlPrice"].startswith("http")
    eshiten["bars"] = []
    with pytest.raises(NoDataError) as excinfo:
        TachibanaPriceProvider(None, client=client).fetch_prices(
            "6501", dt.date(2020, 1, 1), dt.date(2030, 1, 1)
        )
    assert saved["urls"]["sUrlPrice"] not in str(excinfo.value)


# --- どこから取るかの決まり方 --------------------------------------------------


@pytest.mark.parametrize(
    ("market", "requested", "jp_setting", "expected"),
    [
        # 米国株は常に yfinance。--source に何を書かれても関係ない。
        ("US", "jquants", "tachibana", "yfinance"),
        ("US", "yfinance", "jquants", "yfinance"),
        # 日本株は設定が既定を決める。.env 一行で全体を切り替えられる。
        ("JP", "yfinance", "jquants", "jquants"),
        ("JP", "yfinance", "tachibana", "tachibana"),
        # 明示された --source が日本株を扱えるなら、そちらが勝つ。
        ("JP", "tachibana", "jquants", "tachibana"),
        ("JP", "jquants", "tachibana", "jquants"),
    ],
)
def test_the_source_follows_the_ticker_then_the_setting(
    market, requested, jp_setting, expected, monkeypatch
) -> None:
    """銘柄が市場を決め、市場が取得先を決める。日本株だけ選択肢がある。"""
    from stock_ai.cli import _source_for_market
    from stock_ai.config.settings import Settings

    settings = Settings(JP_PRICE_SOURCE=jp_setting)
    assert _source_for_market(market, requested, settings) == expected


def test_an_unknown_source_names_the_ones_that_exist() -> None:
    """打ち間違いを、既定への静かな読み替えで飲み込まない。"""
    import typer

    from stock_ai.cli import _price_source
    from stock_ai.config.settings import Settings

    with pytest.raises(typer.BadParameter) as excinfo:
        _price_source("tachibna", Settings())
    assert "tachibana" in str(excinfo.value)


# --- 版 -----------------------------------------------------------------------


def test_the_newer_version_wins_once_released() -> None:
    """ "v4r10" > "v4r9" は文字列比較では偽。数値で比べる必要がある。"""
    assert default_version(dt.date(2026, 8, 28)) == "v4r9"
    assert default_version(dt.date(2026, 8, 29)) == "v4r10"


def test_a_stopped_version_says_so_rather_than_failing_obscurely() -> None:
    """v4r9 は 2026-09-27 に止まる。埋め込むと通信エラーとして現れる。"""
    before = version_warning("v4r9", dt.date(2026, 9, 26))
    assert before is not None and "あと 1 日" in before

    after = version_warning("v4r9", dt.date(2026, 9, 27))
    assert after is not None and "停止済み" in after and "v4r10" in after

    assert version_warning("v4r10", dt.date(2026, 9, 27)) is None
    assert version_warning("v5r1", dt.date(2026, 9, 1)) is not None


def test_the_version_lives_in_the_path() -> None:
    """ホスト名ではなくパスの Prefix にある。"""
    assert base_url("v4r10") == "https://kabuka.e-shiten.jp/e_api_v4r10"
    assert base_url("v4r10", demo=True) == "https://demo-kabuka.e-shiten.jp/e_api_v4r10"


# --- 保存 ---------------------------------------------------------------------


def test_a_full_25_year_series_can_actually_be_stored(tmp_path) -> None:
    """1文にまとめると SQLite のバインド変数上限を超える。

    6,278 行 × 8 列 = 50,224 個で、Windows の既定 32,766 を超えて実行が落ちた。
    J-Quants の5年分（約 9,600 個）では届いていなかったため、25年分を初めて
    保存した瞬間まで表面化しなかった。

        OperationalError: too many SQL variables

    上限はビルドによって 999 / 32,766 / 250,000 と異なる。開発環境が大きい値を
    返すと、そこでは分割が起きず利用者の環境でだけ落ちる。だからこのテストは、
    件数の多さそのものを通す。
    """
    import pandas as pd

    from stock_ai.database.engine import Database
    from stock_ai.database.repository import PriceRepository

    database = Database(url=f"sqlite:///{tmp_path / 'big.db'}")
    database.create_all()

    days = pd.bdate_range("2001-01-04", periods=6278)
    frame = pd.DataFrame(
        {
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "adj_close": 1.5,
            "volume": 100,
        },
        index=pd.DatetimeIndex(days, name="date"),
    )

    with database.session() as session:
        written = PriceRepository(session).upsert_prices("6501", frame, market="JP")
    assert written == 6278

    with database.session() as session:
        stored = PriceRepository(session).get_prices("6501")
    assert len(stored) == 6278

    # 再実行しても増えない（upsert であること）。
    with database.session() as session:
        PriceRepository(session).upsert_prices("6501", frame, market="JP")
    with database.session() as session:
        assert len(PriceRepository(session).get_prices("6501")) == 6278


def test_the_batch_size_stays_under_the_portable_limit() -> None:
    """開発環境が大きい上限を返しても、分割の挙動は変わってはいけない。"""
    from stock_ai.database.repository import chunked, max_bound_parameters

    assert max_bound_parameters() <= 32766

    columns = 8
    batches = list(chunked([{"n": i} for i in range(6278)], columns))
    assert sum(len(b) for b in batches) == 6278
    assert max(len(b) for b in batches) * columns < 32766

    assert list(chunked([], columns)) == []
    assert [len(b) for b in chunked([{"n": 1}], columns)] == [1]
