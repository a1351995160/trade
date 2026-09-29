"""V3 参数实例与退出声明；风险退出由账户后端按账本分笔执行。"""
from copy import deepcopy
from pathlib import Path
import math
import re

import pandas as pd

from chanlun_trader.engine.daily_exit_v2 import DailyExitRuleSetV2
from . import research_rule_strategy_v2 as v2
from .common import stable_hash
from .strategy_interface_v1 import Requirements

CAPABILITY = "RESEARCH_RULE_STRATEGY_V3"
EXIT_POLICY_VERSION = "RESEARCH_CLOSE_EXIT_V1"
EXECUTION_MODE = "CLOSE_CONFIRM_NEXT_SESSION_OPEN"
EXIT_FIELDS = {"execution_mode", "stop_loss_pct", "take_profit_pct", "trailing_activate_pct", "trailing_pct"}
WINDOW_OVERRIDES = {"MA": (1, 252), "ROLLING_VOLATILITY": (2, 252)}


def exit_policy() -> dict:
    return {"version": EXIT_POLICY_VERSION, "execution_mode": EXECUTION_MODE,
            "cost_anchor": "LOT_ACTUAL_FILL_WITH_SLIPPAGE_EXCLUDING_COMMISSION",
            "trailing_anchor": "HIGHEST_COMPLETED_RAW_PLUS_PAID_GROSS_CASH_SINCE_FILL",
            "comparison_price_policy": "RAW_PLUS_PAID_GROSS_CASH_V1",
            "cash_dividend_policy": "LOT_ENTITLED_PAID_GROSS_CASH_PER_SHARE_ADDED_TO_RAW_CLOSE",
            "supported_cash_payment": "PAYMENT_DATE_EQUALS_EFFECTIVE_DATE",
            "fill_price_policy": "RAW_EXECUTION_TAX_AND_FEES_SEPARATE",
            "risk_exit_min_hold_exempt": True, "signal_exit_min_hold_exempt": False,
            "flat_signal_sell": "EXIT_PRIORITY", "risk_exit_requires_position": True,
            "pending_exit": "PERSIST_UNTIL_FILLED", "account_execution_required": True}


def rule_capabilities() -> dict:
    result = deepcopy(v2.rule_capabilities())
    result.update(version=CAPABILITY, indicator_parameters="显式实例；非开放参数只接受目录默认值",
                  window_overrides={key: list(bounds) for key, bounds in WINDOW_OVERRIDES.items()},
                  exit_policy=exit_policy(), exit_configuration_validated=True)
    return result


def _instances(items: list) -> list:
    if not isinstance(items, list) or len(items) > 64:
        raise ValueError("RULE_INSTANCES_INVALID")
    registry = v2.pilot_registry()
    catalog = {item["id"]: item for item in v2.rule_capabilities()["indicators"]}
    resolved, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {"instance_id", "id", "version", "params"}:
            raise ValueError("RULE_INSTANCE_FIELDS")
        alias = item["instance_id"]
        if not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", alias) or alias in seen:
            raise ValueError("RULE_INSTANCE_ID_INVALID")
        seen.add(alias)
        key = item["id"]
        if not isinstance(key, str) or key not in catalog or item["version"] != catalog[key]["version"]:
            raise ValueError("RULE_INSTANCE_VERSION_INVALID")
        params = item["params"]
        if not isinstance(params, dict):
            raise ValueError("RULE_INSTANCE_PARAMETERS_INVALID")
        defaults = catalog[key]["params"]
        if set(params) - set(defaults):
            raise ValueError("RULE_INSTANCE_PARAMETER_UNSUPPORTED")
        merged = {**defaults, **params}
        for name, value in merged.items():
            if name == "window" and key in WINDOW_OVERRIDES:
                v2._integer(value, *WINDOW_OVERRIDES[key], name)
            elif type(value) is not type(defaults[name]) or value != defaults[name]:
                raise ValueError("RULE_INSTANCE_PARAMETER_UNSUPPORTED")
        spec, identity = registry.resolve_instance(key, version=item["version"], params=merged)
        warmup = merged["window"] + (key == "ROLLING_VOLATILITY") if key in WINDOW_OVERRIDES else spec.warmup_bars
        resolved.append({"instance_id": alias, "id": key, "version": item["version"], "params": merged,
                         "outputs": list(spec.outputs), "inputs": list(spec.inputs),
                         "extra_data": list(spec.requires_extra_data), "warmup_bars": int(warmup),
                         "instance_identity": identity.as_string()})
    return sorted(resolved, key=lambda item: item["instance_id"])


