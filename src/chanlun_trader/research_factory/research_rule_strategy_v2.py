"""冻结研究规则适配器；条件复用原三值求值器，账户与成交仍由公共引擎负责。"""
from copy import deepcopy
from pathlib import Path
import math
import re

import pandas as pd

from chanlun_trader.engine.conditions_v2 import ConditionContext, Expr, evaluate_condition
from scripts.probe_all_indicator_strategy_v1 import pilot_registry, strategy_definition
from scripts.s1_causal_price_strategy_v1 import Causal51VoteStrategy
from .common import stable_hash
from .strategy_interface_v1 import Context, Decision, Requirements, TargetWeight


CAPABILITY = "RESEARCH_RULE_STRATEGY_V2"
FIELDS = ("open", "high", "low", "close", "volume", "amount", "prev_close")
COMPARISONS = {"gt", "ge", "lt", "le", "eq", "ne", "cross_up", "cross_down", "above", "below"}
BOOLEAN = COMPARISONS | {"and", "or", "not", "between"}
PAYLOAD_FIELDS = {"version", "hypothesis", "change_reason", "buy", "sell", "market_filter",
                  "min_hold_sessions", "max_hold_sessions", "cooldown_sessions", "target_weight"}


def rule_capabilities() -> dict:
    registry = pilot_registry()
    definition = strategy_definition(registry)
    return {"version": CAPABILITY, "catalog_sha256": definition["catalog_sha256"],
            "indicators": [{**item, "outputs": list(registry.get(item["id"], item["version"]).outputs)}
                           for item in definition["indicators"]],
            "operators": sorted(BOOLEAN | {"const", "indicator", "field", "ref"}),
            "fields": list(FIELDS), "max_depth": 6, "max_nodes": 64,
            "max_lag_sessions": 60, "max_holding_sessions": 252,
            "max_cooldown_sessions": 60, "target_weight_range": [0.01, 1.0],
            "indicator_parameters": "仅使用目录冻结参数；不接受模型覆盖",
            "market_filter_scope": "同证券合格价格与成交字段；不接受未经资格核验的外部市场状态",
            "qualification": "EXPLORATORY_ONLY"}


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"RULE_INTEGER_INVALID:{name}")
    return value


def _parse(node, catalog, *, level=1, counter=None, boolean=False, market=False):
    counter = [0] if counter is None else counter
    counter[0] += 1
    if level > 6 or counter[0] > 64:
        raise ValueError("RULE_EXPRESSION_LIMIT")
    if not isinstance(node, dict) or set(node) != {"op", "args", "params"}:
        raise ValueError("RULE_EXPRESSION_FIELDS")
    name, args, params = node["op"], node["args"], node["params"]
    if not isinstance(name, str) or not isinstance(args, list) or not isinstance(params, dict):
        raise ValueError("RULE_EXPRESSION_TYPES")
    if name not in BOOLEAN | {"const", "field", "indicator", "ref"}:
        raise ValueError("RULE_OPERATOR_UNSUPPORTED")
    if boolean != (name in BOOLEAN):
        raise ValueError("RULE_EXPRESSION_TYPE_MISMATCH")
    if name == "const":
        value = params.get("value")
        if args or set(params) != {"value"} or type(value) not in (int, float) or abs(value) > 1e12 or not math.isfinite(value):
            raise ValueError("RULE_CONSTANT_INVALID")
        return Expr(name, (), {"value": float(value)})
    if name == "field":
        if len(args) != 1 or not isinstance(args[0], str) or args[0] not in FIELDS or params:
            raise ValueError("RULE_FIELD_UNSUPPORTED")
        return Expr(name, tuple(args))
    if name == "indicator":
        if market or len(args) != 1 or not isinstance(args[0], str) or args[0] not in catalog:
            raise ValueError("RULE_INDICATOR_UNSUPPORTED")
        spec = catalog[args[0]]
        if (set(params) != {"output", "version"} or params["version"] != spec["version"]
                or not isinstance(params["output"], str) or params["output"] not in spec["outputs"]):
            raise ValueError("RULE_INDICATOR_OUTPUT_OR_VERSION_INVALID")
        return Expr(name, tuple(args), dict(params))
    expected = 1 if name in {"not", "ref"} else 3 if name == "between" else 2
    if not (2 <= len(args) <= 4 if name in {"and", "or"} else len(args) == expected):
        raise ValueError("RULE_OPERATOR_ARITY")
    if name == "ref":
        if set(params) != {"periods"}:
            raise ValueError("RULE_REF_PARAMETERS")
        _integer(params["periods"], 0, 60, "periods")
    elif params:
        raise ValueError("RULE_OPERATOR_PARAMETERS")
    children = tuple(_parse(child, catalog, level=level + 1, counter=counter,
                            boolean=name in {"and", "or", "not"}, market=market) for child in args)
    return Expr(name, children, dict(params))


