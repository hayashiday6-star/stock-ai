"""One sector taxonomy spanning both markets.

US quotes arrive with GICS-style sector names; Japanese listings carry a
TOPIX-17 / TSE-33 code instead. A portfolio holding both cannot be broken down
by sector until those land in the same vocabulary, so this module defines the
canonical set and the mappings into it.

The mapping is deliberately coarse and unavoidably lossy - TOPIX-17 bundles
telecoms with "services, other", and 電機・精密 spans names a GICS analyst
would split between Technology and Industrials. It is good enough to answer
"how concentrated am I", which is what the portfolio breakdown asks, and is not
meant to be a substitute for a real GICS licence. Anything unrecognised becomes
:data:`Sector.OTHER` rather than being guessed at.
"""

from __future__ import annotations

from enum import StrEnum


class Sector(StrEnum):
    """Canonical sector buckets, modelled on the GICS top level."""

    TECHNOLOGY = "Technology"
    FINANCIALS = "Financials"
    HEALTHCARE = "Healthcare"
    CONSUMER_CYCLICAL = "Consumer Cyclical"
    CONSUMER_DEFENSIVE = "Consumer Defensive"
    INDUSTRIALS = "Industrials"
    ENERGY = "Energy"
    MATERIALS = "Materials"
    REAL_ESTATE = "Real Estate"
    UTILITIES = "Utilities"
    COMMUNICATION = "Communication Services"
    OTHER = "Other"


# yfinance reports GICS-ish names with its own spellings.
_YFINANCE_SECTORS: dict[str, Sector] = {
    "technology": Sector.TECHNOLOGY,
    "financial services": Sector.FINANCIALS,
    "financial": Sector.FINANCIALS,
    "healthcare": Sector.HEALTHCARE,
    "health care": Sector.HEALTHCARE,
    "consumer cyclical": Sector.CONSUMER_CYCLICAL,
    "consumer discretionary": Sector.CONSUMER_CYCLICAL,
    "consumer defensive": Sector.CONSUMER_DEFENSIVE,
    "consumer staples": Sector.CONSUMER_DEFENSIVE,
    "industrials": Sector.INDUSTRIALS,
    "industrial goods": Sector.INDUSTRIALS,
    "energy": Sector.ENERGY,
    "basic materials": Sector.MATERIALS,
    "materials": Sector.MATERIALS,
    "real estate": Sector.REAL_ESTATE,
    "utilities": Sector.UTILITIES,
    "communication services": Sector.COMMUNICATION,
    "communications": Sector.COMMUNICATION,
}

# TOPIX-17 code -> canonical bucket. The Japanese label is kept in the comment
# because the code alone is unreadable when auditing this table.
_TOPIX17_SECTORS: dict[str, Sector] = {
    "1": Sector.CONSUMER_DEFENSIVE,  # 食品
    "2": Sector.ENERGY,  # エネルギー資源
    "3": Sector.INDUSTRIALS,  # 建設・資材
    "4": Sector.MATERIALS,  # 素材・化学
    "5": Sector.HEALTHCARE,  # 医薬品
    "6": Sector.CONSUMER_CYCLICAL,  # 自動車・輸送機
    "7": Sector.MATERIALS,  # 鉄鋼・非鉄
    "8": Sector.INDUSTRIALS,  # 機械
    "9": Sector.TECHNOLOGY,  # 電機・精密
    "10": Sector.TECHNOLOGY,  # 情報通信・サービスその他 (telecoms land here too)
    "11": Sector.UTILITIES,  # 電気・ガス
    "12": Sector.INDUSTRIALS,  # 運輸・物流
    "13": Sector.INDUSTRIALS,  # 商社・卸売
    "14": Sector.CONSUMER_CYCLICAL,  # 小売
    "15": Sector.FINANCIALS,  # 銀行
    "16": Sector.FINANCIALS,  # 金融(除く銀行)
    "17": Sector.REAL_ESTATE,  # 不動産
    "18": Sector.OTHER,  # その他
}

# TSE-33 codes are grouped by their leading range; the full table is long and
# the ranges are stable, so the coarse mapping is expressed as spans.
_TSE33_SECTORS: dict[str, Sector] = {
    "0050": Sector.MATERIALS,  # 水産・農林業
    "1050": Sector.ENERGY,  # 鉱業
    "2050": Sector.INDUSTRIALS,  # 建設業
    "3050": Sector.CONSUMER_DEFENSIVE,  # 食料品
    "3100": Sector.CONSUMER_CYCLICAL,  # 繊維製品
    "3150": Sector.MATERIALS,  # パルプ・紙
    "3200": Sector.MATERIALS,  # 化学
    "3250": Sector.HEALTHCARE,  # 医薬品
    "3300": Sector.ENERGY,  # 石油・石炭製品
    "3350": Sector.MATERIALS,  # ゴム製品
    "3400": Sector.MATERIALS,  # ガラス・土石製品
    "3450": Sector.MATERIALS,  # 鉄鋼
    "3500": Sector.MATERIALS,  # 非鉄金属
    "3550": Sector.MATERIALS,  # 金属製品
    "3600": Sector.INDUSTRIALS,  # 機械
    "3650": Sector.TECHNOLOGY,  # 電気機器
    "3700": Sector.CONSUMER_CYCLICAL,  # 輸送用機器
    "3750": Sector.TECHNOLOGY,  # 精密機器
    "3800": Sector.INDUSTRIALS,  # その他製品
    "4050": Sector.UTILITIES,  # 電気・ガス業
    "5050": Sector.INDUSTRIALS,  # 陸運業
    "5100": Sector.INDUSTRIALS,  # 海運業
    "5150": Sector.INDUSTRIALS,  # 空運業
    "5200": Sector.INDUSTRIALS,  # 倉庫・運輸関連業
    "5250": Sector.COMMUNICATION,  # 情報・通信業
    "6050": Sector.INDUSTRIALS,  # 卸売業
    "6100": Sector.CONSUMER_CYCLICAL,  # 小売業
    "7050": Sector.FINANCIALS,  # 銀行業
    "7100": Sector.FINANCIALS,  # 証券、商品先物取引業
    "7150": Sector.FINANCIALS,  # 保険業
    "7200": Sector.FINANCIALS,  # その他金融業
    "8050": Sector.REAL_ESTATE,  # 不動産業
    "9050": Sector.COMMUNICATION,  # サービス業
}


def from_yfinance(label: str | None) -> Sector:
    """Map a yfinance ``info["sector"]`` label onto the canonical set."""
    if not label:
        return Sector.OTHER
    return _YFINANCE_SECTORS.get(label.strip().lower(), Sector.OTHER)


def from_topix17(code: str | int | None) -> Sector:
    """Map a TOPIX-17 sector code onto the canonical set."""
    if code in (None, ""):
        return Sector.OTHER
    # Codes arrive as "01" or 1 depending on the payload; normalize to "1".
    text = str(code).strip().lstrip("0") or "0"
    return _TOPIX17_SECTORS.get(text, Sector.OTHER)


def from_tse33(code: str | int | None) -> Sector:
    """Map a TSE-33 sector code onto the canonical set."""
    if code in (None, ""):
        return Sector.OTHER
    return _TSE33_SECTORS.get(str(code).strip(), Sector.OTHER)


def parse(value: str | None) -> Sector:
    """Parse a stored canonical sector name back into a :class:`Sector`."""
    if not value:
        return Sector.OTHER
    try:
        return Sector(value)
    except ValueError:
        return Sector.OTHER
