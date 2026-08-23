"""立花証券・ｅ支店・ＡＰＩの疎通プローブ。本実装の前に実機で1往復だけ通す。

要求の組み立ては公式サンプル（e_api_sample_v4r9.py, MIT）に合わせてある。
マニュアル本文だけでは決まらなかった点が、サンプルで全部確定した:

- **仮想ＵＲＬの暗号は RSA-OAEP（MGF1-SHA256 / SHA256, label なし）。**
  復号後の平文には改行が付くので落とす。
- **要求は既定で HTTP POST、本文が JSON 文字列**（``Content-Type:
  application/json``）。GET も通り、その場合 ``?`` の後ろは**生の JSON**で、
  percent-encode しない。
- **応答は ShiftJIS。** マニュアル本文が ShiftJIS と明示していたのはニュースの
  BASE64 だけだったが、サンプルは応答全体を ``shift-jis`` で復号している。
- **専用ＵＲＬは本番 kabuka / 検証 demo-kabuka の ``e_api_v4r9``。**

サンプルから分かった、マニュアルの機能説明には書かれていない要求項目:

- ``p_no`` は要求ごとに増える通番。サンプルは日付つきファイルに保存して
  翌要求へ引き継ぐ。
- ``p_sd_date`` はミリ秒まで持つが、サンプルは ``.000`` 固定で送っている。
- ``sJsonOfmt`` に ``"5"`` を必ず入れる。
- エラーは2段。まず伝送層の ``p_errno`` / ``p_err``、次に業務層の
  ``sResultCode`` / ``sResultText``。片方だけ見ると素通しする。

**個人情報について。** ログイン応答には口座開設区分（ＮＩＳＡ・信用・先物ＯＰ等の
有無）が含まれる。これは疎通確認に不要なので保存も表示もしない。診断に要る項目
だけを名指しで拾う。認証ＩＤと仮想ＵＲＬも指紋しか出さない。
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import pathlib
import sys
from typing import Any

#: ログイン応答のうち、診断に必要でかつ口座の中身を明かさない項目だけ。
_SAFE_LOGIN_FIELDS = (
    "sCLMID",
    "sResultCode",
    "sResultText",
    "sKinsyouhouMidokuFlg",
    "sUpdateInformWebDocument",
    "sUpdateInformAPISpecFunction",
)

#: 仮想ＵＲＬの項目名。値は復号の前も後も秘密。
_URL_FIELDS = ("sUrlRequest", "sUrlMaster", "sUrlPrice", "sUrlEvent", "sUrlEventWebSocket")

_PRODUCTION = "https://kabuka.e-shiten.jp/e_api_v4r9"
_DEMO = "https://demo-kabuka.e-shiten.jp/e_api_v4r9"


def _fingerprint(value: str) -> str:
    """秘密そのものではなく、同一性だけを比べられる短い指紋を返す。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _sd_date() -> str:
    """``p_sd_date``（``yyyy.mm.dd-hh:mn:ss.ttt``）を返す。

    公式サンプルはミリ秒を ``.000`` 固定で送っている。実時刻を入れて弾かれる
    余地を残す理由がないので、ここでも合わせる。
    """
    return f"{dt.datetime.now():%Y.%m.%d-%H:%M:%S}.000"