def _exits(config: dict) -> dict:
    if not isinstance(config, dict) or set(config) != EXIT_FIELDS or config["execution_mode"] != EXECUTION_MODE:
        raise ValueError("RULE_EXIT_FIELDS_OR_EXECUTION")
    values = {key: config[key] for key in EXIT_FIELDS - {"execution_mode"}}
    for value in values.values():
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError("RULE_EXIT_PARAMETER_INVALID")
    DailyExitRuleSetV2(**values)
    return {"execution_mode": EXECUTION_MODE, **{key: None if value is None else float(value) for key, value in values.items()}}


def validate_rule_payload(payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) != v2.PAYLOAD_FIELDS | {"indicator_instances", "exits"} or payload["version"] != CAPABILITY:
        raise ValueError("RULE_PAYLOAD_FIELDS_OR_VERSION")
    instances = _instances(payload["indicator_instances"])
    catalog = {item["instance_id"]: item for item in instances}
    result = deepcopy(payload)
    for key in ("hypothesis", "change_reason"):
        if not isinstance(payload[key], str) or not payload[key].strip() or len(payload[key]) > 4000:
            raise ValueError("RULE_DESCRIPTION_INVALID")
    for key in ("buy", "sell", "market_filter"):
        if key == "market_filter" and payload[key] is None:
            continue
        result[key] = v2._parse(payload[key], catalog, boolean=True, market=key == "market_filter").to_dict()
    minimum = v2._integer(payload["min_hold_sessions"], 0, 252, "min_hold_sessions")
    v2._integer(payload["max_hold_sessions"], max(1, minimum), 252, "max_hold_sessions")
    v2._integer(payload["cooldown_sessions"], 0, 60, "cooldown_sessions")
    weight = payload["target_weight"]
    if type(weight) not in (int, float) or not math.isfinite(weight) or not .01 <= weight <= 1:
        raise ValueError("RULE_TARGET_WEIGHT_INVALID")
    result["target_weight"] = float(weight)
    result["indicator_instances"] = [{key: item[key] for key in ("instance_id", "id", "version", "params")} for item in instances]
    result["exits"] = _exits(payload["exits"])
    return result


def _warmup(expr, instances):
    if expr.op == "indicator":
        return instances[expr.args[0]]["warmup_bars"]
    needed = max((_warmup(child, instances) for child in expr.args if isinstance(child, v2.Expr)), default=1)
    return needed + (expr.params["periods"] if expr.op == "ref" else 1 if expr.op in {"cross_up", "cross_down"} else 0)


