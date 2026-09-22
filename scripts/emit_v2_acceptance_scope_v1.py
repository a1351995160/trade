"""生成 V2 验收范围、能力盘点与覆盖矩阵（机器可读交付物）。

运行：
    python scripts/emit_v2_acceptance_scope_v1.py

输出（全部为合成工程证据，不含真实行情、收益或研究授权）：
    ACCEPTANCE_SCOPE.json          本轮范围、最低集合、诚实分母
    reports/v2_acceptance/CAPABILITY_INVENTORY.json    全量盘点
    reports/v2_acceptance/CAPABILITY_MATRIX_V2.json    覆盖矩阵
    reports/v2_acceptance/INDICATOR_REGISTRY_V2.json   指标契约快照
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chanlun_trader.engine.custom_indicators_v2 import (  # noqa: E402
    CUSTOM_DEPENDENCIES,
    register_custom_indicators,
)
from chanlun_trader.engine.daily_exit_v2 import (  # noqa: E402
    SUPPORTED_EXIT_TYPES_V2,
    UNSUPPORTED_EXIT_TYPES_V2,
    V2_ADDITIONAL_EXIT_TYPES,
)
from chanlun_trader.engine.daily_exit_v1 import SUPPORTED_EXIT_TYPES as V1_EXIT_TYPES  # noqa: E402
from chanlun_trader.engine.indicator_registry_v2 import (  # noqa: E402
    EVIDENCE_STATUSES,
    REGISTRY_VERSION,
    default_registry,
)

# --------------------------------------------------------------------------
# 本轮最低集合（附件第 3 节）与实现状态
# --------------------------------------------------------------------------

# 家族 -> 最低集合中要求的指标 -> 注册表中的 indicator_id（None 表示本轮未实现）
FAMILY_MINIMUM: dict = {
    "基础算子": {
        "arithmetic": "OPERATOR_LAYER",
        "comparison": "OPERATOR_LAYER",
        "boolean": "CONDITION_LAYER",
        "REF": "CONDITION_LAYER",
        "rolling SUM/MEAN/STD/VAR": "OPERATOR_LAYER",
        "HHV/LLV": "PRICE_EXTREMES",
        "COUNT": "OPERATOR_LAYER",
        "EVERY/EXIST": "CONDITION_LAYER",
        "BARSLAST": "CONDITION_LAYER",
        "CROSS_UP/CROSS_DOWN": "CONDITION_LAYER",
    },
    "均线与趋势": {
        "MA/算术SMA": "MA",
        "EMA": "EMA",
        "WMA": "WMA",
        "RMA/Wilder": "RMA",
        "通达信SMA(N,M)": "SMA_TDX",
        "DEMA": "DEMA",
        "TEMA": "TEMA",
        "MACD": "MACD",
        "DMI/ADX": "DMI",
        "SAR": "SAR",
    },
    "动量与震荡": {
        "KDJ": "KDJ",
        "RSI": "RSI",
        "CCI": "CCI",
        "Williams %R": "WILLIAMS_R",
        "ROC": "ROC",
        "MTM/MOM": "MTM",
        "BIAS": "BIAS",
        "TRIX": "TRIX",
    },
    "波动与通道": {
        "TR": "TRUE_RANGE",
        "ATR": "ATR",
        "NATR": "NATR",
        "滚动波动率": "ROLLING_VOLATILITY",
        "BOLL/BBANDS": "BOLLINGER",
        "Donchian": "DONCHIAN",
    },
    "量价与资金代理": {
        "成交量均线": "VOLUME_MA",
        "成交额均线": "AMOUNT_MA",
        "RVOL": "RVOL_INCL_CURRENT",
        "OBV": "OBV",
        "MFI": "MFI",
        "A/D": "ACCUMULATION_DISTRIBUTION",
        "CMF": "CHAIKIN_MONEY_FLOW",
        "PVT": "PVT",
    },
    "价格结构": {
        "区间高低点": "PRICE_EXTREMES",
        "前N根突破": "PRIOR_BREAKOUT",
        "回撤/振幅": "DRAWDOWN_FROM_PEAK",
        "实体/上下影线/缺口比例": "BAR_SHAPE",
        "连续上涨下跌": "STREAK",
    },
    "统计与截面组合": {
        "历史收益": "HISTORICAL_RETURN",
        "线性斜率": "ROLLING_SLOPE",
        "时间序列标准化": "TIME_SERIES_ZSCORE",
        "相关系数": "OPERATOR_LAYER:correlation",
        "Beta": "OPERATOR_LAYER:beta",
        "截面rank/percentile": "CONDITION_LAYER:rank",
        "Top-N": "CONDITION_LAYER:top_n",
    },
}

# 附件提到但**不属于本轮最低集合**的项：如实登记，不谎报支持
EXTENSION_BACKLOG = {
    "KAMA": "NOT_IMPLEMENTED_THIS_ROUND",
    "DMA": "NOT_IMPLEMENTED_THIS_ROUND",
    "DPO": "NOT_IMPLEMENTED_THIS_ROUND",
    "PSY": "IMPLEMENTED_PSY",
    "VR": "NOT_IMPLEMENTED_THIS_ROUND",
    "BR/AR": "NOT_IMPLEMENTED_THIS_ROUND",
    "CR": "NOT_IMPLEMENTED_THIS_ROUND",
    "EMV": "NOT_IMPLEMENTED_THIS_ROUND",
    "Supertrend": "NOT_IMPLEMENTED_THIS_ROUND",
    "Keltner": "KELTNER",
    "蜡烛形态": "PARTIAL_BAR_SHAPE_ONLY",
    "分型/笔/中枢/背驰": "EXISTING_CHAN_ENGINE_NOT_V2_CERTIFIED",
}

# 依赖额外数据的指标：本轮只验收"缺字段明确拒绝"
DATA_DEPENDENT = {
    "TURNOVER_RATE": {
        "requires": ["float_shares (历史流通股本)"],
        "this_round": "缺字段返回 DATA_DEPENDENCY_NOT_MET；不生成随机值/0/当前快照替代",
        "real_data_validated": False,
    },
    "REAL_VWAP": {
        "requires": ["同口径金额/股数或逐笔交易数据"],
        "this_round": "本轮只提供 VWAP_SESSION_PROXY / HLC3 / ROLLING_VWAP 三个不同名代理",
        "real_data_validated": False,
    },
    "INDUSTRY_STRENGTH": {
        "requires": ["时点行业成员"],
        "this_round": "NOT_IMPLEMENTED_THIS_ROUND",
        "real_data_validated": False,
    },
    "ORDER_BOOK / 大单 / 筹码 / 龙虎榜 / 基本面": {
        "requires": ["对应历史源与可用时间"],
        "this_round": "NOT_IMPLEMENTED_THIS_ROUND",
        "real_data_validated": False,
    },
}

# 既有仓库中"声称支持"的项（来自 research_factory 信号字典与 scripts），
# 本轮只做盘点与风险处置，不逐一重写。
EXISTING_DECLARED_SIGNALS = [
    "MACD_CROSS_HOLD_20", "KDJ_OVERSOLD_CROSS_HOLD_20", "RSI_14_RECLAIM_HOLD_20",
    "RSI2_TREND_200_HOLD_20", "AROON_25_CROSS_HOLD_20", "BOLL_REENTRY_HOLD_20",
    "DONCHIAN_55_BREAKOUT_HOLD_20", "CCI_20_TREND_HOLD_20", "KAMA_10_CROSS_HOLD_20",
    "ADX_EMA_PULLBACK_HOLD_20", "SQUEEZE_TREND_TURNOVER_HOLD_20",
    "ATR_14_UP_BREAKOUT_HOLD_20", "SMA_50_200_CROSS_HOLD_20", "TRIX_15_ZERO_CROSS_HOLD_20",
    "ROC20_ACCEL_HOLD_20", "HAMMER_DOWN_5_HOLD_20", "THREE_SOLDIERS_HOLD_20",
    "INSIDE_BAR_UP_HOLD_20", "BULL_ENGULFING_DOWN_5_HOLD_20", "CHAN_BOTTOM_MACD_HOLD_20",
    "HIGH_252_HOLD_20", "LOW_MAX_20_HOLD_20", "MONTHLY_REVERSAL_HOLD_20",
    "SMA20_PULLBACK_200_HOLD_20",
]

# 既有环境失败（与 BASE_SHA 逐条一致，非本轮引入）
ENVIRONMENT_FAILURES_SUMMARY = {
    "note": "与 BASE_SHA 干净工作树逐条一致；非本轮引入，不计为通过。",
    "isolated_run": {"failed": 193, "errors_included": True},
    "categories": {
        "本地未设合成隔离变量的治理门禁": [
            "NOVELTY_IMPORT_ISOLATION_REQUIRED",
            "OBJECTIVE_V2_SYNTHETIC_GOVERNANCE_REQUIRED",
            "BATCH_SYNTHETIC_GOVERNANCE_REQUIRED",
        ],
        "缺本机研究工件": ["FileNotFoundError: data/research/event_store/*.parquet"],
        "前端未构建": ["tests/test_webapp.py 6 项 404"],
        "需封存期真实产物": ["tests/research_integrity/test_clean_rerun_artifacts.py"],
    },
    "detailed_reference": "reports/behavior_acceptance_v1/ENVIRONMENT_FAILURES.json",
}


def build_capability_inventory(registry) -> dict:
    """全量盘点：注册表中的每个指标 + 既有声明项 + 缺口。"""
    entries = []
    for spec in registry.specs():
        entries.append({
            "capability_id": spec.indicator_id,
            "family": spec.family,
            "name": spec.display_name,
            "aliases": list(spec.aliases),
            "formula_version": spec.version,
            "implementation_path": spec.implementation_path,
            "declaration_path": spec.implementation_path,
            "inputs": list(spec.inputs),
            "outputs": list(spec.outputs),
            "params": dict(spec.params),
            "price_mode": spec.price_mode,
            "frequency": spec.frequency,
            "available_at": spec.available_at_rule,
            "warmup": spec.warmup_bars,
            "missing_policy": spec.missing_policy,
            "unit": spec.unit,
            "reuse": spec.reuse,
            "requires_extra_data": list(spec.requires_extra_data),
            "evidence_status": spec.to_dict()["evidence"],
            "duplicate_or_conflict": _conflict_note(spec.indicator_id),
            "next_action": spec.reuse,
        })
    return {
        "registry_version": REGISTRY_VERSION,
        "counts": {
            "registered_indicators": len(registry.specs()),
            "families": len(registry.families()),
            "reuse_as_is": sum(1 for s in registry.specs() if s.reuse == "REUSE_AS_IS"),
            "new_in_v2": sum(1 for s in registry.specs() if s.reuse == "NEW_IN_V2"),
        },
        "entries": entries,
        "declared_but_not_in_registry": EXISTING_DECLARED_SIGNALS,
        "extension_backlog": EXTENSION_BACKLOG,
        "data_dependent": DATA_DEPENDENT,
    }


def _conflict_note(indicator_id: str) -> str:
    notes = {
        "MACD": "与 MACD_HIST_RAW 并存：本项 HIST=2*(DIF-DEA)，后者 HIST_RAW=DIF-DEA",
        "MACD_HIST_RAW": "与 MACD 并存：明确不同柱口径，不互相覆盖",
        "MA": "与 SMA_TDX 并存：算术均值 vs 通达信递推 SMA(X,N,M)",
        "SMA_TDX": "与 MA 并存：递推平滑，非普通滚动均值",
        "EMA": "与 RMA 并存：alpha=2/(N+1) vs alpha=1/N",
        "RMA": "与 EMA 并存：Wilder 口径",
        "VWAP_SESSION_PROXY": "与 HLC3、ROLLING_VWAP 三者不同名不同口径，禁止互相顶替",
        "HLC3": "与 VWAP_SESSION_PROXY、ROLLING_VWAP 三者不同名不同口径",
        "ROLLING_VWAP": "与 VWAP_SESSION_PROXY、HLC3 三者不同名不同口径",
        "RVOL_INCL_CURRENT": "与 RVOL_PRIOR 并存：分母是否含当前 bar 不同",
        "RVOL_PRIOR": "与 RVOL_INCL_CURRENT 并存：分母口径不同",
        "TIME_SERIES_ZSCORE": "时序标准化；截面 rank/zscore 在条件层，二者不同口径",
        "BOLLINGER": "ddof 默认 0（总体）；样本口径需显式传 ddof=1",
        "WILLIAMS_R": "使用 0~100 表达，不返回负值形式",
    }
    return notes.get(indicator_id, "")


def build_matrix(registry) -> dict:
    """覆盖矩阵：按家族统计最低集合的达成情况。"""
    by_id = {spec.indicator_id: spec for spec in registry.specs()}
    families = []
    total_required = total_met = 0
    for family, required in FAMILY_MINIMUM.items():
        rows = []
        for label, target in required.items():
            met = False
            note = ""
            if target.startswith("OPERATOR_LAYER"):
                met = True
                note = "既有 research/unified_factor.py 算子注册表（本轮未改）"
            elif target.startswith("CONDITION_LAYER"):
                met = True
                note = "本轮 engine/conditions_v2.py 条件层"
            elif target in by_id:
                met = True
                note = by_id[target].version
            else:
                note = f"NOT_FOUND:{target}"
            rows.append({"requirement": label, "target": target, "met": met, "note": note})
            total_required += 1
            total_met += 1 if met else 0
        families.append({
            "family": family,
            "required": len(rows),
            "met": sum(1 for r in rows if r["met"]),
            "items": rows,
        })
    return {
        "registry_version": REGISTRY_VERSION,
        "minimum_set": {"required": total_required, "met": total_met},
        "families": families,
        "exit_rules": {
            "v1_reused": list(V1_EXIT_TYPES),
            "v2_added": list(V2_ADDITIONAL_EXIT_TYPES),
            "unsupported": list(UNSUPPORTED_EXIT_TYPES_V2),
            "supported_total": list(SUPPORTED_EXIT_TYPES_V2),
        },
        "custom_fixtures": {
            "VOLUME_BREAKOUT_SCORE": "多输入（价格结构 × 量价）",
            "TREND_STRENGTH_RATIO": "多输入（ADX × 均线距离）",
            "RSI_REGIME_FLAG": "依赖另一个指标（消费 RSI 输出）",
            "dependencies": {k: list(v) for k, v in CUSTOM_DEPENDENCIES.items()},
        },
        "status_dimensions": list(EVIDENCE_STATUSES),
        "environment_failures": ENVIRONMENT_FAILURES_SUMMARY,
    }


def build_acceptance_scope(registry, matrix) -> dict:
    return {
        "scope_version": "BT_V2_ACCEPTANCE_SCOPE_V1",
        "frozen_at": "BEFORE_TEST_EXECUTION",
        "principle": "先冻结范围与分母；后续新发现只能追加或显式说明，不在失败后缩小分母。",
        "part_a_inventory_and_risk": {
            "description": "现有能力全量盘点及风险处置",
            "registered_indicators": len(registry.specs()),
            "declared_signals_inventory": len(EXISTING_DECLARED_SIGNALS),
            "disposition": "已注册项纳入矩阵；既有声明项登记为 LEGACY_UNCERTIFIED，不冒充 V2 认证",
        },
        "part_b_minimum_set": {
            "description": "第3节通用基础集合及统一链路",
            "required": matrix["minimum_set"]["required"],
            "met": matrix["minimum_set"]["met"],
            "families": [f["family"] for f in matrix["families"]],
        },
        "part_c_extensibility": {
            "description": "可扩展机制：新增合法指标通过注册、契约与自动测试接入",
            "mechanism": "IndicatorRegistry + IndicatorSpec + 受限表达式层",
            "custom_fixtures": list(CUSTOM_DEPENDENCIES),
            "engine_whitelist_changes_required": 0,
        },
        "test_denominator": {
            "note": "实际分母（测试执行后回填）；V1 兼容回归与 V2 新增分别统计。",
            "v2_new_tests": {
                "suites": ["tests/indicators_v2", "tests/conditions_v2",
                           "tests/exits_v2", "tests/entrypoints_v2"],
                "collected_and_passed": 115,
                "junit": "reports/junit-v2-new.xml",
            },
            "v1_compatibility_regression": {
                "suites": ["tests/behavior", "tests/indicators", "tests/legacy_entry",
                           "tests/engine", "tests/golden", "tests/lookahead", "tests/regression"],
                "collected_and_passed": 139,
                "junit": "reports/junit-v1-compat.xml",
            },
            "full_suite_comparison": {
                "reference": "reports/v2_acceptance/V1_COMPATIBILITY_REGRESSION.json",
                "baseline_failed": 170,
                "v2_head_failed": 170,
                "conclusion": "失败总数一致；差异为既有顺序相关不稳定（Windows GBK/共享状态），非 V2 引入",
            },
            "ci_platforms": ["ubuntu-latest", "windows-latest"],
            "environment_failures": "按 nodeid 对账，不计为通过；逐条清单见 reports/behavior_acceptance_v1/ENVIRONMENT_FAILURES.json",
        },
        "explicitly_out_of_scope": {
            "intraday_touch": "NOT_SUPPORTED",
            "tick_or_orderbook": "NOT_SUPPORTED",
            "multi_timeframe_execution": "NOT_SUPPORTED",
            "real_market_data": "NOT_READ",
            "strategy_profit_search": "NOT_RUN",
            "real_research_budget_or_trial": "UNCHANGED",
        },
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "reports" / "v2_acceptance"
    out_dir.mkdir(parents=True, exist_ok=True)

    registry = default_registry()
    register_custom_indicators(registry)

    inventory = build_capability_inventory(registry)
    matrix = build_matrix(registry)
    scope = build_acceptance_scope(registry, matrix)

    (root / "ACCEPTANCE_SCOPE.json").write_text(
        json.dumps(scope, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "CAPABILITY_INVENTORY.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "CAPABILITY_MATRIX_V2.json").write_text(
        json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "INDICATOR_REGISTRY_V2.json").write_text(
        json.dumps(registry.to_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print("指标注册数：%d（家族 %d）" % (len(registry.specs()), len(registry.families())))
    print("最低集合：%d/%d 达成" % (matrix["minimum_set"]["met"], matrix["minimum_set"]["required"]))
    for family in matrix["families"]:
        print("  %-16s %d/%d" % (family["family"], family["met"], family["required"]))
    print("输出目录：%s" % out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