def validate_rule_payload(payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) != PAYLOAD_FIELDS or payload["version"] != CAPABILITY:
        raise ValueError("RULE_PAYLOAD_FIELDS_OR_VERSION")
    for key in ("hypothesis", "change_reason"):
        if not isinstance(payload[key], str) or not payload[key].strip() or len(payload[key]) > 4000:
            raise ValueError("RULE_DESCRIPTION_INVALID")
    catalog = {item["id"]: item for item in rule_capabilities()["indicators"]}
    canonical = deepcopy(payload)
    for key in ("buy", "sell", "market_filter"):
        if key == "market_filter" and payload[key] is None:
            continue
        canonical[key] = _parse(payload[key], catalog, boolean=True, market=key == "market_filter").to_dict()
    minimum = _integer(payload["min_hold_sessions"], 0, 252, "min_hold_sessions")
    _integer(payload["max_hold_sessions"], max(1, minimum), 252, "max_hold_sessions")
    _integer(payload["cooldown_sessions"], 0, 60, "cooldown_sessions")
    weight = payload["target_weight"]
    if type(weight) not in (int, float) or not 0.01 <= weight <= 1 or not math.isfinite(weight):
        raise ValueError("RULE_TARGET_WEIGHT_INVALID")
    canonical["target_weight"] = float(weight)
    return canonical


def _references(node: Expr):
    if node.op == "indicator":
        yield str(node.args[0]), str(node.params["output"])
    for child in node.args:
        if isinstance(child, Expr):
            yield from _references(child)


def _field_references(node: Expr):
    if node.op == "field":
        yield str(node.args[0])
    for child in node.args:
        if isinstance(child, Expr):
            yield from _field_references(child)


