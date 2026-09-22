"""生成本轮交付证据：合成样本、独立预期值、实际逐笔记录、能力矩阵。

运行：
    python scripts/emit_behavior_acceptance_evidence_v1.py --out DIR

输出（全部为合成工程证据，不含真实行情、收益或研究授权）：
    DIR/synthetic_bars.csv          合成行情（项目实际支持的 CSV 文件入口）
    DIR/independent_expectations.json  独立手算/朴素实现的预期值
    DIR/actual_trace.json           真实引擎的逐笔订单/成交/lot/现金/equity
    DIR/CAPABILITY_MATRIX.json      逐能力状态矩阵
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chanlun_trader.engine.behavior_service_v1 import (  # noqa: E402
    BehaviorRequestV1,
    run_behavior_backtest_v1,
)
from chanlun_trader.engine.conditions_v1 import MACD_CONDITIONS, KDJ_CONDITIONS  # noqa: E402
from chanlun_trader.engine.daily_exit_v1 import (  # noqa: E402
    RESERVED_EXIT_TYPES,
    SUPPORTED_EXIT_TYPES,
)
from chanlun_trader.engine.indicators_v1 import (  # noqa: E402
    INDICATOR_CONTRACT_VERSION,
    KDJ_IMPLEMENTATION_VERSION,
    MACD_IMPLEMENTATION_VERSION,
)

from tests.behavior._fixtures import (  # noqa: E402
    CAL,
    ENTRY_INDEX,
    ENTRY_OPEN,
    FEE_CONTRACT,
    SYMBOL,
    entry_bars,
    fee,
    manual_account_case,
    slippage,
    to_dataset_csv,
)
from tests.indicators._oracle import naive_kdj, naive_macd  # noqa: E402

CONDITIONS = ["ABOVE_ZERO_GOLDEN_CROSS"]
EXIT_RULES = {
    "stop_loss_pct": 0.03,
    "take_profit_pct": 0.10,
    "trailing_activate_pct": 0.05,
    "trailing_pct": 0.08,
    "fixed_holding_sessions": 20,
}


def build_request(dataset_path=None) -> BehaviorRequestV1:
    payload = {
        "calendar": list(CAL),
        "symbols": [SYMBOL],
        "entry_conditions": CONDITIONS,
        "exit_rules": EXIT_RULES,
        "initial_cash": 100_000.0,
        "max_positions": 1,
        "max_position_weight": 1.0,
        **FEE_CONTRACT,
    }
    if dataset_path is None:
        payload["bars"] = {SYMBOL: entry_bars()}
    else:
        payload["dataset_path"] = str(dataset_path)
    return BehaviorRequestV1.from_mapping(payload)


def independent_expectations() -> dict:
    """独立手算 / 朴素递推的预期值（不使用被测函数）。"""
    bars = entry_bars()
    closes = [row["close"] for row in bars[:34]]
    highs = [row["high"] for row in bars[:34]]
    lows = [row["low"] for row in bars[:34]]
    macd = naive_macd(closes)
    # naive_macd 的段语义与实现一致；这里取信号日（index 33）的逐值预期。
    kdj = naive_kdj(highs, lows, closes)
    buy_price = slippage("BUY", ENTRY_OPEN)
    quantity = int(100_000.0 / buy_price / 100) * 100
    buy_fee = fee("BUY", quantity, buy_price)["total_fee"]
    return {
        "note": "全部为合成工程合同的独立预期值，不代表真实行情或收益。",
        "signal": {
            "condition": "ABOVE_ZERO_GOLDEN_CROSS",
            "signal_index": 33,
            "signal_date": CAL[33],
            "expected_unique": True,
            "macd_dif_at_signal": macd["dif"][33],
            "macd_dea_at_signal": macd["dea"][33],
            "kdj_k_at_signal": kdj["k"][33],
            "kdj_d_at_signal": kdj["d"][33],
            "kdj_j_at_signal": kdj["j"][33],
        },
        "entry": {
            "entry_index": ENTRY_INDEX,
            "entry_date": CAL[ENTRY_INDEX],
            "reference_open": ENTRY_OPEN,
            "expected_fill_price": buy_price,
            "expected_quantity": quantity,
            "expected_fee": buy_fee,
            "expected_cash_after_buy": 100_000.0 - quantity * buy_price - buy_fee,
        },
        "manual_account_case": manual_account_case(),
        "condition_semantics": dict(MACD_CONDITIONS),
        "kdj_condition_semantics": dict(KDJ_CONDITIONS),
    }


def capability_matrix(junit_path: Path) -> dict:
    def entry(capability, implemented, wired, real_call, validated_scope,
              unsupported_scope, version, evidence, tests, status):
        return {
            "capability": capability,
            "implemented": implemented,
            "wired": wired,
            "real_call_verified": real_call,
            "validated_scope": validated_scope,
            "unsupported_scope": unsupported_scope,
            "version": version,
            "evidence": evidence,
            "tests": tests,
            "status": status,
        }

    be = "tests/behavior"
    ind = "tests/indicators"
    leg = "tests/legacy_entry"
    junit = str(junit_path) if junit_path.exists() else None

    return {
        "contract_version": "BT_BEHAVIOR_ACCEPTANCE_V1",
        "generated_from": "scripts/emit_behavior_acceptance_evidence_v1.py",
        "junit_evidence": junit,
        "labels": {
            "FORMULA_VALIDATED": "公式/口径经独立 oracle 或手算核对通过",
            "ENTRYPOINT_WIRED": "存在可使用入口（API/CLI），参数真实透传",
            "EXECUTION_BEHAVIOR_VALIDATED": "经真实引擎链路验证退出/成交行为",
            "ACCOUNTING_VALIDATED": "现金/持仓/费用/收益经独立预期值核对",
            "REAL_DATA_VALIDATED": "真实行情数据验证（本轮未做）",
            "PROFITABILITY_VALIDATED": "盈利能力（本轮未做，且非目标）",
        },
        "capabilities": [
            entry(
                "MACD（MACD_V1）", True, True, True,
                "显式递推 EMA12/26/9，DIF/DEA/HIST=2*(DIF-DEA)，warmup=slow+signal-1=34",
                "不声称与第三方软件逐值一致；未做同复权外部对照",
                MACD_IMPLEMENTATION_VERSION,
                ["src/chanlun_trader/engine/indicators_v1.py"],
                f"{ind}/test_indicator_formulas_v1.py::test_macd_v1_hand_computed_short_sequence",
                "FORMULA_VALIDATED",
            ),
            entry(
                "MACD 条件（五类分别命名）", True, True, True,
                "DIF_ABOVE_ZERO / BOTH_LINES_ABOVE_ZERO / GOLDEN_CROSS / DEATH_CROSS / ABOVE_ZERO_GOLDEN_CROSS",
                "不做含糊的『MACD 水上』合并标签",
                "BT_MACD_CONDITIONS_V1",
                ["src/chanlun_trader/engine/conditions_v1.py"],
                f"{ind}/test_indicator_formulas_v1.py::test_macd_conditions_are_distinct_not_a_single_label",
                "FORMULA_VALIDATED+EXECUTION_BEHAVIOR_VALIDATED",
            ),
            entry(
                "KDJ(9,3,3)（KDJ_V1）", True, True, True,
                "LLV/HHV(9)、RSV、K=(2K'+RSV)/3、D=(2D'+K)/3、J=3K-2D；段首 K=D=50",
                "HHV==LLV / NaN / 非法价格 -> NOT_READY 并结束该段；J 不裁剪",
                KDJ_IMPLEMENTATION_VERSION,
                ["src/chanlun_trader/engine/indicators_v1.py"],
                f"{ind}/test_indicator_formulas_v1.py::test_kdj_v1_hand_computed_short_sequence",
                "FORMULA_VALIDATED",
            ),
            entry(
                "MA / EMA / CROSS", True, True, True,
                "显式递推 EMA、SMA(window)、cross_up/cross_down（要求相邻有效 bar）",
                "无",
                "MA_EMA_CROSS_V1",
                ["src/chanlun_trader/engine/indicators_v1.py"],
                f"{ind}/test_indicator_formulas_v1.py::test_cross_helpers_match_oracle",
                "FORMULA_VALIDATED",
            ),
            entry(
                "固定成本止损（价格百分比）", True, True, True,
                "SL = 实际成交价*(1-stop_pct)；已完成收盘 <= SL 触发；次日开盘执行",
                "不是净盈利阈值；不含费用口径",
                "BT_DAILY_EXIT_V1",
                ["src/chanlun_trader/engine/daily_exit_v1.py"],
                f"{be}/test_daily_exit_rules_v1.py::test_cost_stop_anchor_is_actual_fill_price_not_structure_low",
                "EXECUTION_BEHAVIOR_VALIDATED+ACCOUNTING_VALIDATED",
            ),
            entry(
                "固定止盈", True, True, True,
                "TP = 实际成交价*(1+profit_pct)；已完成收盘 >= TP 触发",
                "价格百分比版本，不是扣费后净盈利阈值",
                "BT_DAILY_EXIT_V1",
                ["src/chanlun_trader/engine/daily_exit_v1.py"],
                f"{be}/test_daily_exit_rules_v1.py::test_take_profit_changes_orders_and_ledger",
                "EXECUTION_BEHAVIOR_VALIDATED",
            ),
            entry(
                "最高收盘价移动止损", True, True, True,
                "peak_close 初始=成交价；激活峰值 >= 成交价*(1+activate)；线=peak*(1-trail)，只收紧",
                "部分卖出不重置峰值；未激活不触发",
                "BT_DAILY_EXIT_V1",
                ["src/chanlun_trader/engine/daily_exit_v1.py"],
                f"{be}/test_daily_exit_rules_v1.py::test_trailing_stop_inactive_then_active_then_tightens",
                "EXECUTION_BEHAVIOR_VALIDATED",
            ),
            entry(
                "固定持有退出", True, True, True,
                "按独立交易日历 entry_session_index + fixed_holding_sessions 计算到期",
                "不用信号日或证券现存数据行数替代",
                "BT_DAILY_EXIT_V1",
                ["src/chanlun_trader/engine/daily_exit_v1.py"],
                f"{be}/test_daily_exit_rules_v1.py::test_fixed_hold_uses_trading_session_index",
                "EXECUTION_BEHAVIOR_VALIDATED",
            ),
            entry(
                "结构价止损（保留独立语义）", False, False, False,
                "本轮仅在 PortfolioExitEvaluatorV1 保留 STRUCTURE_INVALIDATION（因子条件）",
                "RAW 成交价与 QFQ 结构价无转换证据时拒绝配置；本版本显式拒绝 STRUCTURE_STOP",
                "LEGACY/UNSUPPORTED",
                ["src/chanlun_trader/engine/portfolio_exit.py",
                 "src/chanlun_trader/engine/daily_exit_v1.py"],
                f"{be}/test_daily_exit_rules_v1.py::test_unsupported_exit_types_are_rejected",
                "NOT_SUPPORTED_IN_THIS_VERSION",
            ),
            entry(
                "日线盘中触价 / Tick / 盘口", False, False, False,
                "明确拒绝：返回 UNSUPPORTED_EXIT_TYPE / UNSUPPORTED_MODE",
                "CLOSE_CONFIRM 模式不按 high/low 猜盘中先后",
                "NOT_SUPPORTED",
                ["src/chanlun_trader/engine/daily_exit_v1.py",
                 "src/chanlun_trader/engine/behavior_service_v1.py"],
                f"{be}/test_daily_exit_rules_v1.py::test_high_low_touch_without_close_trigger_produces_no_exit",
                "NOT_SUPPORTED",
            ),
            entry(
                "多周期执行", False, False, False,
                "明确拒绝 BT_BEHAVIOR_MULTI_TIMEFRAME_V1",
                "5MIN 撮合仅存在于既有引擎，未经本轮验收",
                "NOT_SUPPORTED",
                ["src/chanlun_trader/engine/behavior_service_v1.py"],
                f"{be}/test_daily_exit_rules_v1.py::test_unsupported_mode_is_rejected_not_silently_downgraded",
                "NOT_SUPPORTED",
            ),
            entry(
                "旧入口大盘过滤开盘前视修复", True, True, True,
                "严格早于 d 的已完成指数收盘；预热不足/无历史 -> fail-closed",
                "旧入口仍为 legacy/未认证范围；不与新入口混用结果",
                "LEGACY_INDEX_FILTER_FIX_V1",
                ["src/chanlun_trader/backtest.py"],
                f"{leg}/test_index_filter_lookahead.py::test_open_decision_ignores_same_day_index_close",
                "EXECUTION_BEHAVIOR_VALIDATED",
            ),
            entry(
                "撮合容量合同（int(volume*rate)==0 拒绝）", True, True, True,
                "DailyBarFillModel 在容量为 0 时返回 PARTICIPATION_LIMIT，不退回全量成交",
                "为既有模型的行为修正；调用方需注意低流动性 bar 现在会拒单",
                "DailyBarFillModel",
                ["src/chanlun_trader/engine/fill.py"],
                f"{be}/test_full_account_chain_v1.py::test_scenario_f_zero_capacity_rejects_instead_of_full_fill",
                "EXECUTION_BEHAVIOR_VALIDATED",
            ),
            entry(
                "公共 API（HTTP）", True, True, True,
                "GET /api/backtest/behavior/contracts；POST /api/backtest/behavior（请求体内合成行情）",
                "不接受 dataset_path；强制 persist_run_manifest=False；不写研究目录",
                "BT_BEHAVIOR_DAILY_V1",
                ["src/chanlun_trader/webapp.py"],
                f"{be}/test_public_entrypoints_v1.py::test_http_behavior_endpoint_runs_and_reports_versions",
                "ENTRYPOINT_WIRED",
            ),
            entry(
                "公共 CLI", True, True, True,
                "scripts/run_behavior_backtest_v1.py --request JSON [--out JSON]，支持 CSV/Parquet 文件入口",
                "与 HTTP 共用同一服务函数",
                "BT_BEHAVIOR_DAILY_V1",
                ["scripts/run_behavior_backtest_v1.py"],
                f"{be}/test_public_entrypoints_v1.py::test_cli_matches_http_semantics",
                "ENTRYPOINT_WIRED",
            ),
            entry(
                "经典 Web 回测入口（/api/backtest）", True, True, True,
                "保持既有策略：在 create_app 下仍返回 LEGACY_EXECUTION_DISABLED",
                "未认证为本轮行为基线；未静默切换到 V2",
                "legacy-v1",
                ["src/chanlun_trader/webapp.py"],
                f"{be}/test_public_entrypoints_v1.py::test_legacy_web_backtest_endpoint_is_not_silently_replaced",
                "LEGACY_NOT_CERTIFIED",
            ),
        ],
        "excluded_this_round": {
            "supported_exit_types": list(SUPPORTED_EXIT_TYPES),
            "reserved_exit_types": list(RESERVED_EXIT_TYPES),
            "indicator_contract": INDICATOR_CONTRACT_VERSION,
        },
        "final_flags": {
            "REAL_RESEARCH_EXECUTED": False,
            "NEW_REAL_PERFORMANCE_EXPOSURES": 0,
            "STRATEGY_PROFITABILITY_CERTIFIED": False,
            "MAIN_MERGED": False,
            "OLD_WORKSPACE_CHANGED": False,
            "ORIGINAL_CANDIDATE_GOAL": "NOT_ACHIEVED_ARCHIVED",
            "REAL_DATA_VALIDATED": False,
            "PROFITABILITY_VALIDATED": False,
        },
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--junit", type=Path,
                        default=Path("reports/junit-bt-behavior.xml"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    csv_path = args.out / "synthetic_bars.csv"
    to_dataset_csv(csv_path, {SYMBOL: entry_bars()})

    # 文件入口与内存入口必须给出一致的语义结果。
    from_memory = run_behavior_backtest_v1(build_request())
    from_file = run_behavior_backtest_v1(build_request(csv_path))
    assert from_memory.semantics() == from_file.semantics(), "文件入口与内存入口语义不一致"

    (args.out / "independent_expectations.json").write_text(
        json.dumps(independent_expectations(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    (args.out / "actual_trace.json").write_text(
        json.dumps(from_file.to_dict(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    (args.out / "CAPABILITY_MATRIX.json").write_text(
        json.dumps(capability_matrix(args.junit), ensure_ascii=False, indent=2),
        encoding="utf-8")

    print("证据输出目录：%s" % args.out)
    print("  成交：%s" % [(f["side"], f["quantity"], f["price"]) for f in from_file.fills])
    print("  期末现金：%.4f 期末权益：%.4f" % (from_file.cash, from_file.final_equity))
    print("  文件入口与内存入口语义一致：True")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
