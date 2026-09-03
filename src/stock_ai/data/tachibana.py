"""立花証券・ｅ支店・ＡＰＩ から日本株の日足を取る。

J-Quants の有料プランをやめるための取得先。口座があれば無料で、6501 では
2001年から当日まで 6,279 件が返った。仕様の詳細と、実機で確認した事実は
``docs/TACHIBANA.md`` にある。ここには実装がその事実にどう従っているかだけ書く。

この API には、素直に書くと静かに間違う点が3つある。

**1. ``xK`` 列は「その日より後にある分割係数すべての積」を掛けた値。**
つまり現在の株数単位に直した分割調整済みの系列で、リターンを測るならこちら。
生の ``pDPP`` は実際に取引された価格で、分割日に不連続に飛ぶ。解析層は
``adj_close`` を読むので、そこに ``pDPPxK`` を入れる。

**2. だから差分更新をしてはいけない。** ``xK`` は最新の分割を基準にした相対値で、
新しい分割が起きると過去の行の値が遡って変わる。追記型で更新すると、分割を
またいだ瞬間に古い行だけ古い基準のまま残り、系列が不連続になる。この
プロバイダは ``start`` を無視して常に全期間を返す。API 側に日付範囲の指定が
無く毎回全期間が返る以上、切り捨てても通信量は減らず、失うのは正しさだけ。

**3. 版はパスの Prefix にあり、旧版は後継の並行リリースから約60日で停止する。**
埋め込むと停止日から「原因不明の通信エラー」になる。日付から解決する。
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import pathlib
import re
from typing import Any

import pandas as pd
from pydantic import SecretStr

from stock_ai.core.exceptions import DataError, NoDataError
from stock_ai.core.logging import get_logger, redact
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, DATE, HIGH, LOW, OPEN, VOLUME

logger = get_logger(__name__)

#: 本番と検証のホスト。版はパスの Prefix (``e_api_vNrN``) で切り替わる。
_HOSTS = {
    "production": "https://kabuka.e-shiten.jp",
    "demo": "https://demo-kabuka.e-shiten.jp",
}

#: 版ごとの (公開日, 停止日)。停止日は「その日から使えない」の意味。
_VERSIONS: dict[str, tuple[dt.date, dt.date | None]] = {
    "v4r9": (dt.date(2026, 5, 16), dt.date(2026, 9, 27)),
    "v4r10": (dt.date(2026, 8, 29), None),
}

_FALLBACK_VERSION = "v4r9"

#: 立花の項目名 → 正規化後の列名。``xK`` 付きは分割調整済み。
_PRICE_FIELDS = {OPEN: "pDOP", HIGH: "pDHP", LOW: "pDLP", CLOSE: "pDPP"}


def default_version(today: dt.date | None = None) -> str:
    """その日に使うべき版を返す。

    公開済みのうち最も新しいものを選ぶ。並行リリース期間があるので、
    切り替えても旧版が即座に使えなくなるわけではない。
    """
    now = today or dt.date.today()
    released = [name for name, (start, _end) in _VERSIONS.items() if start <= now]
    if not released:
        return _FALLBACK_VERSION
    # 数字の大きい方が新しい。"v4r10" > "v4r9" は文字列比較では偽になる。
    return max(released, key=lambda name: [int(n) for n in re.findall(r"\d+", name)])


def base_url(version: str, *, demo: bool = False) -> str:
    """版とホストから専用ＵＲＬを組み立てる。"""
    return f"{_HOSTS['demo' if demo else 'production']}/e_api_{version}"


def version_warning(version: str, today: dt.date | None = None) -> str | None:
    """使っている版が期限に近い、または新しい版が出ていれば知らせる。"""
    now = today or dt.date.today()
    known = _VERSIONS.get(version)
    if known is None:
        return f"{version} は把握していない版です。停止予定日を確認してください。"
    _start, end = known
    newest = default_version(now)
    if end is not None and now >= end:
        return f"{version} は {end} に停止済みです。{newest} へ移行してください。"
    if end is not None:
        left = (end - now).days
        if newest != version:
            return f"{version} は {end} に停止します（あと {left} 日）。{newest} が公開済みです。"
        return f"{version} は {end} に停止します（あと {left} 日）。"
    return None


class Session:
    """当日の仮想ＵＲＬと ``p_no`` を持ち越す。

    公式サンプルと同じ挙動。省力化ではなく仕様の要求である。

    - **仮想ＵＲＬは当日限り。** 要求のたびにログインする作りにはしない。
    - **``p_no`` はプロセスをまたいで続く通番。** 毎回 1 から振り直すと、同じ日に
      2回目を走らせた時点で番号が重なる。採番のたびに保存するのはそのため。

    **保存先は資格情報である。** 復号済みの仮想ＵＲＬがそのまま入っており、
    持っている者はその日の口座機能を叩ける。``.gitignore`` 済みで、POSIX では
    0600 を立てる。中身はログにも出さない。
    """

    def __init__(self, path: pathlib.Path) -> None:
        """保存先を決める。読み込みは :meth:`load` で行う。"""
        self.path = path
        self.p_no = 0
        self.urls: dict[str, str] = {}

    def load(self) -> bool:
        """当日分があれば読み込む。使えるものが無ければ ``False``。"""
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if saved.get("date") != f"{dt.date.today():%Y%m%d}":
            return False  # 日付が変われば仮想ＵＲＬは失効している
        urls = saved.get("urls") or {}
        if not urls.get("sUrlPrice"):
            return False
        self.p_no = int(saved.get("p_no", 0))
        self.urls = urls
        return True

    def save(self) -> None:
        """現在の ``p_no`` と仮想ＵＲＬを書き出す。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"date": f"{dt.date.today():%Y%m%d}", "p_no": self.p_no, "urls": self.urls},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        if os.name == "posix":
            self.path.chmod(0o600)

    def discard(self) -> None:
        """保存済みセッションを捨てる。次回はログインからやり直す。"""
        self.path.unlink(missing_ok=True)


