"""立花証券・ｅ支店・ＡＰＩの疎通プローブ。本実装の前に仕様を実機で確定する。

マニュアル本文だけでは決められない点が4つある。どれも推測で実装すると、
それらしく動いて静かに間違う類のものなので、100行書く前に1往復で確かめる。

1. **仮想ＵＲＬの暗号方式。** 「登録した公開鍵で暗号化」としか書かれておらず、
   アルゴリズムもパディングも別資料にある。ここは総当たりで観測する。
2. **JSON引数のURLエンコード要否。** マニュアルの例は生のJSONを ``?`` の後ろに
   置いている。ブラウザは自動で percent-encode するので、実際の要求がどちらの
   形なのかは例からは決まらない。
3. **応答の文字コード。** 一部資料は ShiftJIS と書くが、本文中で ShiftJIS を
   明示しているのはニュース本文のBASE64だけ。宣言を信じず実測する。
4. **専用ＵＲＬの版。** ``e_api_vNrN`` は雛形で、実値は利用設定画面で確認する。

**個人情報について。** ログイン応答には口座開設区分（ＮＩＳＡ・信用・先物ＯＰ等の
有無）が含まれる。これは仕様確定に不要なので保存も表示もしない。診断に要る項目
だけを名指しで拾う。仮想ＵＲＬと認証ＩＤも当然出さない。
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
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

#: 仮想ＵＲＬの項目名。値は復号前も復号後も秘密。
_URL_FIELDS = ("sUrlRequest", "sUrlMaster", "sUrlPrice", "sUrlEvent", "sUrlEventWebSocket")


def _stamp() -> str:
    """``p_sd_date`` の形式 ``yyyy.mm.dd-hh:mn:ss.ttt`` で現在時刻を返す。"""
    now = dt.datetime.now()
    return f"{now:%Y.%m.%d-%H:%M:%S}.{now.microsecond // 1000:03d}"


def _fingerprint(value: str) -> str:
    """秘密そのものではなく、同一性だけを比べられる短い指紋を返す。"""
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def keygen(private_path: pathlib.Path) -> None:
    """RSA鍵ペアを作り、公開鍵を2形式で表示する。

    利用設定画面がどちらの形式を受け付けるかは画面を見ないと分からないので、
    両方出して貼り分けてもらう。秘密鍵はファイルにのみ書き、表示しない。
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


