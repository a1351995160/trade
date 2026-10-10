"""全池数据审核后，冻结全部合格证券及完整排除原因。

范围只由输入质量确定；不计算收益、不授权账户，也不升级历史可见性。
父输入真实性由公共冻结链绑定，本模块验证与给定父输入的确定性派生关系。
"""
from copy import deepcopy

import numpy as np
import pandas as pd

from .common import stable_hash
from .universe_account_inputs_v1 import (
    _prepare_owned_universe_account_inputs_v1,
    audit_universe_account_inputs_v1, normalized_universe_window_v1,
    universe_input_identity_v1,
)


VERSION = "UNIVERSE_QUALIFIED_SCOPE_V1"
POLICY = {"version": VERSION, "selection": "ALL_ACCOUNT_DATA_QUALIFIED_SYMBOLS",
    "event_attribution": "VALIDATED_EVENT_SYMBOL", "unknown_global": "BLOCK",
    "corporate_summary": "REGISTERED_PER_SYMBOL_ACCOUNT_COVERAGE_REQUIRED",
    "combined_actions": "PER_SYMBOL_PRICE_AND_LEDGER_TERMS_REQUIRED",
    "strategy_results_used": False, "window_selection": "RETROSPECTIVE_DATA_AVAILABILITY"}
POLICY_IDENTITY = stable_hash(POLICY)
_RECEIPT_FIELDS = {"version", "policy_identity", "target_symbols", "qualified_symbols",
    "excluded", "blocking_global_gaps", "parent_input_identity", "parent_window",
    "parent_universe_identity", "parent_source_identity", "parent_manifest_hash",
    "source_hashes", "required_fields", "warmup_bars", "account_audit",
    "projected_input_identity", "scope_identity"}
_SYMBOL_MAPS = ("listing_dates", "listing_date_sources", "listing_sessions_before_calendar",
                "source_qualification")


def _partition(audit):
    symbols = audit["window"]["symbols"]
    by_symbol = {symbol: [] for symbol in symbols}
    blocking = []
    for gap in audit["typed_gaps"]:
        scope = gap["scope"]
        if scope in {"SYMBOL", "EVENT"} and gap.get("symbol") in by_symbol:
            by_symbol[gap["symbol"]].append(deepcopy(gap))
        elif scope == "SCOPE_SUMMARY" and gap["reason"] == "UNIVERSE_CORPORATE_ACTIONS_INCOMPLETE":
            # 总标志与全部完整证明矛盾时，不猜测遗漏了哪类事实。
            if not any(row["complete"] is False for row in audit["account_coverage"]):
                blocking.append(gap["reason"])
        else:
            blocking.append(gap["reason"])
    qualified = [symbol for symbol in symbols if not by_symbol[symbol]]
    excluded = [{"symbol": symbol,
                 "reasons": sorted({gap["reason"] for gap in by_symbol[symbol]}),
                 "gaps": by_symbol[symbol]}
                for symbol in symbols if by_symbol[symbol]]
    if not qualified:
        blocking.append("UNIVERSE_QUALIFIED_SCOPE_EMPTY")
    return qualified, excluded, sorted(set(blocking))


def _project(bundle, window, symbols):
    selected = frozenset(symbols)
    # 每列 take 只分配选中行，避免 loc 后 deep copy 合并整表分块。
    # 子列独立于父缓冲，内部严格认证接管这些列，不再复制第二份。
    child = {key: deepcopy(value) for key, value in bundle.items()
             if key not in {"daily", "turn", "states", "qualified_scope", "events",
                            "corporate_action_coverage"}}
    for key in ("daily", "turn", "states"):
        frame = bundle[key]
        positions = np.flatnonzero(frame.symbol.isin(selected).to_numpy())
        child[key] = pd.DataFrame(
            {name: pd.Series(series.array.take(positions), dtype=series.dtype, copy=False)
             for name, series in frame.items()}, copy=False)
    child["events"] = [deepcopy(event) for event in bundle["events"] if event["symbol"] in selected]
    for key in _SYMBOL_MAPS:
        if key in child and child[key] is not None:
            if not isinstance(child[key], dict):
                raise ValueError("UNIVERSE_QUALIFIED_SCOPE_METADATA_INVALID:" + key)
            child[key] = {symbol: value for symbol, value in child[key].items() if symbol in selected}
    rows = bundle["corporate_action_coverage"]
    if isinstance(rows, dict):
        rows = [rows]
    coverage = []
    for row in rows:
        kept = sorted(selected.intersection(row.get("symbols", [row.get("symbol")])))
        if kept:
            coverage.append({**deepcopy(row), "symbols": kept})
    child["corporate_action_coverage"] = coverage
    # 仅从 ACCOUNT 审核的逐股完整区间和已验证事件投影后重新认证。
    child["corporate_actions_complete"] = True
    child_window = {**deepcopy(window), "symbols": sorted(symbols)}
    return child, child_window