class ResearchRuleStrategyV3(v2.ResearchRuleStrategyV2):
    source_files = (str(Path(__file__).resolve()), *v2.ResearchRuleStrategyV2.source_files, str(Path(v2.__file__).resolve()),
                    str(Path(__file__).resolve().parents[1] / "engine/daily_exit_v2.py"),
                    str(Path(__file__).resolve().parents[1] / "engine/daily_exit_v1.py"))

    def __init__(self, payload: dict, *, strategy_id: str):
        if not isinstance(strategy_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", strategy_id):
            raise ValueError("RULE_STRATEGY_ID_INVALID")
        self.strategy_id = strategy_id
        self.payload = validate_rule_payload(payload)
        instances = {item["instance_id"]: item for item in _instances(self.payload["indicator_instances"])}
        self.expressions = {key: v2._parse(value, instances, boolean=True, market=key == "market_filter")
                            for key in ("buy", "sell", "market_filter") if (value := self.payload[key]) is not None}
        self.references = sorted({ref for expr in self.expressions.values() for ref in v2._references(expr)})
        used = {key for key, _ in self.references}
        if used != set(instances):
            raise ValueError("RULE_UNUSED_INSTANCE")
        fields = {name for expr in self.expressions.values() for name in v2._field_references(expr)}
        for item in instances.values():
            fields.update(item["inputs"])
            fields.update("turn" if name == "vendor_turn" else name for name in item["extra_data"])
        # 注册器需要 close 作为共同时间索引；它也是账户基本行情，而非伪造因子依赖。
        fields.add("close")
        self.requirements = Requirements("A_SHARE", "1D", "CAUSAL_HFQ_FEATURE_RAW_EXECUTION", tuple(sorted(fields)),
                                        max(_warmup(expr, instances) for expr in self.expressions.values()),
                                        ("TARGET_WEIGHT",), capabilities=(CAPABILITY, "EXECUTION_STATE", "PERSONAL_CASH_DIVIDEND"))
        self.exit_policy = exit_policy()
        self.exit_rules = DailyExitRuleSetV2(**{key: value for key, value in self.payload["exits"].items() if key != "execution_mode"})
        rule = {key: value for key, value in self.payload.items() if key not in {"hypothesis", "change_reason"}}
        catalog_hash = rule_capabilities()["catalog_sha256"]
        self.rule_identity = stable_hash({"rule": rule, "catalog_sha256": catalog_hash, "exit_policy": self.exit_policy})
        self.definition = {"strategy_id": strategy_id, "catalog_sha256": catalog_hash, "rule_identity": self.rule_identity,
                           "rule": rule, "indicators": list(instances.values()), "exit_policy": self.exit_policy}
        self.parameters = {"rule_definition": deepcopy(self.definition), "candidate_payload": deepcopy(self.payload),
                           "rule_identity": self.rule_identity, "factor_ids": sorted({item["id"] for item in instances.values()})}

    def validate(self):
        super().validate()
        expected = type(self)(self.payload, strategy_id=self.strategy_id)
        if self.requirements != expected.requirements or self.exit_rules != expected.exit_rules or self.exit_policy != expected.exit_policy:
            raise ValueError("RULE_CHANGED_AFTER_FREEZE")

    def build_feature_matrix(self, bars: pd.DataFrame, vendor_turn: pd.Series | None = None) -> pd.DataFrame:
        required = set(self.requirements.fields) - {"turn"}
        if bars.empty or not required <= set(bars) or bars.index.has_duplicates or not bars.index.is_monotonic_increasing:
            raise ValueError("RULE_FEATURE_BARS_INVALID")
        series = {name: pd.to_numeric(bars[name], errors="raise").astype(float) for name in v2.FIELDS if name in bars}
        matrix = pd.DataFrame(series, index=bars.index)
        registry = v2.pilot_registry()
        for item in self.definition["indicators"]:
            result = registry.compute(item["id"], series["close"], version=item["version"],
                                      high=series.get("high"), low=series.get("low"), open_=series.get("open"),
                                      volume=series.get("volume"), amount=series.get("amount"), prev_close=series.get("prev_close"),
                                      params=item["params"], extra_data={"vendor_turn": vendor_turn} if vendor_turn is not None else {})
            for key, output in self.references:
                if key == item["instance_id"]:
                    column = f"{key}.{output}"
                    matrix[column] = result.output(output)
                    matrix[f"{column}__ready"] = result.ready()
        return matrix

    def on_close(self, context):
        """通用调用不能把仅信号评估误报为包含风险退出的完整策略。"""
        if any(value is not None for key, value in self.payload["exits"].items() if key != "execution_mode"):
            raise ValueError("RULE_RISK_EXIT_BACKEND_REQUIRED")
        return self.on_signal_close(context)

    def on_signal_close(self, context):
        """供已接线账户后端使用；后端另以真实账本评估 exit_rules 并合并意图。"""
        return super().on_close(context)