def decrypt_url(blob: str, private_key: bytes) -> str:
    r"""暗号化された仮想ＵＲＬを復号する。

    方式は RSA-OAEP（MGF1-SHA256 / SHA256）。平文には改行が付いてくるので
    落とす。忘れると以降の要求ＵＲＬの末尾に ``\r\n`` が残り、原因の見えない
    失敗になる。
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_private_key(private_key, password=None)
    plain = key.decrypt(
        base64.b64decode(blob),
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return plain.decode("ascii").strip()


class TachibanaClient:
    """ｅ支店・ＡＰＩへの1セッション。要求の形は公式サンプルに合わせる。

    共通項目 ``p_no`` / ``p_sd_date`` / ``sJsonOfmt`` は機能ごとの説明には
    書かれておらず、サンプルにしか無い。欠けると要求そのものが通らない。
    """

    def __init__(
        self,
        auth_id: SecretStr,
        private_key: bytes,
        *,
        base: str,
        session: Session,
        timeout: float = 60.0,
    ) -> None:
        """接続先と資格情報を受け取る。ログインは最初の要求まで遅らせる。"""
        self._auth_id = auth_id
        self._private_key = private_key
        self.base = base.rstrip("/")
        self.session = session
        self._timeout = timeout

    def _payload(self, fields: dict[str, str]) -> str:
        """共通ヘッダを足した要求 JSON を組み立てる。

        採番のたびに保存する。プロセスが途中で落ちても番号が巻き戻らない
        ようにするためで、サンプルもそうしている。
        """
        self.session.p_no += 1
        self.session.save()
        body = {
            # ミリ秒はサンプルに合わせて .000 固定。実時刻を入れて弾かれる
            # 余地を残す理由がない。
            "p_no": str(self.session.p_no),
            "p_sd_date": f"{dt.datetime.now():%Y.%m.%d-%H:%M:%S}.000",
            "sJsonOfmt": "5",
            **fields,
        }
        return json.dumps(body, ensure_ascii=False, separators=(",", ":"))

    def request(self, url: str, fields: dict[str, str], *, what: str) -> dict[str, Any]:
        """1要求を投げ、応答を返す。

        Args:
            url: 送信先。認証は専用ＵＲＬ、業務は仮想ＵＲＬ。
            fields: 機能ごとの項目。共通ヘッダは自動で足す。
            what: 失敗時のメッセージに出す、要求の説明。

        Raises:
            DataError: HTTP が 200 以外、本文が読めない、または API がエラーを返した。
        """
        import httpx

        payload = self._payload(fields)
        try:
            # リダイレクトは追わない。認証ＩＤが要求に入るので、追従すると
            # Location が指す先へそのまま再送されることになる。
            with httpx.Client(timeout=self._timeout, follow_redirects=False) as client:
                response = client.post(
                    url,
                    content=payload.encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
        except Exception as exc:  # noqa: BLE001 - 通信の失敗は素通しせず包む
            raise DataError(f"立花への接続に失敗しました（{what}）: {redact(str(exc))}") from None

        if response.is_redirect:
            raise DataError(
                f"立花がリダイレクトを返しました（{what}）: HTTP {response.status_code}"
            )
        # 応答は ShiftJIS。cp932 は shift_jis の上位互換で、機種依存文字も通る。
        text = response.content.decode("cp932", errors="replace")
        if response.status_code != 200:
            raise DataError(f"立花が HTTP {response.status_code} を返しました（{what}）")
        try:
            answer = json.loads(text)
        except json.JSONDecodeError:
            raise DataError(f"立花の応答が JSON として読めません（{what}）") from None

        problem = self._problem(answer)
        if problem:
            raise DataError(f"立花がエラーを返しました（{what}）: {problem}")
        return answer

    @staticmethod
    def _problem(answer: dict[str, Any]) -> str | None:
        """応答のエラーを2段とも見る。問題なければ ``None``。

        伝送層 (``p_errno``) と業務層 (``sResultCode``) は別物で、片方だけ見ると
        もう片方のエラーを正常として素通しする。
        """
        errno = str(answer.get("p_errno", "0"))
        if errno != "0":
            return f"p_errno={errno} p_err={answer.get('p_err', '')!r}"
        code = str(answer.get("sResultCode", "0"))
        if code != "0":
            return f"sResultCode={code} sResultText={answer.get('sResultText', '')!r}"
        return None

    def price_url(self) -> str:
        """時価情報の仮想ＵＲＬ。当日分があれば使い回し、無ければログインする。"""
        return self._virtual_url("sUrlPrice")

    def master_url(self) -> str:
        """マスタの仮想ＵＲＬ。**時価情報とは別の口である。**

        銘柄マスタを ``sUrlPrice`` に投げても通らない。ログイン応答は5本の
        仮想ＵＲＬを返し、機能ごとに使う口が決まっている。
        """
        return self._virtual_url("sUrlMaster")

    def _virtual_url(self, field: str) -> str:
        """当日分の仮想ＵＲＬがあれば使い回し、無ければログインして取り直す。"""
        if self.session.urls.get(field) or self.session.load():
            url = self.session.urls.get(field)
            if url:
                return url
        self._login()
        url = self.session.urls.get(field)
        if not url:
            raise DataError(f"立花が仮想ＵＲＬ {field} を返しませんでした。")
        return url

    def _login(self) -> str:
        """ログインし、仮想ＵＲＬを復号して保存する。時価情報の口を返す。"""
        answer = self.request(
            f"{self.base}/auth/",
            {"sCLMID": "CLMAuthLoginRequest", "sAuthId": self._auth_id.get_secret_value()},
            what="ログイン",
        )
        if answer.get("sKinsyouhouMidokuFlg") == "1":
            raise DataError(
                "金商法交付書面が未読のため、立花は仮想ＵＲＬを発行しません。"
                "e支店の Web サイトで書面を確認してください。"
            )

        urls: dict[str, str] = {}
        for field in ("sUrlRequest", "sUrlMaster", "sUrlPrice", "sUrlEvent"):
            blob = answer.get(field)
            if not blob:
                continue
            try:
                plain = decrypt_url(blob, self._private_key)
            except Exception:  # noqa: BLE001 - 鍵の取り違えを具体的に報告したい
                raise DataError(
                    f"仮想ＵＲＬ {field} を復号できません。利用設定画面に登録した公開鍵と、"
                    "いま使っている秘密鍵が対か確認してください。"
                ) from None
            # 復号が通ったことだけを成功の判定に使わない。鍵や方式を取り違えた
            # まま、意味のないバイト列を接続先として持ち回ることになる。
            if plain.startswith(("http", "ws")):
                urls[field] = plain

        if not urls.get("sUrlPrice"):
            raise DataError("立花が時価情報の仮想ＵＲＬ (sUrlPrice) を返しませんでした。")

        self.session.urls = urls
        self.session.save()
        logger.info("立花にログインしました（仮想ＵＲＬ %d 件）", len(urls))
        return urls["sUrlPrice"]

    def price_history(self, symbol: str) -> list[dict[str, Any]]:
        """``symbol`` の日足を全期間ぶん取る。日付範囲は指定できない。"""
        answer = self.request(
            self.price_url(),
            {
                "sCLMID": "CLMMfdsGetMarketPriceHistory",
                "sIssueCode": symbol,
                "sSizyouC": "00",
            },
            what=f"{symbol} の株価履歴",
        )
        return answer.get("aCLMMfdsMarketPriceHistory") or []

    def issue_masters(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """株式銘柄マスタと株式銘柄市場マスタを取る。要求は機能ＩＤだけ。

        マスタ機能は**システム稼働中に更新されない**ので、1営業日に1回取れば
        足りる。実測（2026-09-03）でどちらも 4,441 件だった。

        Returns:
            ``(銘柄マスタ, 銘柄市場マスタ)``。
        """
        url = self.master_url()
        kabu = self.request(url, {"sCLMID": "CLMStkGetIssueMstKabu"}, what="株式銘柄マスタ").get(
            "aCLMStkIssueMstKabu"
        )
        sizyou = self.request(
            url, {"sCLMID": "CLMStkGetIssueSizyouMstKabu"}, what="株式銘柄市場マスタ"
        ).get("aCLMStkIssueSizyouMstKabu")
        return list(kabu or []), list(sizyou or [])


def normalize_history(records: list[dict[str, Any]], symbol: str) -> pd.DataFrame:
    """立花の日足レコードを正規化 OHLCV に直す。

    ``close`` には実際に取引された価格 (``pDPP``) を、``adj_close`` には分割
    調整済みの価格 (``pDPPxK``) を入れる。解析層はいずれも ``adj_close`` を
    読むので、リターンは分割をまたいでも連続する。

    価格は小数を取る（6501 の実データで生 1,041 行、調整済み 2,275 行）ため
    ``float`` で読む。整数として読むと、併合前の 777.8 が 777 になる。

    **全項目が 0 の行は「取引なし」の印であって、値段 0 ではない。** 6501 には
    2020-10-01 がそう入っている。東証が終日売買を停止した日である。0 を価格と
    して受け取ると、その日へ -100%、翌日へ +inf のリターンが生まれ、窓に触れる
    移動平均・ボラティリティ・モメンタムがまとめて壊れる。祝日と同じく、行ごと
    落とすのが正しい。前日値で埋めない。埋めれば出来高 0 の売買が有ったことに
    なってしまう。

    Raises:
        NoDataError: 有効な行が1つも残らなかった。
    """
    rows: list[dict[str, Any]] = []
    halted = 0
    for record in records:
        try:
            date = dt.datetime.strptime(record["sDate"], "%Y%m%d").date()
            row = {DATE: date, **{col: float(record[key]) for col, key in _PRICE_FIELDS.items()}}
            row[ADJ_CLOSE] = float(record["pDPPxK"])
            row[VOLUME] = int(float(record["pDV"]))
        except (KeyError, TypeError, ValueError):
            # 1行の欠けで銘柄ごと失うのは割に合わない。落とした事実は残す。
            logger.debug("%s: 読めない行を飛ばしました: %r", symbol, record)
            continue
        if any(row[col] <= 0 for col in _PRICE_FIELDS) or row[ADJ_CLOSE] <= 0:
            halted += 1
            continue
        rows.append(row)

    if halted:
        logger.info("%s: 売買が成立していない %d 日を除外しました", symbol, halted)
    if not rows:
        raise NoDataError(f"立花は {symbol!r} の日足を返しませんでした。")

    frame = pd.DataFrame(rows)
    frame[DATE] = pd.to_datetime(frame[DATE])
    frame = frame.drop_duplicates(subset=[DATE], keep="last").sort_values(DATE)
    return frame.set_index(DATE)


def build_client(
    auth_id: SecretStr | None,
    private_key_path: pathlib.Path | str = "tachibana_private.pem",
    *,
    version: str | None = None,
    base: str | None = None,
    session_file: pathlib.Path | str = "tachibana_session.json",
) -> TachibanaClient:
    """設定から接続済みでないクライアントを組み立てる。

    価格とマスタで**同じ組み立て方**を使うためにここに出してある。別々に
    書くと、版の既定やセッションファイルの場所が片方だけずれる。ずれても
    例外は出ないので気付けない。

    Raises:
        DataError: 認証ＩＤまたは秘密鍵が無い。
    """
    if auth_id is None:
        raise DataError(
            "TACHIBANA_AUTH_ID が設定されていません。"
            "ｅ支店の利用設定画面で認証ＩＤを生成し、.env に書いてください。"
        )
    key_path = pathlib.Path(private_key_path)
    if not key_path.exists():
        raise DataError(
            f"立花の秘密鍵が見つかりません: {key_path}。"
            "`checks/立花API確認.bat` を実行すると鍵を作れます。"
        )

    resolved = version or default_version()
    warning = version_warning(resolved)
    if warning and base is None:
        logger.warning("立花ＡＰＩ: %s", warning)

    return TachibanaClient(
        auth_id,
        key_path.read_bytes(),
        base=base or base_url(resolved),
        session=Session(pathlib.Path(session_file)),
    )


class TachibanaPriceProvider:
    """立花証券・ｅ支店・ＡＰＩ から日本株の日足を取る。

    **``start`` を無視して常に全期間を返す。** これは手抜きではなく、この API
    に対して正しい唯一の振る舞いである。理由は2つ:

    1. ``CLMMfdsGetMarketPriceHistory`` に日付範囲の指定が無く、毎回全期間が
       返る。切り捨てても通信量は1バイトも減らない。
    2. ``adj_close`` の元になる ``xK`` 列は**最新の分割を基準にした相対値**で、
       新しい分割が起きると過去の行の値が遡って変わる。新しい行だけを保存すると、
       分割をまたいだ瞬間に古い行だけ古い基準のまま残り、系列が不連続になる。
       毎回すべてを upsert すれば、この不整合は構造的に起こり得ない。

    ``end`` は尊重する。過去時点を再現する検証（``--as-of``）が意味を持つため。
    """

    def __init__(
        self,
        auth_id: SecretStr | None,
        private_key_path: pathlib.Path | str = "tachibana_private.pem",
        *,
        version: str | None = None,
        base: str | None = None,
        session_file: pathlib.Path | str = "tachibana_session.json",
        client: TachibanaClient | None = None,
    ) -> None:
        """接続に必要なものを受け取る。``client`` を渡せば通信を差し替えられる。

        Raises:
            DataError: 認証ＩＤまたは秘密鍵が無い。
        """
        self._client = client or build_client(
            auth_id,
            private_key_path,
            version=version,
            base=base,
            session_file=session_file,
        )

    def fetch_prices(self, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        """``symbol`` の日足を返す。``start`` は無視される（クラスの説明を参照）。"""
        if start > end:
            raise DataError(f"start ({start}) must not be after end ({end}).")

        assert self._client is not None  # noqa: S101 - __init__ が保証している
        frame = normalize_history(self._client.price_history(symbol), symbol)
        frame = frame[frame.index <= pd.Timestamp(end)]
        if frame.empty:
            raise NoDataError(f"立花は {symbol!r} の {end} 以前の日足を返しませんでした。")

        logger.info(
            "立花から %s の日足 %d 件（%s〜%s）。start=%s は無視しています（全期間を置き換えます）",
            symbol,
            len(frame),
            frame.index[0].date(),
            frame.index[-1].date(),
            start,
        )
        return frame