def _login_url(base: str, auth_id: str, *, encode: bool) -> str:
    """ログイン要求のURLを組み立てる。``encode`` で引数の渡し方を切り替える。"""
    from urllib.parse import quote

    payload = json.dumps(
        {
            "p_no": "1",
            "p_sd_date": _stamp(),
            "sCLMID": "CLMAuthLoginRequest",
            "sAuthId": auth_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    query = quote(payload, safe="") if encode else payload
    return f"{base.rstrip('/')}/auth/?{query}"


def _decode(raw: bytes) -> tuple[str, str]:
    """本文を復号し、(文字コード名, 文字列) を返す。宣言ではなく実測で決める。"""
    for name in ("utf-8", "cp932"):
        try:
            return name, raw.decode(name)
        except UnicodeDecodeError:
            continue
    return "utf-8/replace", raw.decode("utf-8", errors="replace")


def _try_decrypt(blob: str, private_path: pathlib.Path) -> tuple[str, str] | None:
    """暗号化された仮想ＵＲＬを、パディングを総当たりして復号する。

    復号できたことを成功の判定に使ってはいけない。PKCS1v15 は、別方式で
    暗号化された文字列を渡しても例外を投げずにゴミを返すことがある（この
    プローブの自己テストで実際に起きた）。方式を取り違えたまま「成功」と
    報告すれば、本実装がそのまま間違った前提の上に乗る。

    そこで平文が仮想ＵＲＬに見えること――``http`` で始まること――まで
    確かめて初めて成功とする。どれも URL にならなかった場合は、黙って
    諦めるのではなく候補を全部見せる。

    Returns:
        (方式名, 復号結果) か、URLとして通ったものが無ければ ``None``。
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    try:
        cipher = base64.b64decode(blob, validate=True)
    except Exception as exc:  # noqa: BLE001 - 何が来ても観測して報告したい
        print(f"    BASE64として読めません: {exc}")
        return None

    schemes: list[tuple[str, Any]] = [
        (
            "OAEP-SHA256",
            padding.OAEP(padding.MGF1(hashes.SHA256()), hashes.SHA256(), None),
        ),
        (
            "OAEP-SHA1",
            padding.OAEP(padding.MGF1(hashes.SHA1()), hashes.SHA1(), None),  # noqa: S303
        ),
        ("PKCS1v15", padding.PKCS1v15()),
    ]
    candidates: list[tuple[str, str]] = []
    for name, scheme in schemes:
        try:
            plain = key.decrypt(cipher, scheme)
        except Exception:  # noqa: BLE001 - 失敗が普通なので次を試す
            continue
        text = plain.decode("utf-8", errors="replace")
        if text.startswith("http"):
            return name, text
        candidates.append((name, text))

    for name, text in candidates:
        print(f"    {name}: 復号は通りましたが URL に見えません（先頭16字 {text[:16]!r}）")
    return None


def probe(
    base: str,
    auth_id: str,
    private_path: pathlib.Path,
    symbol: str,
    out: pathlib.Path,
) -> int:
    """ログインから株価履歴1銘柄までを1往復ずつ通し、観測結果を報告する。"""
    import httpx

    if not private_path.exists():
        sys.exit(f"秘密鍵が見つかりません: {private_path}\n先に `keygen` を実行してください。")

    print(f"認証ID 指紋: {_fingerprint(auth_id)}  (値そのものは表示しません)")
    print(f"接続先: {base.rstrip('/')}/auth/\n")

    # --- 1. ログイン。引数の渡し方が未確定なので両方試す ---------------------
    login: dict[str, Any] | None = None
    encode_args = False
    # リダイレクトは追わない。認証IDは要求のクエリ文字列に入るので、追従すると
    # Location が指す先へそのまま再送されることになる。相手がどこであれ、秘密を
    # 黙って転送するより、302 を観測して報告する方がプローブとして正しい。
    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        for encode in (False, True):
            label = "percent-encoded" if encode else "生JSON"
            try:
                response = client.get(_login_url(base, auth_id, encode=encode))
            except Exception as exc:  # noqa: BLE001 - 到達性そのものを観測している
                print(f"[{label}] 接続失敗: {type(exc).__name__}: {exc}")
                continue
            charset, text = _decode(response.content)
            size = len(response.content)
            print(f"[{label}] HTTP {response.status_code}, 文字コード={charset}, {size} bytes")
            if response.is_redirect:
                # 転送先そのものは出す（秘密ではない）。追従はしない。
                print(f"    リダイレクト先: {response.headers.get('location', '(なし)')}")
                continue
            if response.status_code != 200:
                print(f"    本文: {text[:200]}")
                continue
            try:
                login = json.loads(text)
            except json.JSONDecodeError as exc:
                print(f"    JSONとして読めません: {exc}")
                print(f"    先頭200字: {text[:200]}")
                continue
            print(f"    → この渡し方が通りました（引数は{label}で送る）")
            encode_args = encode
            break

        if login is None:
            print("\nログインできませんでした。上の観測結果を貼ってください。")
            return 1

        print("\n--- ログイン応答（口座情報は除外して表示） ---")
        for field in _SAFE_LOGIN_FIELDS:
            if field in login:
                print(f"  {field} = {login[field]!r}")
        if login.get("sResultCode") not in ("0", 0):
            print("\n結果コードが 0 ではありません。上記 sResultText を確認してください。")
            return 1
        if login.get("sKinsyouhouMidokuFlg") == "1":
            print("\n金商法交付書面が未読です。この場合、仮想ＵＲＬは発行されません。")
            print("e支店Webサイトで書面を確認してから、もう一度実行してください。")
            return 1

        # --- 2. 仮想ＵＲＬの復号。暗号方式が未確定なので総当たり -------------
        print("\n--- 仮想ＵＲＬの復号 ---")
        scheme_used: str | None = None
        price_url: str | None = None
        for field in _URL_FIELDS:
            blob = login.get(field)
            if not blob:
                print(f"  {field}: （空）")
                continue
            print(f"  {field}: 暗号文 {len(blob)} 字")
            result = _try_decrypt(blob, private_path)
            if result is None:
                print("    → PKCS1v15 / OAEP-SHA1 / OAEP-SHA256 のいずれでも復号できません")
                continue
            scheme, plain = result
            scheme_used = scheme
            print(f"    → {scheme} で復号成功。指紋 {_fingerprint(plain)}、{len(plain)} 字")
            if not plain.startswith("http"):
                print(f"    ※ URLに見えません。先頭20字: {plain[:20]!r}")
            if field == "sUrlPrice":
                price_url = plain

        if scheme_used is None:
            print("\n仮想ＵＲＬを復号できませんでした。登録した公開鍵と、いま使っている")
            print("秘密鍵が対になっているかを確認してください。")
            return 1
        print(f"\n暗号方式は {scheme_used} と確定しました。")

        if not price_url:
            print("時価情報の仮想ＵＲＬ (sUrlPrice) が取れませんでした。")
            return 1

        # --- 3. 株価履歴を1銘柄だけ取得 ---------------------------------------
        print(f"\n--- 蓄積情報問合取得: {symbol} ---")
        payload = json.dumps(
            {
                "p_no": "2",
                "p_sd_date": _stamp(),
                "sCLMID": "CLMMfdsGetMarketPriceHistory",
                "sIssueCode": symbol,
                "sSizyouC": "00",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        from urllib.parse import quote

        # ログインで通った渡し方をそのまま使う。認証と業務で違う可能性は低く、
        # 違ったならここで落ちて分かる。
        query = quote(payload, safe="") if encode_args else payload
        try:
            response = client.get(f"{price_url}?{query}")
        except Exception as exc:  # noqa: BLE001
            print(f"接続失敗: {type(exc).__name__}: {exc}")
            return 1
        charset, text = _decode(response.content)
        print(f"HTTP {response.status_code}, 文字コード={charset}, {len(response.content)} bytes")
        if response.status_code != 200:
            print(f"本文: {text[:300]}")
            return 1
        try:
            history = json.loads(text)
        except json.JSONDecodeError as exc:
            print(f"JSONとして読めません: {exc}\n先頭300字: {text[:300]}")
            return 1

    bars = history.get("aCLMMfdsMarketPriceHistory") or []
    print(f"結果コード: {history.get('sResultCode', '(なし)')!r}  レコード数: {len(bars)}")
    if bars:
        print("\n最古の1件:")
        print(f"  {json.dumps(bars[0], ensure_ascii=False)}")
        print("最新の1件:")
        print(f"  {json.dumps(bars[-1], ensure_ascii=False)}")
        print(f"\n日付の範囲: {bars[0].get('sDate')} 〜 {bars[-1].get('sDate')}")
        splits = [b for b in bars if b.get("pSPUK") not in (None, "", "0")]
        print(f"分割係数が入っている日: {len(splits)} 件")
        if splits:
            print(f"  例: {json.dumps(splits[-1], ensure_ascii=False)}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n生の応答を書きました: {out}")
    print("株価データに個人情報は含まれません。このファイルはそのまま貼って構いません。")
    return 0


def main() -> int:
    """コマンドラインから keygen / probe を実行する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("keygen", help="RSA鍵ペアを作り、登録用の公開鍵を表示する")
    gen.add_argument("--private", type=pathlib.Path, default=pathlib.Path("tachibana_private.pem"))

    run = sub.add_parser("probe", help="ログイン〜株価履歴1銘柄までを実際に通す")
    run.add_argument("--private", type=pathlib.Path, default=pathlib.Path("tachibana_private.pem"))
    run.add_argument("--symbol", default="6501", help="試す銘柄コード（既定: 6501 日立）")
    run.add_argument(
        "--base",
        default=os.environ.get("TACHIBANA_BASE_URL", "https://kabuka.e-shiten.jp/e_api_v4r9"),
        help="ｅ支店・ＡＰＩ専用ＵＲＬ。利用設定画面の表示に合わせる",
    )
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
    return probe(args.base, auth_id, args.private, args.symbol, args.out)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    raise SystemExit(main())
