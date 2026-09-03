"""立花の銘柄マスタから、上場銘柄のユニバースを組み立てる。

**J-Quants を解約したときに、ここが埋まっていないと銘柄一覧が更新できなくなる。**
`universe.py` と `jquants_profile.py` はどちらも J-Quants の
``equities/master`` を直接叩いており、``JP_PRICE_SOURCE`` も
``JP_STATEMENT_SOURCE`` もそこには効かなかった（`docs/JQUANTS_EXIT.md`）。

実測（2026-09-03、本番、v4r10）で確認したこと。

- 銘柄マスタ・銘柄市場マスタとも **4,441 件**、銘柄コードで1対1に対応する
- **上場区分** は 01プライム 1,555 / 02スタンダード 1,555 / 09グロース 596。
  東証の公表値とほぼ一致する
- **業種コード** は東証33業種で、空は 4,441 件中 1 件だけ。J-Quants と同じ
  4桁コードなので `sectors.from_tse33` がそのまま使える
- **上場廃止日は使えない。** 値が入っているのは 23 件だけで、廃止済み銘柄が
  残っているのではなく、現存銘柄に廃止予定日が入っているだけである。
  **生存バイアスは是正できない**（事前登録の「既知の制約」はそのまま残る）
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from stock_ai.core.exceptions import DataError
from stock_ai.data.sectors import Sector, from_tse33
from stock_ai.data.types import SecurityProfile
from stock_ai.data.universe import Segment

logger = logging.getLogger(__name__)

#: 上場区分（``sZyouzyouKubun``）から市場区分への対応。
#:
#: 外国銘柄（03/04/11）も本則の区分に寄せる。実測では5件しかないが、
#: **落とすと「東証に上場しているのに universe に出てこない」銘柄ができる**。
#: 数が少ないことは、黙って消してよい理由にならない。
_SEGMENT_OF_KUBUN: dict[str, Segment] = {
    "01": Segment.PRIME,
    "03": Segment.PRIME,  # プライム（外国銘柄）
    "02": Segment.STANDARD,
    "04": Segment.STANDARD,  # スタンダード（外国銘柄）
    "09": Segment.GROWTH,
    "11": Segment.GROWTH,  # グロース（外国銘柄）
}

#: 東証33業種の「その他」。ETF・REIT などがここに落ちる。
_UNCLASSIFIED = "9999"


class MasterFetcher(Protocol):
    """銘柄マスタと銘柄市場マスタを返すもの。テストで差し替える。"""

    def __call__(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """``(銘柄マスタ, 銘柄市場マスタ)`` を返す。"""
        ...


def _text(record: dict[str, Any], key: str) -> str | None:
    """空白だけの値を ``None`` に潰して取り出す。

    立花は「値が無い」を空文字ではなく**半角スペース**で返すことがある
    （``sBaibaiTeisiC`` が実測で 4,255 件そうだった）。``str`` として真だから
    という理由で通すと、空白を意味のある区分値として扱ってしまう。
    """
    value = record.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _code_of(record: dict[str, Any]) -> str | None:
    """銘柄コード。4桁でないものは通さない。"""
    code = _text(record, "sIssueCode")
    if code is None or len(code) != 4 or not code.isalnum():
        return None
    return code


def normalize_masters(
    kabu: list[dict[str, Any]],
    sizyou: list[dict[str, Any]],
    segment: Segment = Segment.ALL,
) -> list[SecurityProfile]:
    """2つのマスタを突き合わせて、1つの区分ぶんのプロファイルにする。

    **市場区分は銘柄市場マスタ側にしか無く、業種は銘柄マスタ側にしか無い。**
    片方だけでは区分で絞れないので、銘柄コードで結合する。

    ``Segment.ALL`` は「東証の3区分すべて」であって「マスタの全レコード」では
    ない。ETF・REIT・TPM は上場区分が 00 や 21 で返るので、ここで落ちる。
    J-Quants 側の ``normalize_listings`` がファンドを落とすのと同じ結果になる。
    """
    by_code: dict[str, dict[str, Any]] = {}
    bad_code_in_kabu = 0
    for row in kabu:
        code = _code_of(row)
        if code is None:
            bad_code_in_kabu += 1
            continue
        by_code[code] = row

    profiles: dict[str, SecurityProfile] = {}
    bad_code = 0
    unlisted = 0
    unmatched = 0
    unclassified = 0
    duplicate = 0

    for row in sizyou:
        code = _code_of(row)
        if code is None:
            # **黙って落とさない。** 実測（2026-09-03）でプライムが見込みより
            # 7件少なく、どこで落ちたのかを推測するしかない状態になった。
            # 落ちる経路すべてに数え口を置く。
            bad_code += 1
            continue
        kubun = _text(row, "sZyouzyouKubun")
        listed_on = _SEGMENT_OF_KUBUN.get(kubun or "")
        if listed_on is None:
            unlisted += 1
            continue
        if segment is not Segment.ALL and listed_on is not segment:
            continue
        detail = by_code.get(code)
        if detail is None:
            # 市場マスタにあって銘柄マスタに無い。名前も業種も取れないので
            # 通さない。**推測で埋めるより、落ちた件数を数えるほうがよい。**
            unmatched += 1
            continue

        industry = _text(detail, "sGyousyuCode")
        sector = from_tse33(industry) if industry else Sector.OTHER
        if industry is None or industry == _UNCLASSIFIED:
            unclassified += 1
        if code in profiles:
            # 同じ銘柄コードが2度来た。dict に入れると後勝ちで静かに1件消える。
            duplicate += 1

        profiles[code] = SecurityProfile(
            symbol=code,
            market="JP",
            name=_text(detail, "sIssueName") or _text(detail, "sIssueNameRyaku"),
            sector=str(sector),
            industry=industry,
        )

    if unlisted:
        logger.info(
            "上場区分が東証3区分でない %d 件を除外しました（ETF・REIT・TPM など）", unlisted
        )
    if bad_code or bad_code_in_kabu:
        odd = [str(row.get("sIssueCode", "")).strip() for row in sizyou if _code_of(row) is None]
        # 5桁は普通株でない銘柄（優先株・種類株）に割り当てられる。実測
        # （2026-09-03）で落ちた7件はすべて5桁で末尾が 5 で、先頭4桁は既存の
        # 普通株コードだった（2593 / 5076 / 7550 / 9201 / 9202 など）。
        # **これは正しく落ちている。** ユニバースは普通株だけを対象にする。
        #
        # 正しい除外を毎回 WARNING で出すと、本物の警告が見えなくなる。
        # 想定どおりの形（5桁）は INFO、それ以外は WARNING に分ける。
        unexpected = [code for code in odd if len(code) != 5]
        if unexpected:
            logger.warning(
                "銘柄コードの桁数が想定外の %d 件を除外しました。例: %s。"
                "普通株でない銘柄は5桁で来るので、これはそれとも違う形である。",
                len(unexpected),
                unexpected[:5],
            )
        preferred = len(odd) - len(unexpected)
        if preferred or bad_code_in_kabu:
            logger.info(
                "普通株でない銘柄（優先株・種類株、5桁コード）%d 件を除外しました"
                "（銘柄マスタ側 %d 件）。例: %s",
                preferred,
                bad_code_in_kabu,
                [code for code in odd if len(code) == 5][:5] or "(なし)",
            )
    if unmatched:
        logger.warning("銘柄マスタに対応が無い %d 件を除外しました", unmatched)
    if duplicate:
        logger.warning("同じ銘柄コードが %d 件重複していました（後勝ちで1件に）", duplicate)
    if unclassified:
        logger.info("業種コードが無い、または 9999 の %d 件を Other にしました", unclassified)
    if sizyou and not profiles:
        # レコードは来たのに1件も残らなかった。区分が本当に空であるより、
        # 項目名が変わったことのほうがはるかに多い。**どの項目が来ていたかを
        # 出せば、黙って空のユニバースになるのを1行の修正に変えられる。**
        logger.warning(
            "立花は %d 件返しましたが、区分 %s で1件も残りませんでした。先頭レコードの項目名: %s",
            len(sizyou),
            segment.value,
            sorted(sizyou[0]) if isinstance(sizyou[0], dict) else "(オブジェクトではない)",
        )
    return [profiles[code] for code in sorted(profiles)]


class TachibanaUniverse:
    """立花の銘柄マスタから上場銘柄を取る。``JQuantsUniverse`` と同じ形。"""

    name = "tachibana"

    def __init__(self, fetcher: MasterFetcher) -> None:
        """マスタの取得元を受け取る。

        Args:
            fetcher: ``(銘柄マスタ, 銘柄市場マスタ)`` を返すもの。
        """
        self._fetch = fetcher

    def profiles(
        self, segment: Segment = Segment.PRIME, limit: int | None = None
    ) -> list[SecurityProfile]:
        """その区分の上場銘柄を、コード順に返す。

        Raises:
            DataError: マスタが1件も返らなかったとき。
        """
        kabu, sizyou = self._fetch()
        if not sizyou:
            raise DataError("立花が銘柄市場マスタを1件も返しませんでした。")

        found = normalize_masters(kabu, sizyou, segment)
        logger.info("ユニバース: %s に %d 件", segment.value, len(found))
        return found[:limit] if limit else found
