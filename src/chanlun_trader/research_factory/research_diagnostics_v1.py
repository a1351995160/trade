"""公共账户结果的探索诊断；不授予策略资格，不把推测当成失败因果。"""
from __future__ import annotations

import math
from typing import Any, Mapping

from .common import stable_hash
from .failure_adapter import FailureKnowledgeViewV1


# AI 只能看到这些固定文案，不能借原始错误、数值或模型文本绕过反馈边界。
_REASONS = {
    "ACCOUNT_INCOMPLETE": ("ENGINE_FAILURE", "账户证据不完整，不能评价策略表现。", "repair_account_evidence"),
    "ACCOUNT_UNRECONCILED": ("ENGINE_FAILURE", "账户独立核对存在差异，先修复工程问题。", "repair_account_reconciliation"),
    "NO_TRADES": ("EXECUTION_FAILURE", "本次没有成交；需检查信号、状态过滤和成交条件，尚不能确定唯一原因。", "inspect_execution_evidence"),
    "EXECUTION_FILTERS_OBSERVED": ("EXECUTION_FAILURE", "存在状态过滤或订单未完全成交；这只是观察结果，不证明它导致亏损。", "preserve_execution_constraints"),
    "COST_ERASES_ACCOUNT_GAIN": ("ROBUSTNESS_FAILURE", "按原成交路径加回已记录费用后账户转为盈利；不是零成本重新回测。", "preserve_frozen_cost_model"),
    "EXPLORATORY_LOSS": ("ALPHA_FAILURE", "本次探索账户亏损，尚未确定原因，不能据此断言策略永久无效。", "preserve_frozen_research_scope"),
    "LIMITED_SAMPLE": ("SAMPLE_FAILURE", "探索样本有限，不能据此取得正式资格。", "require_independent_confirmation"),
    "INDEPENDENT_CONFIRMATION_REQUIRED": ("SAMPLE_FAILURE", "尚未经过独立确认，本轮只提供探索证据。", "require_independent_confirmation"),
}


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("BOOLEAN_IS_NOT_ACCOUNT_NUMBER")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("NONFINITE_ACCOUNT_NUMBER")
    return result


