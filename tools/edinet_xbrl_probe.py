"""EDINET の書類本体を1件だけ取り、中身の形を観測する。

いま我々が読んでいるのは ``documents.json``――目録だけで、書類そのものは開いて
いない。README にも「a ``[HIGH]`` on a 変更報告書 is a verdict on its title」と
記録がある。有価証券報告書の中身（売上・純利益・自己資本）を自前で持てば、
J-Quants の有料プランを完全に外せる。

その前に確かめることが2つある。どちらも推測で実装すると、例外を出さずに
間違った財務データを持つことになる。

**1. ``type`` に何を渡すと何が返るのか。** ``documents/{docID}`` は ``type`` で
返すものが変わる。ZIP に入った XBRL なのか、集計済みの CSV なのか、PDF なのか。
CSV が返るなら XBRL の構文解析そのものが要らなくなるので、実装量が桁で変わる。

**2. 値がどの要素名・どの文脈に入っているのか。** 連結と個別、当期と前期は
別の行として同居する。取り違えても数字は出るので、目で見て確かめる必要がある。

書類本体はファイルに保存する。中身は公開情報（有価証券報告書）なので、そのまま
貼って構わない。APIキーは指紋しか表示しない。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import pathlib
import sys
import zipfile
from typing import Any

#: ``documents/{docID}`` の ``type``。名前は EDINET の説明に合わせている。
#: 何が実際に返るかはここでは決めつけない――それを観測するのがこのプローブ。
DOCUMENT_TYPES = {
    "1": "提出本文書及び監査報告書",
    "2": "PDF",
    "3": "代替書面・添付文書",
    "4": "英文ファイル",
    "5": "CSV",
}

_DOCUMENTS_URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
_DOCUMENT_URL = "https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}"

#: 有価証券報告書。四半期・半期ではなく通期の本体。
ANNUAL_REPORT = "120"


def _fingerprint(value: str) -> str:
    """秘密そのものではなく、同一性だけを比べられる短い指紋を返す。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _auth(secret: str) -> tuple[dict[str, str], dict[str, str]]:
    """実証済みの鍵の渡し方（クエリ + Ocp-Apim ヘッダ）。"""
    return {"Subscription-Key": secret}, {"Ocp-Apim-Subscription-Key": secret}


def find_annual_report(
    client: Any, secret: str, sec_code: str, *, days: int, today: dt.date
) -> dict[str, Any] | None:
    """``sec_code`` の有価証券報告書を、直近 ``days`` 日から1件探す。

    EDINET は日付で引く API で、会社では引けない。有報は年1回なので、遡る
    範囲を広く取らないと見つからない。
    """
    params, headers = _auth(secret)
    for back in range(days):
        day = today - dt.timedelta(days=back)
        query = {**params, "date": day.isoformat(), "type": "2"}
        response = client.get(_DOCUMENTS_URL, params=query, headers=headers)
        if response.status_code != 200:
            print(f"  {day}: HTTP {response.status_code}")
            continue
        for record in response.json().get("results") or []:
            code = str(record.get("secCode") or "")
            if code.startswith(sec_code) and record.get("docTypeCode") == ANNUAL_REPORT:
                print(f"  {day}: 見つかりました")
                return record
    return None


def describe(body: bytes, out: pathlib.Path) -> None:
    """受け取ったものが何なのかを、宣言ではなく中身から言う。"""
    out.write_bytes(body)
    print(f"    保存: {out}  ({len(body):,} bytes)")

    if body[:2] == b"PK":
        with zipfile.ZipFile(out) as archive:
            names = archive.namelist()
        print(f"    ZIP、{len(names)} ファイル:")
        for name in names[:25]:
            print(f"      {name}")
        if len(names) > 25:
            print(f"      … 他 {len(names) - 25} 件")
        return

    if body[:4] == b"%PDF":
        print("    PDF")
        return

    # テキストらしいなら、文字コードと先頭を見る。EDINET の CSV は UTF-16LE の
    # タブ区切りだという説明があるが、宣言を信じずに実測する。
    for name in ("utf-16", "utf-8", "cp932"):
        try:
            text = body.decode(name)
        except (UnicodeDecodeError, UnicodeError):
            continue
        head = text[:400].replace("\t", "→").replace("\r", "")
        print(f"    テキスト（{name} で読めた）先頭:")
        for line in head.splitlines()[:6]:
            print(f"      {line}")
        return
    print(f"    不明な形式。先頭16バイト: {body[:16]!r}")


def probe(sec_code: str, days: int, out_dir: pathlib.Path) -> int:
    """有報を1件見つけ、``type`` を順に試して何が返るかを報告する。"""
    import httpx

    secret = (os.environ.get("EDINET_API_KEY") or "").strip()
    if not secret:
        sys.exit("環境変数 EDINET_API_KEY が空です。.env を確認してください。")

    print(f"APIキー 指紋: {_fingerprint(secret)}  (値そのものは表示しません)")
    print(f"対象: 証券コード {sec_code} の有価証券報告書を直近 {days} 日から探します\n")

    out_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120.0, follow_redirects=False) as client:
        record = find_annual_report(client, secret, sec_code, days=days, today=dt.date.today())
        if record is None:
            print(f"\n直近 {days} 日に {sec_code} の有報はありませんでした。")
            print("--days を伸ばすか、別の銘柄を指定してください（有報は年1回です）。")
            return 1

        doc_id = str(record.get("docID"))
        print("\n--- 見つかった書類 ---")
        for field in ("docID", "secCode", "filerName", "docDescription", "submitDateTime"):
            print(f"  {field}: {record.get(field)!r}")

        params, headers = _auth(secret)
        print("\n--- type ごとに何が返るか ---")
        for type_id, label in DOCUMENT_TYPES.items():
            query = {**params, "type": type_id}
            try:
                response = client.get(
                    _DOCUMENT_URL.format(doc_id=doc_id), params=query, headers=headers
                )
            except Exception as exc:  # noqa: BLE001 - 到達性そのものを観測している
                print(f"\n  type={type_id} ({label}): 接続できません: {exc}")
                continue

            kind = response.headers.get("content-type", "(なし)")
            print(f"\n  type={type_id} ({label}): HTTP {response.status_code}  {kind}")
            if response.status_code != 200:
                print(f"    本文: {response.text[:200]}")
                continue
            describe(response.content, out_dir / f"{doc_id}_type{type_id}.bin")

    print(f"\n{out_dir} の中身を貼ってください。")
    print("有価証券報告書は公開情報なので、そのままで構いません。")
    return 0


def main() -> int:
    """コマンドラインから実行する。"""
    parser = argparse.ArgumentParser(description="EDINET の書類本体の形を観測する")
    parser.add_argument("--sec-code", default="6501", help="証券コード（既定: 6501 日立）")
    parser.add_argument("--days", type=int, default=400, help="遡る日数。有報は年1回なので広めに")
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("edinet_probe"))
    args = parser.parse_args()
    return probe(args.sec_code, args.days, args.out)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    raise SystemExit(main())
