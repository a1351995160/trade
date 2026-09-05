"""受保护的历史 5m capability / Stage 2 symbol manifests。

这些清单是下载验证范围，不是 2022 年完整证券 universe 的声明；
正式 PIT security master 仍由覆盖报告单独标记为 PARTIAL。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stage2UniverseMember:
    symbol: str
    board: str
    listing_vintage: str
    selection_bucket: str


STAGE1_SYMBOLS = ("600519.SH", "600036.SH", "000001.SZ", "000002.SZ")

# 50 个额外标的：沪主板 20、深主板/原中小板 15、创业板 10、科创板 5。
# 清单在采集前固定，不依据本轮 5m 结果筛选或优化。
STAGE2_UNIVERSE = (
    *(Stage2UniverseMember(symbol, "SH_MAIN", "PRE_2010", "LIQUIDITY_ANCHOR") for symbol in (
        "600000.SH", "600016.SH", "600030.SH", "600048.SH", "600104.SH",
        "600276.SH", "600309.SH", "600585.SH", "600690.SH", "600887.SH",
        "600900.SH", "601166.SH", "601318.SH", "601328.SH", "601398.SH",
        "601888.SH", "601988.SH", "600009.SH", "600028.SH",
    )),
    Stage2UniverseMember("601288.SH", "SH_MAIN", "2010_2015", "LIQUIDITY_ANCHOR"),
    *(Stage2UniverseMember(symbol, "SZ_MAIN", "MIXED_PRE_2022", "DIVERSIFIER") for symbol in (
        "000333.SZ", "000338.SZ", "000538.SZ", "000568.SZ", "000625.SZ",
        "000651.SZ", "000661.SZ", "000725.SZ", "000895.SZ", "002027.SZ",
        "002415.SZ", "002475.SZ", "002594.SZ", "002714.SZ", "000977.SZ",
    )),
    *(Stage2UniverseMember(symbol, "GEM", "MIXED_PRE_2022", "DIVERSIFIER") for symbol in (
        "300015.SZ", "300059.SZ", "300122.SZ", "300124.SZ", "300274.SZ",
        "300347.SZ", "300408.SZ", "300750.SZ", "300782.SZ", "300896.SZ",
    )),
    *(Stage2UniverseMember(symbol, "STAR", "POST_2019", "DIVERSIFIER") for symbol in (
        "688001.SH", "688005.SH", "688009.SH", "688012.SH", "688599.SH",
    )),
)


def stage2_symbols() -> list[str]:
    return [member.symbol for member in STAGE2_UNIVERSE]


def stage2_manifest() -> list[dict[str, str]]:
    return [member.__dict__.copy() for member in STAGE2_UNIVERSE]


if len(STAGE2_UNIVERSE) != 50 or len(stage2_symbols()) != len(set(stage2_symbols())):
    raise RuntimeError("Stage 2 universe must contain 50 unique symbols")
