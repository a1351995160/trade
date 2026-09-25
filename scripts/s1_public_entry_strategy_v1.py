"""Frozen 51-vote rule adapter for the common strategy entry diagnostic."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.probe_all_indicator_strategy_v1 import (
    ACCOUNT_START_DATE, BUY_VOTES, END_DATE, INDICATOR_COUNT, START_DATE,
    STRATEGY_ID, SYMBOLS, pilot_registry, run_chain, strategy_definition,
)
from scripts.verify_fixed_strategy_state_v1 import _state_eligible
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.strategy_interface_v1 import (
    Context, Decision, Requirements, TargetWeight, validate_decision,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    ROOT / "scripts/probe_all_indicator_strategy_v1.py",
    ROOT / "scripts/verify_fixed_strategy_state_v1.py",
    ROOT / "src/chanlun_trader/engine/indicator_registry_v2.py",
    ROOT / "src/chanlun_trader/engine/indicators_v2.py",
    ROOT / "src/chanlun_trader/engine/custom_indicators_v2.py",
    ROOT / "src/chanlun_trader/engine/individual_dividend_accounting_v1.py",
)


def _frame_hash(frame: pd.DataFrame) -> str:
    material = {"columns": list(frame.columns),
                "dtypes": [str(dtype) for dtype in frame.dtypes],
                "rows": pd.util.hash_pandas_object(frame, index=True).astype("uint64").tolist()}
    return stable_hash(material)


def input_identity(bundle: dict, events: tuple[dict, ...], catalog_hash: str) -> str:
    return stable_hash({
        "scope": [START_DATE, END_DATE, ACCOUNT_START_DATE, bundle["account_end_date"], list(SYMBOLS)],
        "catalog_sha256": catalog_hash,
        "source_hashes": bundle["source_hashes"],
        "frames": {name: _frame_hash(bundle[name]) for name in
                   ("daily", "turn", "states", "historical")},
        "corporate_events": events,
    })


class Fixed51VoteStrategy:
    strategy_id = STRATEGY_ID
    source_files = tuple(str(path) for path in SOURCE_FILES)
    requirements = Requirements(
        asset="A_SHARE", frequency="1D", price_view="RAW",
        fields=("open", "high", "low", "close", "volume", "amount", "prev_close", "turn"),
        warmup_sessions=60, intents=("TARGET_WEIGHT",),
        capabilities=("FIXED_51_VOTE", "EXECUTION_STATE", "PERSONAL_CASH_DIVIDEND"),
    )

    def __init__(self):
        self.definition = strategy_definition(pilot_registry())
        self.parameters = {
            "rule_definition": self.definition,
            "account_start_date": ACCOUNT_START_DATE,
            "account_end_date": 20240731,
            "target_weight_per_symbol": 0.5,
            "turnover_source": "BaoStock.daily.turn.percent",
        }

    def validate(self):
        expected = strategy_definition(pilot_registry())
        parameters = {
            "rule_definition": expected,
            "account_start_date": ACCOUNT_START_DATE,
            "account_end_date": 20240731,
            "target_weight_per_symbol": 0.5,
            "turnover_source": "BaoStock.daily.turn.percent",
        }
        if self.definition != expected or self.parameters != parameters:
            raise ValueError("FROZEN_51_RULE_CHANGED")

    def on_close(self, context: Context) -> Decision:
        history = context.history
        if (not isinstance(history, pd.DataFrame) or len(history) != context.index + 1
                or context.calendar[context.index] != int(history.index[-1])):
            raise ValueError("STRATEGY_HISTORY_NOT_PREFIX_ONLY")
        expected = {f"{item['id']}__{kind}" for item in self.definition["indicators"]
                    for kind in ("value", "ready")}
        if set(history.columns) != expected:
            raise ValueError("ALL_INDICATORS_REQUIRED_FOR_DECISION")
        if context.index == 0:
            return Decision("ALL_51_NOT_READY")
        current, previous = history.iloc[-1], history.iloc[-2]
        votes = 0
        for item in self.definition["indicators"]:
            key = item["id"]
            if not (current[f"{key}__ready"] and previous[f"{key}__ready"]):
                return Decision("ALL_51_NOT_READY")
            now, before = float(current[f"{key}__value"]), float(previous[f"{key}__value"])
            if not (math.isfinite(now) and math.isfinite(before)):
                return Decision("ALL_51_NOT_READY")
            votes += now > before
        side = "BUY" if votes >= BUY_VOTES else "SELL"
        return Decision(side, TargetWeight(0.5 if side == "BUY" else 0.0),
                        metadata={"rising_votes": int(votes)})


class Fixed51AccountBackend:
    """Compute all votes independently; reuse the frozen account engine assembly."""

    def check(self, requirements: Requirements):
        if requirements != Fixed51VoteStrategy.requirements:
            raise ValueError("UNSUPPORTED_FIXED_51_REQUIREMENTS")

    def describe(self) -> dict:
        paths = (Path(__file__), *SOURCE_FILES)
        return {"backend": "S1_FIXED_51_PUBLIC_ENTRY_ACCOUNT_V1",
                "source_hashes": {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in paths}}

    def validate_strategy(self, strategy):
        if not isinstance(strategy, Fixed51VoteStrategy):
            raise ValueError("UNSUPPORTED_FIXED_51_STRATEGY")

    def _matrix(self, bars: pd.DataFrame, vendor: pd.DataFrame, definition: dict) -> pd.DataFrame:
        if (bars.empty or bars["date"].duplicated().any()
                or not bars["adjustflag"].astype(str).eq("3").all()):
            raise ValueError("PUBLIC_ENTRY_RAW_DAILY_INVALID")
        index = pd.Index(bars["date"].astype(int).to_numpy(), name="date")
        matching = vendor.set_index("date")
        if (matching.index.has_duplicates or set(matching.index.astype(int)) != set(index)
                or not matching["tradestatus"].eq(1).all()):
            raise ValueError("PUBLIC_ENTRY_TURN_COVERAGE_INVALID")
        matching = matching.reindex(index)
        series = {name: pd.Series(bars[name].to_numpy(dtype=float), index=index)
                  for name in ("open", "high", "low", "close", "volume", "amount", "prev_close")}
        if (not np.isfinite(matching["turn"].to_numpy(dtype=float)).all()
                or not matching["turn"].ge(0).all()
                or not np.allclose(matching["volume"].to_numpy(dtype=float),
                                   series["volume"].to_numpy(dtype=float), rtol=0, atol=1e-6)):
            raise ValueError("PUBLIC_ENTRY_TURN_CONTENT_INVALID")
        turn = pd.Series(matching["turn"].to_numpy(dtype=float), index=index)
        registry = pilot_registry()
        columns = {}
        for item in definition["indicators"]:
            result = registry.compute(
                item["id"], series["close"], version=item["version"],
                high=series["high"], low=series["low"], open_=series["open"],
                volume=series["volume"], amount=series["amount"],
                prev_close=series["prev_close"], params=item["params"],
                extra_data={"vendor_turn": turn},
            )
            columns[f"{item['id']}__value"] = result.output(item["primary_output"])
            columns[f"{item['id']}__ready"] = result.ready()
        if len(columns) != INDICATOR_COUNT * 2:
            raise ValueError("ALL_INDICATORS_REQUIRED_FOR_DECISION")
        return pd.DataFrame(columns, index=index)

    def run(self, strategy: Fixed51VoteStrategy, bundle: dict,
            actions: tuple[dict, ...], guard) -> dict:
        guard()
        if input_identity(bundle, actions, strategy.definition["catalog_sha256"]) != bundle["input_identity"]:
            raise PermissionError("PUBLIC_ENTRY_INPUT_CONTENT_CHANGED")
        daily, turn = bundle["daily"], bundle["turn"]
        states, historical = bundle["states"], bundle["historical"]
        if (bundle["account_end_date"] != strategy.parameters["account_end_date"]
                or set(daily.symbol) != set(SYMBOLS) or set(turn.symbol) != set(SYMBOLS)
                or set(states.symbol) != set(SYMBOLS) or set(historical.symbol) != set(SYMBOLS)):
            raise ValueError("PUBLIC_ENTRY_SYMBOL_SCOPE_CHANGED")
        full_days = sorted(int(day) for day in daily.date.unique())
        if full_days[0] != START_DATE or full_days[-1] != END_DATE:
            raise ValueError("PUBLIC_ENTRY_DAILY_SCOPE_CHANGED")
        previous_day = {full_days[i]: full_days[i - 1] for i in range(1, len(full_days))}
        prior_states = historical.set_index(["symbol", "trade_date"])
        decisions, submitted, rejected = {}, {}, []
        for symbol in SYMBOLS:
            guard()
            bars = daily.loc[daily.symbol == symbol].sort_values("date")
            vendor = turn.loc[turn.symbol == symbol].sort_values("date")
            matrix = self._matrix(bars, vendor, strategy.definition)
            calendar = tuple(int(day) for day in matrix.index)
            if calendar != tuple(full_days):
                raise ValueError("PUBLIC_ENTRY_DAILY_COVERAGE_INVALID")
            decisions[symbol], submitted[symbol] = [], []
            for i, day in enumerate(calendar):
                decision = strategy.on_close(Context(
                    history=matrix.iloc[:i + 1].copy(), calendar=calendar, index=i,
                    account={}, state={},
                ))
                validate_decision(decision, strategy.requirements)
                if decision.intent is None:
                    continue
                if (decision.reason not in ("BUY", "SELL")
                        or not isinstance(decision.intent, TargetWeight)
                        or decision.intent.weight != (0.5 if decision.reason == "BUY" else 0.0)
                        or not isinstance(decision.metadata, dict)
                        or type(decision.metadata.get("rising_votes")) is not int):
                    raise ValueError("PUBLIC_ENTRY_DECISION_INTENT_CONFLICT")
                item = {"date": day, "rising_votes": decision.metadata["rising_votes"],
                        "decision_at_close": decision.reason}
                decisions[symbol].append(item)
                if not ACCOUNT_START_DATE <= day <= bundle["account_end_date"]:
                    continue
                if decision.reason == "BUY":
                    key = (symbol, previous_day[day])
                    state = prior_states.loc[key] if key in prior_states.index else None
                    cutoff = pd.Timestamp(str(day), tz="Asia/Shanghai") + pd.Timedelta(hours=15, minutes=30)
                    if (state is None or not _state_eligible(state)
                            or pd.Timestamp(state["available_at"]) > cutoff):
                        rejected.append({"symbol": symbol, "signal_date": day,
                                         "reason": "PRIOR_STATE_NOT_KNOWN_ELIGIBLE"})
                        continue
                submitted[symbol].append(item)
        hashes = bundle["source_hashes"]
        chain = run_chain(
            daily, submitted, strategy.definition, hashes["daily_sha256"],
            hashes["turn_sha256"], hashes["states_sha256"],
            hashes["corporate_actions_sha256"], execution_states=states,
            historical_states_hash=hashes["historical_states_sha256"],
            account_end_date=bundle["account_end_date"], corporate_events=actions,
        )
        return {"status": chain["status"], "decisions": decisions,
                "submitted_decisions": submitted, "decision_state_rejections": rejected,
                "chain": chain, "fills": chain["trades"],
                "ledger": chain["independent_account_checks"]}