def keygen(private_path: pathlib.Path) -> None:
    """RSA鍵ペアを作り、登録用の公開鍵を2形式で表示する。

    サンプルの秘密鍵は PKCS#8 PEM（``-----BEGIN PRIVATE KEY-----``）なので
    こちらもそれで書く。利用設定画面が受け付ける公開鍵の形式は画面を見ないと
    分からないため、両方出して貼り分けてもらう。秘密鍵は表示しない。
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    if private_path.exists():
        sys.exit(f"{private_path} は既にあります。上書きしないので、消すか別名を指定してください。")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    if os.name == "posix":
        private_path.chmod(0o600)

    public = key.public_key()
    spki = public.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    pkcs1 = public.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.PKCS1)

    print(f"秘密鍵を書きました: {private_path}")
    print("  ※ このファイルは .env と同じ扱いです。git に入れないでください。\n")
    print("--- 公開鍵 (X.509 / SubjectPublicKeyInfo). まずこちらを試してください ---")
    print(spki.decode())
    print("--- 公開鍵 (PKCS#1). 上が弾かれたらこちら ---")
    print(pkcs1.decode())


def decrypt_url(blob: str, private_path: pathlib.Path) -> str:
    r"""暗号化された仮想ＵＲＬを復号する。

    方式は公式サンプルどおり RSA-OAEP（MGF1-SHA256 / SHA256）。平文には改行が
    付いてくるので落とす。ここを ``strip()`` し忘れると、以降の要求URLの末尾に
    ``\\r\\n`` が残り、原因の見えない失敗になる。
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    plain = key.decrypt(
        base64.b64decode(blob),
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return plain.decode("ascii").strip()


class Client:
    """ｅ支店・ＡＰＩへの1セッション。要求の組み立てを公式サンプルに合わせる。"""

    def __init__(self, base: str, *, use_post: bool = True) -> None:
        """接続先と送信方法を決める。

        Args:
            base: ｅ支店・ＡＰＩ専用ＵＲＬ。
            use_post: ``True`` で POST（サンプルの既定）、``False`` で GET。
        """
        import httpx

        self.base = base.rstrip("/")
        self.use_post = use_post
        self._p_no = 0
        # リダイレクトは追わない。GET のとき認証ＩＤが要求のクエリ文字列に入るので、
        # 追従すると Location が指す先へそのまま再送されることになる。相手がどこで
        # あれ、秘密を黙って転送するより 302 を観測して報告する方が正しい。
        self._client = httpx.Client(timeout=30.0, follow_redirects=False)

    def __enter__(self) -> Client:
        """``with`` で使えるようにする。"""
        return self

    def __exit__(self, *exc: object) -> None:
        """接続を閉じる。"""
        self._client.close()

    def _payload(self, fields: dict[str, str]) -> str:
        """共通ヘッダを足した要求 JSON を、サンプルと同じ並びで組み立てる。"""
        self._p_no += 1
        body = {
            "p_no": str(self._p_no),
            "p_sd_date": _sd_date(),
            "sJsonOfmt": "5",
            **fields,
        }
        return json.dumps(body, ensure_ascii=False, separators=(",", ":"))

    def request(self, url: str, fields: dict[str, str]) -> tuple[dict[str, Any], str]:
        """1要求を投げ、(応答, 生テキスト) を返す。

        Raises:
            RuntimeError: HTTP が 200 以外、または本文が JSON として読めない。
        """
        payload = self._payload(fields)
        if self.use_post:
            response = self._client.post(
                url, content=payload.encode("utf-8"), headers={"Content-Type": "application/json"}
            )
        else:
            # GET では ``?`` の後ろに生の JSON を置く。percent-encode しない。
            response = self._client.get(f"{url}?{payload}")

        if response.is_redirect:
            location = response.headers.get("location", "(なし)")
            raise RuntimeError(f"HTTP {response.status_code}: リダイレクトされました → {location}")
        # 応答は ShiftJIS。cp932 は shift-jis の上位互換で、機種依存文字も通る。
        text = response.content.decode("cp932", errors="replace")
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}: {text[:200]}")
        try:
            return json.loads(text), text
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"JSON として読めません: {exc}\n先頭300字: {text[:300]}") from None

    @staticmethod
    def check(answer: dict[str, Any]) -> str | None:
        """応答のエラーを2段とも見る。問題なければ ``None``。

        伝送層 (``p_errno``) と業務層 (``sResultCode``) は別物で、片方だけ見ると
        もう片方のエラーを正常として素通しする。
        """
        errno = str(answer.get("p_errno", "0"))
        if errno != "0":
            return f"伝送エラー p_errno={errno} p_err={answer.get('p_err', '')!r}"
        code = str(answer.get("sResultCode", "0"))
        if code != "0":
            return f"業務エラー sResultCode={code} sResultText={answer.get('sResultText', '')!r}"
        return None


