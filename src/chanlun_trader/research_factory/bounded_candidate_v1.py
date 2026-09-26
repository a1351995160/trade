"""受限研究候选：模型只选择冻结指标与投票门槛，不提供可执行代码。"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import math
from pathlib import Path
import re

import pandas as pd

from .common import stable_hash
from .strategy_interface_v1 import Context, Decision, TargetWeight
from scripts.s1_causal_price_strategy_v1 import Causal51AccountBackend, Causal51VoteStrategy
from scripts.probe_all_indicator_strategy_v1 import SYMBOLS, START_DATE, END_DATE, ACCOUNT_START_DATE


CAPABILITY = "BOUNDED_INDICATOR_VOTE_V1"
_FIELDS = {"hypothesis", "indicators", "threshold", "change_reason"}


def candidate_capabilities() -> dict:
    definition = Causal51VoteStrategy().definition
    return {
        "capability": CAPABILITY,
        "fields": sorted(_FIELDS),
        "indicators": deepcopy(definition["indicators"]),
        "rule": "所选指标的首个注册输出较前一日上升记一票；全部所选指标就绪后，达到门槛买入，否则卖出。",
        "threshold": "整数，1 到所选指标数；指标不得重复，顺序不影响规则。",
        "scope": {"symbols": list(SYMBOLS), "frequency": "1D",
                  "feature_start": START_DATE, "feature_end": END_DATE,
                  "account_start": ACCOUNT_START_DATE, "account_end": 20240731,
                  "price_view": "CAUSAL_HFQ_FEATURE_RAW_EXECUTION",
                  "execution": "NEXT_SESSION_OPEN", "target_weight_per_symbol": 0.5,
                  "initial_cash": 1_000_000, "account_model": "S1_PERSONAL_CASH_DIVIDEND",
                  "calendar": "冻结输入中两证券共同且完整的交易日日历",
                  "costs": "复用冻结 S1 账户引擎；候选不得修改"},
        "qualification": "EXPLORATORY_ONLY",
    }


def _validated_payload(payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise ValueError("BOUNDED_CANDIDATE_FIELDS_INVALID")
    for field in ("hypothesis", "change_reason"):
        if not isinstance(payload[field], str) or not payload[field].strip() or len(payload[field]) > 4000:
            raise ValueError(f"BOUNDED_CANDIDATE_TEXT_INVALID:{field}")
    ids = payload["indicators"]
    supported = {item["id"] for item in Causal51VoteStrategy().definition["indicators"]}
    if (not isinstance(ids, list) or not ids or any(not isinstance(key, str) for key in ids)
            or len(ids) != len(set(ids)) or not set(ids) <= supported):
        raise ValueError("BOUNDED_CANDIDATE_INDICATORS_UNSUPPORTED")
    threshold = payload["threshold"]
    if type(threshold) is not int or not 1 <= threshold <= len(ids):
        raise ValueError("BOUNDED_CANDIDATE_THRESHOLD_INVALID")
    return {**payload, "indicators": sorted(ids)}


def validate_candidate(payload: dict, *, strategy_id: str):
    """调用方绑定候选身份；模型不能指定身份、父版本、输入或账户选项。"""
    return BoundedVoteStrategy(payload, strategy_id=strategy_id)


class BoundedVoteStrategy(Causal51VoteStrategy):
    requirements = replace(Causal51VoteStrategy.requirements, capabilities=(
        CAPABILITY, "EXECUTION_STATE", "PERSONAL_CASH_DIVIDEND"))
    source_files = (*Causal51VoteStrategy.source_files,
                    str(Path(__file__).resolve()),
                    *(str(Path(__file__).resolve().parents[1] / "engine" / name) for name in (
                        "engine.py", "broker.py", "fee.py", "fill.py", "ledger.py", "order_manager.py",
                        "risk.py", "security_state.py", "sizing.py", "slippage.py", "time_types.py")),
                    str(Path(__file__).resolve().parents[3] / "scripts/s1_causal_price_strategy_v1.py"),
                    str(Path(__file__).resolve().parents[3] / "scripts/s1_public_entry_strategy_v1.py"))

    def __init__(self, payload: dict, *, strategy_id: str):
        if not isinstance(strategy_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", strategy_id):
            raise ValueError("BOUNDED_CANDIDATE_ID_INVALID")
        super().__init__()
        self.strategy_id = strategy_id
        self.payload = _validated_payload(payload)
        rule = {"capability": CAPABILITY, "catalog_sha256": self.definition["catalog_sha256"],
                "indicators": self.payload["indicators"], "threshold": self.payload["threshold"],
                "price_view": self.requirements.price_view, "target_weight_per_symbol": 0.5}
        self.rule_identity = stable_hash(rule)
        self.definition.update(strategy_id=strategy_id, execution_strategy_id=strategy_id, rule_identity=self.rule_identity,
                               rule=deepcopy(rule))
        self.parameters.update(rule_definition=deepcopy(self.definition),
                               candidate_payload=deepcopy(self.payload),
                               rule_identity=self.rule_identity,
                               factor_ids=list(self.payload["indicators"]))

    def validate(self):
        expected = type(self)(self.payload, strategy_id=self.strategy_id)
        if (self.parameters != expected.parameters or self.definition != expected.definition
                or self.rule_identity != expected.rule_identity):
            raise ValueError("BOUNDED_CANDIDATE_RULE_CHANGED")

    def on_close(self, context: Context) -> Decision:
        history = context.history
        if (not isinstance(history, pd.DataFrame) or context.index < 0
                or context.index >= len(context.calendar) or len(history) != context.index + 1
                or tuple(map(int, history.index)) != context.calendar[:context.index + 1]):
            raise ValueError("STRATEGY_HISTORY_NOT_PREFIX_ONLY")
        required = {f"{key}__{kind}" for key in self.payload["indicators"] for kind in ("value", "ready")}
        if not required <= set(history.columns):
            raise ValueError("BOUNDED_CANDIDATE_INDICATORS_MISSING")
        if context.index == 0:
            return Decision("SELECTED_INDICATORS_NOT_READY")
        current, previous = history.iloc[-1], history.iloc[-2]
        votes = 0
        for key in self.payload["indicators"]:
            if not (current[f"{key}__ready"] == True and previous[f"{key}__ready"] == True):
                return Decision("SELECTED_INDICATORS_NOT_READY")
            now, before = float(current[f"{key}__value"]), float(previous[f"{key}__value"])
            if not (math.isfinite(now) and math.isfinite(before)):
                return Decision("SELECTED_INDICATORS_NOT_READY")
            votes += now > before
        buy = votes >= self.payload["threshold"]
        return Decision("BUY" if buy else "SELL", TargetWeight(0.5 if buy else 0.0),
                        metadata={"rising_votes": int(votes)})


class BoundedCandidateAccountBackend(Causal51AccountBackend):
    def check(self, requirements):
        if requirements != BoundedVoteStrategy.requirements:
            raise ValueError("UNSUPPORTED_BOUNDED_CANDIDATE_REQUIREMENTS")

    def validate_strategy(self, strategy):
        if type(strategy) is not BoundedVoteStrategy:
            raise ValueError("UNSUPPORTED_BOUNDED_CANDIDATE_STRATEGY")

    def describe(self) -> dict:
        description = super().describe()
        description["backend"] = "BOUNDED_CANDIDATE_S1_ACCOUNT_V1"
        description["source_hashes"][str(Path(__file__).resolve())] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        description["corporate_events_identity"] = stable_hash(self.events)
        return description

    def run(self, strategy, bundle, actions, guard):
        if stable_hash(actions) != stable_hash(self.events):
            raise ValueError("BOUNDED_CANDIDATE_ACTIONS_CHANGED")
        return super().run(strategy, bundle, actions, guard)