class ResearchRuleStrategyV2:
    requirements = Requirements("A_SHARE", "1D", "CAUSAL_HFQ_FEATURE_RAW_EXECUTION",
                                (*FIELDS, "turn"), 60, ("TARGET_WEIGHT",),
                                capabilities=(CAPABILITY, "EXECUTION_STATE", "PERSONAL_CASH_DIVIDEND"))
    source_files = (*Causal51VoteStrategy.source_files,
                    str(Path(__file__).resolve().parents[1] / "engine/conditions_v2.py"))

    def __init__(self, payload: dict, *, strategy_id: str):
        if not isinstance(strategy_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", strategy_id):
            raise ValueError("RULE_STRATEGY_ID_INVALID")
        self.strategy_id = strategy_id
        self.payload = validate_rule_payload(payload)
        capabilities = rule_capabilities()
        catalog = {item["id"]: item for item in capabilities["indicators"]}
        self.expressions = {key: _parse(value, catalog, boolean=True, market=key == "market_filter")
                            for key in ("buy", "sell", "market_filter")
                            if (value := self.payload[key]) is not None}
        self.references = sorted({ref for expr in self.expressions.values() for ref in _references(expr)})
        rule = {key: value for key, value in self.payload.items() if key not in {"hypothesis", "change_reason"}}
        self.rule_identity = stable_hash({"rule": rule, "catalog_sha256": capabilities["catalog_sha256"]})
        self.definition = {"strategy_id": strategy_id, "catalog_sha256": capabilities["catalog_sha256"],
                           "rule_identity": self.rule_identity, "rule": rule,
                           "indicators": [catalog[key] for key in sorted({key for key, _ in self.references})]}
        self.parameters = {"rule_definition": deepcopy(self.definition), "candidate_payload": deepcopy(self.payload),
                           "rule_identity": self.rule_identity, "factor_ids": sorted({key for key, _ in self.references})}

    def validate(self):
        expected = type(self)(self.payload, strategy_id=self.strategy_id)
        if (self.parameters != expected.parameters or self.definition != expected.definition
                or self.rule_identity != expected.rule_identity or self.expressions != expected.expressions
                or self.references != expected.references):
            raise ValueError("RULE_CHANGED_AFTER_FREEZE")

    def build_feature_matrix(self, bars: pd.DataFrame, vendor_turn: pd.Series | None = None) -> pd.DataFrame:
        """接收调用方已按事件时点构造的因果价格视图；不改变原始成交行情。"""
        if bars.empty or not set(FIELDS) <= set(bars) or bars.index.has_duplicates or not bars.index.is_monotonic_increasing:
            raise ValueError("RULE_FEATURE_BARS_INVALID")
        series = {name: pd.to_numeric(bars[name], errors="raise").astype(float) for name in FIELDS}
        matrix = pd.DataFrame(series)
        registry = pilot_registry()
        for item in self.definition["indicators"]:
            result = registry.compute(item["id"], series["close"], version=item["version"],
                                      high=series["high"], low=series["low"], open_=series["open"],
                                      volume=series["volume"], amount=series["amount"], prev_close=series["prev_close"],
                                      params=item["params"], extra_data={"vendor_turn": vendor_turn} if vendor_turn is not None else {})
            for key, output in self.references:
                if key == item["id"]:
                    column = f"{key}.{output}"
                    matrix[column] = result.output(output)
                    matrix[f"{column}__ready"] = result.ready()
        return matrix

    def on_close(self, context: Context) -> Decision:
        history, index = context.history, context.index
        if (not isinstance(history, pd.DataFrame) or type(index) is not int or not 0 <= index < len(context.calendar)
                or len(history) != index + 1 or tuple(history.index) != context.calendar[:index + 1]
                or len(set(context.calendar)) != len(context.calendar) or tuple(sorted(context.calendar)) != context.calendar):
            raise ValueError("STRATEGY_HISTORY_NOT_PREFIX_ONLY")
        account = context.account
        if not isinstance(account, dict) or set(account) != {"quantity", "sellable_quantity", "entry_session_index", "last_exit_session_index"}:
            raise ValueError("RULE_LEDGER_PROJECTION_REQUIRED")
        quantity = _integer(account["quantity"], 0, 10**12, "quantity")
        _integer(account["sellable_quantity"], 0, quantity, "sellable_quantity")
        entry, last_exit = account["entry_session_index"], account["last_exit_session_index"]
        if quantity:
            _integer(entry, 0, index, "entry_session_index")
        elif entry is not None:
            raise ValueError("RULE_FLAT_ACCOUNT_HAS_ENTRY")
        if last_exit is not None:
            _integer(last_exit, 0, index, "last_exit_session_index")
            if quantity and last_exit >= entry:
                raise ValueError("RULE_LEDGER_SESSION_ORDER")
        old = context.state
        if not isinstance(old, dict):
            raise ValueError("RULE_STATE_INVALID")
        if old and (set(old) != {"rule_identity", "last_session_index", "exit_pending"}
                    or old["rule_identity"] != self.rule_identity or type(old["last_session_index"]) is not int
                    or old["last_session_index"] != index - 1 or type(old["exit_pending"]) is not bool):
            raise ValueError("RULE_STATE_IDENTITY_OR_CONTINUITY")
        state = {"rule_identity": self.rule_identity, "last_session_index": index,
                 "exit_pending": bool(quantity and old.get("exit_pending", False))}
        values, ready = {}, {}
        for key, output in self.references:
            column = f"{key}.{output}"
            values[column] = pd.to_numeric(history.get(column, pd.Series(float("nan"), index=history.index)), errors="coerce")
            mask = history.get(f"{column}__ready", pd.Series(False, index=history.index))
            ready[column] = mask.map(lambda item: type(item) is bool and item)
        fields = {name: pd.to_numeric(history.get(name, pd.Series(float("nan"), index=history.index)), errors="coerce") for name in FIELDS}
        evaluation = ConditionContext(values, fields, history.index, ready=ready)
        truth = {name: float(evaluate_condition(expr, evaluation).iloc[-1]) for name, expr in self.expressions.items()}
        metadata = {name: None if not math.isfinite(value) else bool(value) for name, value in truth.items()}
        def decision(reason, weight=None):
            return Decision(reason, None if weight is None else TargetWeight(weight, increase_existing=False), state, metadata)
        held = index - entry if quantity else 0
        exit_due = quantity and (state["exit_pending"] or held >= self.payload["max_hold_sessions"]
                                 or (held >= self.payload["min_hold_sessions"] and truth["sell"] == 1))
        if exit_due:
            state["exit_pending"] = True
            return decision("EXIT_PENDING" if old.get("exit_pending") else "SELL", 0.0)
        if quantity:
            return decision("HOLD")
        if truth["sell"] == 1:
            return decision("EXIT_PRIORITY")
        if last_exit is not None and index - last_exit <= self.payload["cooldown_sessions"]:
            return decision("COOLDOWN")
        # 缺失的卖出判断也不能被解释成买入许可。
        missing_input = any(not bool(ready[key].iloc[-1]) or not math.isfinite(values[key].iloc[-1]) for key in values)
        missing_input |= any(not math.isfinite(fields[key].iloc[-1])
                             for expr in self.expressions.values() for key in _field_references(expr))
        if missing_input or any(not math.isfinite(value) for value in truth.values()):
            return decision("CONDITION_UNKNOWN")
        if truth["buy"] != 1 or truth.get("market_filter", 1) != 1:
            return decision("CONDITION_FALSE")
        return decision("BUY", self.payload["target_weight"])