def qualify_universe_bundle(prepared, required_fields=(), warmup_bars=0, *,
                            _owned_inputs_receiver=None):
    """返回正式 child prepared；共同缺口/空范围返回 ready=False 的完整回执。

    结构错误仍抛出工程错误。成功范围恰好等于全部合格证券，不能手选。
    """
    if not isinstance(prepared, dict) or not {"bundle", "window", "input_identity"} <= set(prepared):
        raise ValueError("UNIVERSE_QUALIFIED_SCOPE_PARENT_INVALID")
    bundle, window = prepared["bundle"], normalized_universe_window_v1(prepared["window"])
    if not isinstance(bundle, dict) or "qualified_scope" in bundle:
        raise ValueError("UNIVERSE_QUALIFIED_SCOPE_PARENT_INVALID")
    if universe_input_identity_v1(bundle, window) != prepared["input_identity"]:
        raise ValueError("UNIVERSE_QUALIFIED_SCOPE_PARENT_CHANGED")
    audit = audit_universe_account_inputs_v1(bundle, window,
        required_fields=required_fields, warmup_bars=warmup_bars)
    if audit["input_identity"] != prepared["input_identity"]:
        raise ValueError("UNIVERSE_QUALIFIED_SCOPE_PARENT_NOT_NORMALIZED")
    qualified, excluded, blocking = _partition(audit)
    receipt = {"version": VERSION, "policy_identity": POLICY_IDENTITY,
        "target_symbols": window["symbols"], "qualified_symbols": qualified,
        "excluded": excluded, "blocking_global_gaps": blocking,
        "parent_input_identity": prepared["input_identity"], "parent_window": window,
        "parent_universe_identity": bundle.get("universe_identity"),
        "parent_source_identity": bundle.get("source_identity"),
        "parent_manifest_hash": prepared.get("qualification", {}).get("manifest_hash"),
        "source_hashes": deepcopy(bundle["source_hashes"]),
        "required_fields": audit["required_fields"], "warmup_bars": audit["warmup_bars"],
        "account_audit": audit, "projected_input_identity": None}
    child, child_window, inputs = None, None, None
    if not blocking:
        child, child_window = _project(bundle, window, qualified)
        # 在生成回执之前先以默认严格 ACCOUNT 认证投影事实。
        inputs = _prepare_owned_universe_account_inputs_v1(child, child_window,
            required_fields=required_fields, warmup_bars=warmup_bars)
        child = inputs.bundle
        receipt["projected_input_identity"] = inputs.input_identity
    receipt["scope_identity"] = stable_hash(receipt)
    if child is not None:
        child["qualified_scope"] = deepcopy(receipt)
        input_identity = universe_input_identity_v1(child, child_window)
        # 原严格构造发生在回执之前；保留的输入必须绑定最终回执身份。
        inputs.input_identity = input_identity
        if _owned_inputs_receiver is not None:
            verify_qualified_scope_bundle(child, child_window,
                required_fields=sorted(inputs.required_fields), warmup_bars=inputs.warmup_bars)
            _owned_inputs_receiver(inputs)
    else:
        input_identity = None
    qualification = {**deepcopy(prepared.get("qualification", {})),
        "data_stage": "ACCOUNT", "account_data_ready": inputs is not None,
        "historical_availability": audit["coverage"]["historical_availability"],
        "historical_independence": "UNKNOWN", "independent_confirmation_eligible": False,
        "strategy_qualified": False, "coverage": deepcopy(inputs.coverage if inputs else audit["coverage"]),
        "account_executed": False, "account_authorized": False, "budget_created": False,
        "scope_identity": receipt["scope_identity"],
        "limitations": ["范围按整个窗口的数据完整性回顾性确定，不代表事前全市场可投资范围。",
                        "数据通过不授予策略、账户、预算或历史独立确认资格。"]}
    result = {"ready": inputs is not None, "bundle": child, "window": child_window,
            "input_identity": input_identity, "qualification": qualification,
            "scope_receipt": receipt}
    if 'trusted_data_access' in prepared:
        result['trusted_data_access'] = deepcopy(prepared['trusted_data_access'])
    return result