def _account(result: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    chain = result.get("chain")
    if not isinstance(chain, Mapping):
        return "ACCOUNT_INCOMPLETE", {}
    if chain.get("issues") or chain.get("status") == "RECONCILIATION_FAILED":
        return "ACCOUNT_UNRECONCILED", {}
    try:
        rows, trades = chain["independent_account_checks"], chain["trades"]
        if (chain["status"] != "RECONCILED_DIAGNOSTIC" or chain["issues"] != []
                or not rows or not isinstance(trades, list)
                or len(rows) != chain["n_account_days"] or len(trades) != chain["n_trades"]):
            return "ACCOUNT_INCOMPLETE", {}
        dates = [row["date"] for row in rows]
        if dates != sorted(set(dates)) or [dates[0], dates[-1]] != chain["account_dates"]:
            return "ACCOUNT_INCOMPLETE", {}
        initial = _number(chain["execution_assumptions"]["initial_cash"])
        if initial <= 0:
            return "ACCOUNT_INCOMPLETE", {}
        equities = [_number(row["equity"]) for row in rows]
        if any(abs(_number(row["max_abs_ledger_delta"])) > .001 or
               abs(_number(row["cash"]) + _number(row["market_value"]) - equity) > .001
               for row, equity in zip(rows, equities)):
            return "ACCOUNT_UNRECONCILED", {}
        fees = [_number(trade["fee"]) for trade in trades]
        corporate = chain.get("corporate_account") or {}
        dividend_tax = _number(corporate.get("dividend_tax_withheld", 0))
        if min([dividend_tax, *fees]) < 0:
            return "ACCOUNT_INCOMPLETE", {}
        peak, drawdown = initial, 0.0
        for equity in equities:
            peak = max(peak, equity)
            drawdown = max(drawdown, (peak - equity) / peak)
        return "COMPLETE", {
            "initial_cash": initial, "ending_equity": equities[-1],
            "net_return": equities[-1] / initial - 1, "max_drawdown": drawdown,
            "total_fees": sum(fees), "dividend_tax_withheld": dividend_tax,
            "trade_count": len(trades), "account_days": len(rows),
            "account_gain_before_recorded_costs": equities[-1] - initial + sum(fees) + dividend_tax,
        }
    except (KeyError, TypeError, ValueError, OverflowError):
        return "ACCOUNT_INCOMPLETE", {}


def diagnose(result: Mapping[str, Any], *, trial_id: str,
             benchmark: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """诊断 S1 公共账户形状；未知结果形状明确返回证据不完整。"""
    if not trial_id:
        raise ValueError("TRIAL_ID_REQUIRED")
    state, metrics = _account(result)
    reasons = [] if state == "COMPLETE" else [state]
    chain = result.get("chain") or {}
    if state == "COMPLETE":
        if metrics["trade_count"] == 0:
            reasons.append("NO_TRADES")
        if result.get("decision_state_rejections") or any(
                item.get("filled_quantity", 0) < item.get("quantity", 0)
                for item in chain.get("orders", [])):
            reasons.append("EXECUTION_FILTERS_OBSERVED")
        if metrics["net_return"] < 0:
            reasons.append("EXPLORATORY_LOSS")
            if metrics["account_gain_before_recorded_costs"] > 0:
                reasons.append("COST_ERASES_ACCOUNT_GAIN")
        # 仅为描述性样本提示，绝不是显著性或晋级门槛。
        if metrics["account_days"] < 252 or metrics["trade_count"] < 30:
            reasons.append("LIMITED_SAMPLE")
    reasons.append("INDEPENDENT_CONFIRMATION_REQUIRED")
    comparison: dict[str, Any] = {"status": "NOT_RUN"}
    if benchmark is not None:
        other_state, other_metrics = _account(benchmark)
        scope = result.get("comparison_context")
        comparable = (state == other_state == "COMPLETE" and isinstance(scope, Mapping)
                      and bool(scope.get("input_identity"))
                      and scope == benchmark.get("comparison_context")
                      and chain["account_dates"] == benchmark["chain"]["account_dates"]
                      and [row["date"] for row in chain["independent_account_checks"]]
                      == [row["date"] for row in benchmark["chain"]["independent_account_checks"]]
                      and chain["execution_assumptions"] == benchmark["chain"]["execution_assumptions"])
        comparison = {"status": "AVAILABLE" if comparable else "NOT_COMPARABLE",
                      "source_result_hash": stable_hash(benchmark)}
        if comparable:
            comparison["net_return_difference"] = metrics["net_return"] - other_metrics["net_return"]
    report = {
        "schema_version": "RESEARCH_DIAGNOSTICS_V1", "trial_id": trial_id,
        "result_hash": stable_hash(result), "account_state": state,
        "metrics": metrics, "reason_codes": reasons, "benchmark": comparison,
        "evidence_paths": ["chain.issues", "chain.independent_account_checks",
                           "chain.execution_assumptions", "chain.trades", "chain.orders",
                           "chain.corporate_account", "decision_state_rejections"],
        "qualification": "NOT_ASSESSED", "execution_authorization_granted": False,
        "limitations": ["历史发布时间按既有假设处理，未取得严格历史资格。",
                        "费用加回只解释原成交路径，不代表另一种成本下可实现的账户。",
                        "样本提示是描述性规则，不能代替独立统计确认。"],
    }
    report["diagnostic_hash"] = stable_hash(report)
    return report


def feedback_view(diagnostic: Mapping[str, Any]) -> dict[str, Any]:
    """只允许固定定性反馈进入 AI，详细账本和收益留在人类报告中。"""
    body = {key: value for key, value in diagnostic.items() if key != "diagnostic_hash"}
    if stable_hash(body) != diagnostic.get("diagnostic_hash"):
        raise ValueError("DIAGNOSTIC_HASH_MISMATCH")
    entries = []
    for code in diagnostic["reason_codes"]:
        category, reason, constraint = _REASONS[code]
        entries.append({"category": category, "mechanism": "ACCOUNT_EXPLORATION",
                        "high_level_reason": reason, "reason_code": code,
                        "constraints": [constraint]})
    return FailureKnowledgeViewV1(
        failure_view_version="1.0.0", entries=tuple(entries),
        source_history_hash=str(diagnostic["diagnostic_hash"]),
    ).to_dict()


def render_markdown(diagnostic: Mapping[str, Any]) -> str:
    lines = ["# 探索账户诊断", "", f"试验：{diagnostic['trial_id']}",
             f"账户检查：{diagnostic['account_state']}", "策略资格：尚未评审", "",
             "## 观察与限制", *[f"- {_REASONS[code][1]}" for code in diagnostic["reason_codes"]],
             "", "## 账户结果", *[f"- {key}：{value}" for key, value in diagnostic["metrics"].items()],
             "", f"基准比较：{diagnostic['benchmark']['status']}",
             f"结果凭据：{diagnostic['result_hash']}", "", *diagnostic["limitations"]]
    return "\n".join(lines) + "\n"
