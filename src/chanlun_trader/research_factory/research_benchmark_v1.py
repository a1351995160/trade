"""全池一次建仓买入持有基准，复用公共账户、撮合与核账。"""
from copy import deepcopy
from pathlib import Path
import hashlib
import math
import re

import pandas as pd

from .common import stable_hash
from .rule_account_backend_v2 import RuleAccountBackendV2
from .strategy_interface_v1 import Context, Decision, Requirements, TargetWeight

CAPABILITY = "FULL_POOL_BUY_HOLD_V1"


def benchmark_payload(symbols):
    if (not isinstance(symbols, (list, tuple)) or not symbols or len(symbols) > 10000
            or any(not isinstance(s, str) or not re.fullmatch(r"(?:00[0-9]{4}\.SZ|60[0-9]{4}\.SH)", s) for s in symbols)
            or len(set(symbols)) != len(symbols)):
        raise ValueError("BENCHMARK_POOL_INVALID")
    return {"version": CAPABILITY, "symbols": sorted(symbols)}


class FullPoolBuyHoldStrategyV1:
    requirements = Requirements("A_SHARE", "1D", "CAUSAL_HFQ_FEATURE_RAW_EXECUTION", ("close",), 1,
                                ("TARGET_WEIGHT",), capabilities=(CAPABILITY, "EXECUTION_STATE", "PERSONAL_CASH_DIVIDEND"))
    source_files = (str(Path(__file__).resolve()),)

    def __init__(self, payload, *, strategy_id):
        if not isinstance(strategy_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", strategy_id):
            raise ValueError("BENCHMARK_STRATEGY_ID_INVALID")
        if not isinstance(payload, dict) or set(payload) != {"version", "symbols"} or payload["version"] != CAPABILITY:
            raise ValueError("BENCHMARK_PAYLOAD_INVALID")
        self.strategy_id = strategy_id
        self.payload = benchmark_payload(payload["symbols"])
        self.rule_identity = stable_hash(self.payload)
        self.definition = {"strategy_id": strategy_id, "rule_identity": self.rule_identity,
                           "rule": deepcopy(self.payload), "indicators": []}
        self.parameters = {"candidate_payload": deepcopy(self.payload), "rule_identity": self.rule_identity,
                           "target_weight": 1 / len(self.payload["symbols"]), "rebalance": False,
                           "first_decision": "FIRST_ACCOUNT_CLOSE", "retry_unfilled": False,
                           "exit_policy": "HOLD_TO_WINDOW_END_MARK_TO_MARKET"}

    def validate(self):
        expected = type(self)(self.payload, strategy_id=self.strategy_id)
        if self.rule_identity != expected.rule_identity or self.definition != expected.definition or self.parameters != expected.parameters:
            raise ValueError("BENCHMARK_CHANGED_AFTER_FREEZE")

    def build_feature_matrix(self, bars, vendor_turn=None):
        if (not isinstance(bars, pd.DataFrame) or bars.empty or "close" not in bars
                or bars.index.has_duplicates or not bars.index.is_monotonic_increasing):
            raise ValueError("BENCHMARK_BARS_INVALID")
        return pd.DataFrame({"close": pd.to_numeric(bars["close"], errors="raise").astype(float)}, index=bars.index)

    def on_close(self, context: Context):
        if (not isinstance(context.history, pd.DataFrame) or type(context.index) is not int
                or not 0 <= context.index < len(context.calendar) or len(context.history) != context.index + 1
                or tuple(context.history.index) != context.calendar[:context.index + 1]
                or tuple(sorted(set(context.calendar))) != context.calendar):
            raise ValueError("BENCHMARK_HISTORY_NOT_PREFIX_ONLY")
        old = context.state
        if not isinstance(old, dict):
            raise ValueError("BENCHMARK_STATE_INVALID")
        if old and (set(old) != {"rule_identity", "last_session_index", "attempted"}
                    or old["rule_identity"] != self.rule_identity or old["attempted"] is not True
                    or type(old["last_session_index"]) is not int or old["last_session_index"] != context.index - 1):
            raise ValueError("BENCHMARK_STATE_IDENTITY_OR_CONTINUITY")
        state = {"rule_identity": self.rule_identity, "last_session_index": context.index, "attempted": True}
        if old:
            return Decision("BENCHMARK_HOLD", state=state)
        close = float(context.history["close"].iloc[-1])
        if not math.isfinite(close) or close <= 0:
            raise ValueError("BENCHMARK_CLOSE_INVALID")
        if not isinstance(context.account, dict) or context.account.get("quantity") != 0:
            raise ValueError("BENCHMARK_INITIAL_ACCOUNT_NOT_FLAT")
        return Decision("BENCHMARK_INITIAL_BUY", TargetWeight(1 / len(self.payload["symbols"]), increase_existing=False), state)


class FullPoolBuyHoldBackendV1(RuleAccountBackendV2):
    def __init__(self, window, costs="BASE", initial_cash=1_000_000, execution_profile="HISTORICAL_MODELED"):
        pool = benchmark_payload(window.get("symbols"))["symbols"]
        super().__init__(window, costs, initial_cash, max_positions=len(pool),
                         max_symbol_exposure_bps=max(1, 10000 // len(pool)), execution_profile=execution_profile)

    def check(self, requirements):
        if requirements != FullPoolBuyHoldStrategyV1.requirements:
            raise ValueError("BENCHMARK_REQUIREMENTS_UNSUPPORTED")

    def validate_strategy(self, strategy):
        if type(strategy) is not FullPoolBuyHoldStrategyV1 or strategy.payload["symbols"] != self.window["symbols"]:
            raise ValueError("BENCHMARK_STRATEGY_POOL_CONFLICT")
        strategy.validate()

    def describe(self):
        result = super().describe()
        result.update(backend=CAPABILITY, allocation="FULL_POOL_EQUAL_INITIAL_TARGET_NO_REBALANCE",
                      retry_unfilled=False, terminal_liquidation=False,
                      initial_budget_per_symbol=self.initial_cash / len(self.window["symbols"]),
                      rounding="BOARD_LOTS_AND_FEES_MAY_REDUCE_ACTUAL_COVERAGE",
                      size_policy="EXISTING_ACCOUNT_EQUITY_AND_CASH_LIMITS_AT_FIRST_OPEN")
        source = Path(__file__).resolve()
        result["source_hashes"][str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
        return result

    def run(self, strategy, bundle, actions, guard):
        result = super().run(strategy, bundle, actions, guard)
        fills = result["fills"]
        if any(trade["side"] != "BUY" for trade in fills):
            raise ValueError("BENCHMARK_UNEXPECTED_SELL")
        purchased = sorted({trade["symbol"] for trade in fills})
        requested = self.window["symbols"]
        result["benchmark"] = {"version": CAPABILITY, "requested_symbols": list(requested),
            "purchased_symbols": purchased, "unfilled_symbols": sorted(set(requested) - set(purchased)),
            "coverage": len(purchased) / len(requested), "initial_cash": self.initial_cash,
            "initial_budget_per_symbol": self.initial_cash / len(requested),
            "max_positions": self.max_positions, "target_weight": 1 / len(requested),
            "rebalance": False, "retry_unfilled": False, "terminal_liquidation": False,
            "final_cash": result["daily_accounts"][-1]["cash"],
            "execution_skips": deepcopy(result["final_account_checkpoint"]["skipped_intents"]),
            "limitations": ["全池等权初始目标；整手、费用、可交易状态及账户风控会缩小实际覆盖。",
                            "首个账户收盘发信号，次日尝试建仓；未成交不补买，分红留现金，不再均衡。",
                            "委托数量按公共引擎当时权益与现金约束缩量，成交金额不承诺逐股完全相等。",
                            "期末按市值计价，不强制卖出；股票池与策略一致，但持仓数不限制为策略的两只。"]}
        return result