def probe(
    base: str,
    auth_id: str,
    private_path: pathlib.Path,
    symbol: str,
    out: pathlib.Path,
    *,
    use_post: bool,
) -> int:
    """ログインから株価履歴1銘柄までを通し、観測結果を報告する。"""
    if not private_path.exists():
        sys.exit(f"秘密鍵が見つかりません: {private_path}\n先に `keygen` を実行してください。")

    method = "POST" if use_post else "GET"
    print(f"接続先: {base.rstrip('/')}   送信方法: {method}")
    print(f"認証ID 指紋: {_fingerprint(auth_id)}  (値そのものは表示しません)\n")

    with Client(base, use_post=use_post) as client:
        # --- 1. ログイン -----------------------------------------------------
        print("--- ログイン ---")
        try:
            login, _ = client.request(
                f"{client.base}/auth/",
                {"sCLMID": "CLMAuthLoginRequest", "sAuthId": auth_id},
            )
        except RuntimeError as exc:
            print(f"失敗: {exc}")
            return 1

        for field in _SAFE_LOGIN_FIELDS:
            if field in login:
                print(f"  {field} = {login[field]!r}")
        problem = Client.check(login)
        if problem:
            print(f"\n{problem}")
            return 1
        if login.get("sKinsyouhouMidokuFlg") == "1":
            print("\n金商法交付書面が未読です。この場合、仮想ＵＲＬは発行されません。")
            print("e支店Webサイトで書面を確認してから、もう一度実行してください。")
            return 1

        # --- 2. 仮想ＵＲＬの復号 ---------------------------------------------
        print("\n--- 仮想ＵＲＬの復号 (RSA-OAEP / SHA256) ---")
        price_url: str | None = None
        for field in _URL_FIELDS:
            blob = login.get(field)
            if not blob:
                print(f"  {field}: （空）")
                continue
            try:
                plain = decrypt_url(blob, private_path)
            except Exception as exc:  # noqa: BLE001 - 原因を観測して報告したい
                print(f"  {field}: 復号できません — {type(exc).__name__}: {exc}")
                continue
            # URL に見えることまで確かめる。復号が「通った」ことだけを成功の判定に
            # 使うと、鍵や方式を取り違えたまま先へ進んでしまう。
            shape = "OK" if plain.startswith("http") else f"URLに見えません {plain[:16]!r}"
            print(f"  {field}: {shape}  指紋 {_fingerprint(plain)}  {len(plain)}字")
            if field == "sUrlPrice" and plain.startswith("http"):
                price_url = plain

        if not price_url:
            print("\n時価情報の仮想ＵＲＬ (sUrlPrice) を復号できませんでした。")
            print("利用設定画面に登録した公開鍵と、いま使っている秘密鍵が対か確認してください。")
            return 1

        # --- 3. 株価履歴を1銘柄だけ取得 ---------------------------------------
        print(f"\n--- 蓄積情報問合取得: {symbol} ---")
        try:
            history, _ = client.request(
                price_url,
                {
                    "sCLMID": "CLMMfdsGetMarketPriceHistory",
                    "sIssueCode": symbol,
                    "sSizyouC": "00",
                },
            )
        except RuntimeError as exc:
            print(f"失敗: {exc}")
            return 1

        problem = Client.check(history)
        if problem:
            print(problem)
            return 1

    bars = history.get("aCLMMfdsMarketPriceHistory") or []
    print(f"レコード数: {len(bars)}")
    if bars:
        print(f"日付の範囲: {bars[0].get('sDate')} 〜 {bars[-1].get('sDate')}")
        print(f"\n最古: {json.dumps(bars[0], ensure_ascii=False)}")
        print(f"最新: {json.dumps(bars[-1], ensure_ascii=False)}")
        splits = [b for b in bars if b.get("pSPUK") not in (None, "", "0")]
        print(f"\n分割係数が入っている日: {len(splits)} 件")
        if splits:
            print(f"  直近: {json.dumps(splits[-1], ensure_ascii=False)}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n生の応答を書きました: {out}")
    print("株価データに個人情報は含まれません。このファイルはそのまま貼って構いません。")
    return 0


def main() -> int:
    """コマンドラインから keygen / probe を実行する。"""
    parser = argparse.ArgumentParser(description="立花証券・ｅ支店・ＡＰＩの疎通プローブ")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("keygen", help="RSA鍵ペアを作り、登録用の公開鍵を表示する")
    gen.add_argument("--private", type=pathlib.Path, default=pathlib.Path("tachibana_private.pem"))

    run = sub.add_parser("probe", help="ログイン〜株価履歴1銘柄までを実際に通す")
    run.add_argument("--private", type=pathlib.Path, default=pathlib.Path("tachibana_private.pem"))
    run.add_argument("--symbol", default="6501", help="試す銘柄コード（既定: 6501 日立）")
    run.add_argument(
        "--base",
        default=os.environ.get("TACHIBANA_BASE_URL") or _PRODUCTION,
        help=f"専用ＵＲＬ。既定 {_PRODUCTION}",
    )
    run.add_argument(
        "--demo",
        action="store_true",
        help=f"検証環境 ({_DEMO}) を使う。利用時間帯が決まっている点に注意",
    )
    run.add_argument("--get", action="store_true", help="POST ではなく GET で送る")
    run.add_argument("--out", type=pathlib.Path, default=pathlib.Path("tachibana_history.json"))

    args = parser.parse_args()
    if args.command == "keygen":
        keygen(args.private)
        return 0

    auth_id = os.environ.get("TACHIBANA_AUTH_ID", "").strip()
    if not auth_id:
        sys.exit(
            "環境変数 TACHIBANA_AUTH_ID が空です。\n"
            "利用設定画面で生成した認証IDを .env に書くか、実行前に設定してください。"
        )
    base = _DEMO if args.demo else args.base
    return probe(base, auth_id, args.private, args.symbol, args.out, use_post=not args.get)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    raise SystemExit(main())