def verify_qualified_scope_bundle(bundle, window, *, parent_prepared=None,
                                  required_fields=None, warmup_bars=None):
    """校验回执/子身份；给定可信父 INPUT 时重算整个确定性派生关系。

    无父输入只能证明内部一致，公共冻结/执行复验必须另绑定并提供父输入。
    不从回执自报路径读取文件，也不授予数据访问或账户权限。
    """
    receipt = bundle.get("qualified_scope") if isinstance(bundle, dict) else None
    if not isinstance(receipt, dict) or set(receipt) != _RECEIPT_FIELDS:
        raise ValueError("UNIVERSE_QUALIFIED_SCOPE_RECEIPT_INVALID")
    try:
        parent_window = normalized_universe_window_v1(receipt["parent_window"])
        child_window = normalized_universe_window_v1(window)
        audit = receipt["account_audit"]
        if (receipt["version"] != VERSION or receipt["policy_identity"] != POLICY_IDENTITY
                or receipt["scope_identity"] != stable_hash({k: v for k, v in receipt.items() if k != "scope_identity"})
                or receipt["target_symbols"] != parent_window["symbols"]
                or child_window != {**parent_window, "symbols": receipt["qualified_symbols"]}
                or receipt["blocking_global_gaps"]
                or audit["window"] != parent_window or audit["input_identity"] != receipt["parent_input_identity"]
                or audit["version"] != "UNIVERSE_ACCOUNT_INPUT_AUDIT_V1"
                or audit["stage"] != "ACCOUNT" or audit["account_executed"] is not False
                or audit["account_authorized"] is not False or audit["budget_created"] is not False
                or audit["audit_identity"] != stable_hash({k: v for k, v in audit.items() if k != "audit_identity"})
                or receipt["required_fields"] != audit["required_fields"]
                or receipt["warmup_bars"] != audit["warmup_bars"]
                or type(receipt["warmup_bars"]) is not int or receipt["warmup_bars"] < 0
                or not isinstance(receipt["required_fields"], list)
                or receipt["required_fields"] != sorted(set(receipt["required_fields"]))
                or not set(receipt["required_fields"]) <= {
                    "open", "high", "low", "close", "volume", "amount", "prev_close", "turn"}
                or bundle["source_hashes"] != receipt["source_hashes"]
                or bundle["universe_identity"] != receipt["parent_universe_identity"]
                or bundle["source_identity"] != receipt["parent_source_identity"]):
            raise ValueError()
        qualified, excluded, blocking = _partition(audit)
        if (qualified != receipt["qualified_symbols"] or excluded != receipt["excluded"] or blocking
                or (required_fields is not None and sorted(set(required_fields)) != receipt["required_fields"])
                or (warmup_bars is not None and warmup_bars != receipt["warmup_bars"])):
            raise ValueError()
        bare = {k: v for k, v in bundle.items() if k != "qualified_scope"}
        if universe_input_identity_v1(bare, child_window) != receipt["projected_input_identity"]:
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("UNIVERSE_QUALIFIED_SCOPE_BINDING_INVALID") from None
    if parent_prepared is not None:
        expected = qualify_universe_bundle(parent_prepared,
            required_fields=receipt["required_fields"], warmup_bars=receipt["warmup_bars"])
        if (not expected["ready"] or expected["scope_receipt"] != receipt
                or expected["window"] != child_window
                or expected["input_identity"] != universe_input_identity_v1(bundle, child_window)):
            raise ValueError("UNIVERSE_QUALIFIED_SCOPE_DERIVATION_CHANGED")
    return deepcopy(receipt)
