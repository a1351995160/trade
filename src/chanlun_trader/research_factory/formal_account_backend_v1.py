"""冻结任意窗口的公共账户后端；复用 S1 引擎与逐日独立核账。"""
from copy import deepcopy
from collections import OrderedDict
from pathlib import Path
import hashlib
import re

import numpy as np
import pandas as pd

from .bounded_candidate_v1 import BoundedVoteStrategy, validate_candidate
from .common import stable_hash
from .strategy_interface_v1 import Context, Decision, TargetWeight, prepare, run, validate_decision
from scripts.s1_causal_price_strategy_v1 import Causal51AccountBackend
from scripts.s1_public_entry_strategy_v1 import _frame_hash
from scripts.probe_all_indicator_strategy_v1 import run_chain


# 仅缓存已完成确定性重放的完整结果身份；每次仍重新执行独立现金/持仓核验。
_RESULT_REPLAY_CACHE = OrderedDict()


BASE_COSTS = {"commission_rate": 0.00025, "min_commission": 5.0,
              "stamp_tax_rate": 0.0005, "slippage_bps": 0.001}
STRESS_COSTS = {**BASE_COSTS, "commission_rate": 0.0005,
                "min_commission": 10.0, "slippage_bps": 0.002}


def normalized_costs(costs):
    if costs == "BASE":
        return dict(BASE_COSTS)
    if costs == "STRESS":
        return dict(STRESS_COSTS)
    if not isinstance(costs, dict) or costs not in (BASE_COSTS, STRESS_COSTS):
        raise ValueError("FORMAL_COST_SCENARIO_UNSUPPORTED")
    return dict(costs)


def normalized_window(window):
    required = {"symbols", "feature_start", "account_start", "account_end", "calendar"}
    if not isinstance(window, dict) or set(window) != required:
        raise ValueError("FORMAL_WINDOW_FIELDS_INVALID")
    value = deepcopy(window)
    symbols = value["symbols"]
    if (not isinstance(symbols, (list, tuple)) or len(symbols) != 2
            or len(set(symbols)) != 2 or any(not isinstance(s, str) or not re.fullmatch(
                r"(?:00[0-9]{4}\.SZ|60[0-9]{4}\.SH)", s) for s in symbols)):
        raise ValueError("FORMAL_WINDOW_REQUIRES_TWO_MAIN_BOARD_SYMBOLS")
    days = value["calendar"]
    if (not isinstance(days, (list, tuple)) or not days
            or any(type(d) is not int for d in days) or list(days) != sorted(set(days))):
        raise ValueError("FORMAL_CALENDAR_INVALID")
    for day in days:
        if pd.Timestamp(str(day)).strftime("%Y%m%d") != str(day):
            raise ValueError("FORMAL_CALENDAR_INVALID")
    if (any(type(value[k]) is not int for k in ("feature_start", "account_start", "account_end"))
            or value["feature_start"] != days[0] or value["account_end"] != days[-1]
            or value["account_start"] not in days
            or list(days).index(value["account_start"]) < 60
            or value["account_start"] >= value["account_end"]):
        raise ValueError("FORMAL_WINDOW_OR_WARMUP_INVALID")
    value["symbols"] = list(symbols)
    value["calendar"] = list(days)
    return value


def window_input_identity(bundle, window):
    """数据身份不含策略或压力成本；授权方必须先冻结这个身份。"""
    window = normalized_window(window)
    return stable_hash({"window": window,
        "frames": {key: _frame_hash(bundle[key]) for key in ("daily", "turn", "states")},
        "calendar": bundle["calendar"], "events": bundle["events"],
        "corporate_actions_complete": bundle["corporate_actions_complete"],
        "source_hashes": bundle["source_hashes"],
        "open_snapshots": bundle["open_snapshots"], "close_snapshots": bundle["close_snapshots"]})


class FormalWindowStrategyV1:
    requirements = BoundedVoteStrategy.requirements
    source_files = (*BoundedVoteStrategy.source_files, str(Path(__file__).resolve()))

    def __init__(self, proposal, *, strategy_id):
        if not isinstance(strategy_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", strategy_id):
            raise ValueError("FORMAL_STRATEGY_ID_INVALID")
        self.strategy_id = strategy_id
        self.proposal = deepcopy(proposal)
        self.delegate = None if proposal is None else validate_candidate(proposal, strategy_id=strategy_id)
        # 数据窗口只属于后端；不继承 S1 的固定日期参数。
        if self.delegate is None:
            catalog = BoundedVoteStrategy({"hypothesis": "基准", "indicators": ["MACD"],
                "threshold": 1, "change_reason": "冻结等权持有基准"}, strategy_id=strategy_id)
            self.definition = deepcopy(catalog.definition)
            self.rule_identity = stable_hash({"rule": "TWO_SYMBOL_BUY_AND_HOLD_V1",
                                              "target_weight_per_symbol": 0.5})
            rule = {"rule": "TWO_SYMBOL_BUY_AND_HOLD_V1", "target_weight_per_symbol": 0.5}
        else:
            self.definition = deepcopy(self.delegate.definition)
            self.rule_identity = self.delegate.rule_identity
            rule = deepcopy(self.definition["rule"])
        self.definition.update(rule_identity=self.rule_identity, execution_strategy_id=strategy_id, rule=rule)
        self.parameters = {"rule_identity": self.rule_identity, "rule_definition": rule,
                           "candidate_payload": self.proposal, "target_weight_per_symbol": 0.5}

    def validate(self):
        expected = type(self)(self.proposal, strategy_id=self.strategy_id)
        if (self.parameters != expected.parameters or self.definition != expected.definition
                or self.rule_identity != expected.rule_identity):
            raise ValueError("FORMAL_STRATEGY_CHANGED")
        if self.delegate is not None:
            self.delegate.validate()

    def on_close(self, context):
        if self.delegate is None:
            return Decision("BUY", TargetWeight(0.5), metadata={"rising_votes": 0})
        return self.delegate.on_close(context)


class FormalAccountBackendV1:
    def __init__(self, window, costs="BASE"):
        self.window = normalized_window(window)
        self.costs = normalized_costs(costs)

    def check(self, requirements):
        if requirements != FormalWindowStrategyV1.requirements:
            raise ValueError("FORMAL_REQUIREMENTS_UNSUPPORTED")

    def validate_strategy(self, strategy):
        if type(strategy) is not FormalWindowStrategyV1:
            raise ValueError("FORMAL_STRATEGY_UNSUPPORTED")

    def describe(self):
        paths = sorted(set(FormalWindowStrategyV1.source_files))
        return {"backend": "FORMAL_WINDOW_ACCOUNT_V1", "window": deepcopy(self.window),
                "costs": deepcopy(self.costs), "initial_cash": 1_000_000,
                "source_hashes": {str(Path(p).resolve()): hashlib.sha256(Path(p).read_bytes()).hexdigest()
                                  for p in paths}}

    def _validate_bundle(self, bundle):
        w = self.window
        if (list(bundle["calendar"]) != w["calendar"]
                or bundle["corporate_actions_complete"] is not True
                or not isinstance(bundle["events"], (list, tuple))
                or not isinstance(bundle["source_hashes"], dict) or not bundle["source_hashes"]):
            raise ValueError("FORMAL_INPUT_COVERAGE_INVALID")
        expected = {(s, d) for s in w["symbols"] for d in w["calendar"]}
        account_days = [d for d in w["calendar"] if d >= w["account_start"]]
        for name, date_col, keys in (("daily", "date", expected), ("turn", "date", expected),
                ("states", "trade_date", {(s, d) for s in w["symbols"] for d in account_days})):
            frame = bundle[name]
            if (frame.duplicated(["symbol", date_col]).any()
                    or set(zip(frame.symbol, frame[date_col])) != keys):
                raise ValueError(f"FORMAL_FRAME_COVERAGE_INVALID:{name}")
        daily, turn, states = bundle["daily"], bundle["turn"], bundle["states"]
        prices = daily[["open", "high", "low", "close", "prev_close"]].to_numpy(dtype=float)
        if (not np.isfinite(prices).all() or (prices <= 0).any()
                or not np.isfinite(daily[["volume", "amount"]].to_numpy(dtype=float)).all()
                or (daily[["volume", "amount"]] < 0).any().any()
                or (daily.high < daily[["open", "close", "low"]].max(axis=1)).any()
                or (daily.low > daily[["open", "close", "high"]].min(axis=1)).any()):
            raise ValueError("FORMAL_RAW_PRICES_INVALID")
        # 首版不伪造停牌日的指标输入；扩展前明确拒绝。
        if not turn.tradestatus.eq(1).all():
            raise ValueError("FORMAL_SUSPENDED_FEATURES_UNSUPPORTED")
        required = {"listed", "delisted", "universe_member", "eligibility_status", "st_status",
                    "suspension_status", "board"}
        if not required <= set(states):
            raise ValueError("FORMAL_STATE_FIELDS_MISSING")
        if (not states.board.isin(["SZ_MAIN", "SH_MAIN", "MAIN"]).all()
                or not states.suspension_status.eq("TRADING").all()
                or not states.st_status.isin(["NORMAL", "ST"]).all()
                or not states.eligibility_status.isin(["ELIGIBLE", "INELIGIBLE"]).all()
                or any(not states[k].map(lambda x: isinstance(x, (bool, np.bool_))).all()
                       for k in ("listed", "delisted", "universe_member"))):
            raise ValueError("FORMAL_EXECUTION_STATE_UNSUPPORTED")
        events = bundle["events"]
        ids = set()
        for event in events:
            if (event["event_id"] in ids or event["symbol"] not in w["symbols"]
                    or event["event_type"] != "CASH_DIVIDEND"
                    or event.get("units") != "CNY_PER_SHARE"
                    or not event.get("source")
                    or event.get("terms", {}).get("tax_rule", {}).get("kind") != "DEFERRED_INDIVIDUAL_2015_101"
                    or not event.get("terms", {}).get("tax_rule", {}).get("source")
                    or event["record_date"] not in w["calendar"]
                    or event["effective_date"] not in w["calendar"]
                    or event["payment_date"] != event["effective_date"]
                    or event["record_date"] < w["account_start"] <= event["effective_date"]):
                raise ValueError("FORMAL_CORPORATE_EVENT_UNSUPPORTED")
            ids.add(event["event_id"])

    def _observed_sessions(self, bundle):
        w = self.window
        account_days = [d for d in w["calendar"] if d >= w["account_start"]]
        grouped = {}
        for name, phase, expected in (("open_snapshots", "OPEN", account_days),
                                      ("close_snapshots", "CLOSE", w["calendar"])):
            snapshots = bundle[name]
            if len(snapshots) != len(expected) or [s["market_date"] for s in snapshots] != expected:
                raise ValueError("FORMAL_OBSERVED_SESSION_COVERAGE_INVALID")
            grouped[phase] = {s["market_date"]: s for s in snapshots}
            for snap in snapshots:
                ts = pd.Timestamp(snap["received_at"])
                if ts.tzinfo is None:
                    raise ValueError("FORMAL_OBSERVED_TIME_INVALID")
                ts = ts.tz_convert("Asia/Shanghai")
                minute = ts.hour * 60 + ts.minute
                if (snap["phase"] != phase or int(ts.strftime("%Y%m%d")) != snap["market_date"]
                        or not ((570 <= minute <= 575) if phase == "OPEN" else (900 <= minute <= 1020))):
                    raise ValueError("FORMAL_OBSERVED_TIME_INVALID")
                rows = snap["payload"]["bars"]
                if len(rows) != 2 or {r["symbol"] for r in rows} != set(w["symbols"]):
                    raise ValueError("FORMAL_OBSERVED_BAR_COVERAGE_INVALID")
                for row in rows:
                    if row["date"] != snap["market_date"]:
                        raise ValueError("FORMAL_OBSERVED_BAR_DATE_INVALID")
                    fields = ["open", "high", "low", "close", "prev_close", "volume", "amount"]
                    values = np.asarray([row[k] for k in fields], dtype=float)
                    if not np.isfinite(values).all() or (values[:5] <= 0).any() or (values[5:] < 0).any():
                        raise ValueError("FORMAL_OBSERVED_BAR_INVALID")
                    if phase == "OPEN":
                        if row.get("price_basis") != "OBSERVED_NOW" or len({row[k] for k in fields[:4]}) != 1:
                            raise ValueError("FORMAL_OPEN_PRICE_BASIS_INVALID")
                    else:
                        raw = bundle["daily"].loc[(bundle["daily"].symbol == row["symbol"]) &
                                                  (bundle["daily"].date == row["date"])].iloc[0]
                        if any(float(raw[k]) != float(row[k]) for k in fields):
                            raise ValueError("FORMAL_CLOSE_INPUT_CONFLICT")
        result = {}
        for day in account_days:
            opened, closed = grouped["OPEN"][day], grouped["CLOSE"][day]
            states = bundle["states"].loc[bundle["states"].trade_date == day]
            observed_states = {r["symbol"]: r for r in opened["payload"]["states"]}
            if set(observed_states) != set(w["symbols"]):
                raise ValueError("FORMAL_OPEN_STATE_COVERAGE_INVALID")
            for row in states.to_dict("records"):
                actual = observed_states[row["symbol"]]
                stamp = pd.Timestamp(row["available_at"])
                if stamp.tzinfo is None or stamp != pd.Timestamp(opened["received_at"]):
                    raise ValueError("FORMAL_OPEN_STATE_TIME_INVALID")
                if (any(row[k] != actual[k] for k in ("listed", "delisted", "board"))
                        or (row["st_status"] == "ST") != actual["is_st"]
                        or (row["suspension_status"] == "SUSPENDED") != actual["suspended"]):
                    raise ValueError("FORMAL_OPEN_STATE_CONFLICT")
            result[day] = {"open_at": opened["received_at"], "close_at": closed["received_at"],
                           "bars": deepcopy(opened["payload"]["bars"])}
        return result

    def run(self, strategy, bundle, actions, guard):
        guard()
        expected_identity = guard()["input_identity"]
        if window_input_identity(bundle, self.window) != expected_identity or stable_hash(actions) != stable_hash(bundle["events"]):
            raise PermissionError("FORMAL_INPUT_CHANGED")
        self._validate_bundle(bundle)
        observed_sessions = self._observed_sessions(bundle)
        daily = bundle["daily"]
        events = tuple(deepcopy(bundle["events"]))
        matrix_backend = Causal51AccountBackend(events)
        decisions = {}
        w = self.window
        calendar = tuple(w["calendar"])
        for symbol in w["symbols"]:
            guard()
            bars = daily.loc[daily.symbol == symbol].sort_values("date")
            vendor = bundle["turn"].loc[bundle["turn"].symbol == symbol].sort_values("date")
            matrix = matrix_backend._matrix(bars, vendor, strategy.definition)
            decisions[symbol] = []
            for i, day in enumerate(calendar):
                if day < w["account_start"]:
                    continue
                if strategy.delegate is None and day != w["account_start"]:
                    continue
                decision = strategy.on_close(Context(matrix.iloc[:i + 1].copy(), calendar, i, {}, {}))
                validate_decision(decision, strategy.requirements)
                if decision.intent is None:
                    raise ValueError(f"FORMAL_SELECTED_INDICATORS_NOT_READY:{symbol}:{day}")
                if not isinstance(decision.intent, TargetWeight) or decision.intent.weight not in (0, 0.5):
                    raise ValueError("FORMAL_TARGET_WEIGHT_UNSUPPORTED")
                decisions[symbol].append({"date": day, "decision_at_close": decision.reason,
                                         "rising_votes": decision.metadata["rising_votes"],
                                         "decision_at": observed_sessions[day]["close_at"]})
        guard()
        chain = run_chain(daily, decisions, strategy.definition,
            _frame_hash(daily), _frame_hash(bundle["turn"]), _frame_hash(bundle["states"]), stable_hash(events),
            execution_states=bundle["states"], account_start_date=w["account_start"],
            account_end_date=w["account_end"], symbols=tuple(w["symbols"]),
            account_calendar=tuple(d for d in calendar if d >= w["account_start"]), execution_costs=self.costs,
            observed_sessions=observed_sessions,
            corporate_events=tuple(e for e in events if e["record_date"] >= w["account_start"]))
        guard()
        if window_input_identity(bundle, self.window) != expected_identity:
            raise PermissionError("FORMAL_INPUT_CHANGED_DURING_RUN")
        if chain["status"] != "RECONCILED_DIAGNOSTIC":
            raise ValueError("FORMAL_ACCOUNT_RECONCILIATION_FAILED")
        checks = chain["independent_account_checks"]
        previous, peak, drawdown = 1_000_000.0, 1_000_000.0, 0.0
        daily_returns = []
        for item in checks:
            equity = item["equity"]
            daily_returns.append({"date": item["date"], "net_return": equity / previous - 1})
            peak = max(peak, equity)
            drawdown = min(drawdown, equity / peak - 1)
            previous = equity
        return {"status": "RECONCILED_DIAGNOSTIC", "chain": chain, "decisions": decisions,
                "rule_identity": strategy.rule_identity, "input_identity": expected_identity,
                "daily_returns": daily_returns, "fills": chain["trades"], "ledger": checks,
                "metrics": {"net_return": previous / 1_000_000 - 1, "max_drawdown": drawdown,
                            "total_fees": sum(t["fee"] for t in chain["trades"]),
                            "trade_count": len(chain["trades"])}}


def prepare_formal_account(proposal, *, strategy_id, window, costs="BASE"):
    return prepare(FormalWindowStrategyV1(proposal, strategy_id=strategy_id), FormalAccountBackendV1(window, costs))


def run_formal_account(proposal, *, strategy_id, bundle, window, costs="BASE", input_identity, active_check):
    strategy = FormalWindowStrategyV1(proposal, strategy_id=strategy_id)
    backend = FormalAccountBackendV1(window, costs)
    return run(strategy, backend, frame=bundle, actions=bundle["events"],
               input_identity=input_identity, active_check=active_check)


def validate_formal_result(result, *, bundle, window, costs="BASE"):
    """从成交事实复算保存的无公司行动报告，不运行策略或账户引擎。"""
    def require(condition, code):
        if not condition:
            raise ValueError("FORMAL_SAVED_RESULT_" + code)

    def equal(actual, expected, code, tolerance=1e-6):
        require(isinstance(actual, (int, float)) and not isinstance(actual, bool)
                and np.isfinite(actual) and abs(actual - expected) <= tolerance, code)

    backend = FormalAccountBackendV1(window, costs)
    backend._validate_bundle(bundle)
    observed = backend._observed_sessions(bundle)
    require(not bundle["events"], "CORPORATE_ACTIONS_UNSUPPORTED")
    w, fees = backend.window, backend.costs
    days = [d for d in w["calendar"] if d >= w["account_start"]]
    try:
        chain = result["chain"]
        require(result["input_identity"] == window_input_identity(bundle, w), "INPUT_IDENTITY")
        require(result["status"] == chain["status"] == "RECONCILED_DIAGNOSTIC" and chain["issues"] == [], "STATUS")
        require(chain["account_dates"] == [days[0], days[-1]] and chain["n_account_days"] == len(days), "CALENDAR")
        plan = result["strategy_plan"]
        strategy_id = plan["strategy"]["strategy_id"]
        proposal = plan["strategy"]["parameters"]["candidate_payload"]
        require(plan == prepare_formal_account(proposal, strategy_id=strategy_id, window=w, costs=fees), "PLAN")
        require(result["rule_identity"] == plan["strategy"]["parameters"]["rule_identity"], "RULE_IDENTITY")
        assumptions = chain["execution_assumptions"]
        for field, expected in (("initial_cash", 1_000_000), ("max_positions", 2),
                ("max_position_weight", .5), ("fee_commission_rate", fees["commission_rate"]),
                ("min_commission", fees["min_commission"]), ("sell_stamp_tax_rate", fees["stamp_tax_rate"]),
                ("slippage_fraction", fees["slippage_bps"])):
            equal(assumptions[field], expected, "COST_OR_ACCOUNT_POLICY")
        require(assumptions["fill_reference"] == "NEXT_SESSION_OBSERVED_NOW_AT_RECEIPT", "PRICE_BASIS")
        trades = chain["trades"]
        require(result["fills"] == trades, "FILL_COPIES")
        require(len({t["id"] for t in trades}) == len(trades), "DUPLICATE_TRADE")
        require(chain["n_trades"] == len(trades), "TRADE_COUNT")
        orders = {o["id"]: o for o in chain["orders"]}
        require(len(orders) == len(chain["orders"]) == chain["n_orders"], "ORDER_COUNT")
        for order in orders.values():
            require(order["symbol"] in w["symbols"] and order["side"] in ("BUY", "SELL"), "ORDER_SCOPE")
            created = pd.Timestamp(order["created_at"])
            require(created.tzinfo is not None, "ORDER_TIME")
            day = int(created.tz_convert("Asia/Shanghai").strftime("%Y%m%d"))
            require(day in days and created == pd.Timestamp(observed[day]["close_at"]), "ORDER_TIME")
            quantity = order["quantity"]
            # 原 broker 在实际成交前再次 sizing，明确拒单可被缩量为零。
            rejected_zero = (quantity == 0 and order["filled_quantity"] == 0
                             and order["status"] == "REJECTED"
                             and order["last_event_message"] == "SIZING_ZERO_AT_FILL")
            require(type(quantity) is int and ((quantity > 0 and quantity % 100 == 0) or rejected_zero),
                    "ORDER_QUANTITY")
        by_day = {day: [] for day in days}
        filled_quantities = {key: 0 for key in orders}
        for trade in trades:
            day, symbol, quantity, side = trade["date"], trade["symbol"], trade["quantity"], trade["side"]
            require(day in by_day and symbol in w["symbols"] and side in ("BUY", "SELL"), "TRADE_SCOPE")
            require(type(quantity) is int and quantity > 0 and quantity % 100 == 0, "TRADE_QUANTITY")
            require(trade["order_id"] in orders, "TRADE_ORDER_MISSING")
            order = orders[trade["order_id"]]
            require(order["symbol"] == symbol and order["side"] == side, "TRADE_ORDER_IDENTITY")
            fill_time = pd.Timestamp(trade["filled_at"])
            require(fill_time == pd.Timestamp(observed[day]["open_at"]), "FILL_TIME")
            created_day = int(pd.Timestamp(order["created_at"]).tz_convert("Asia/Shanghai").strftime("%Y%m%d"))
            require(days.index(day) == days.index(created_day) + 1, "NEXT_SESSION")
            row = next(r for r in observed[day]["bars"] if r["symbol"] == symbol)
            expected_price = round(float(row["open"]) * (1 + fees["slippage_bps"] if side == "BUY"
                                                         else 1 - fees["slippage_bps"]), 4)
            equal(trade["price"], expected_price, "FILL_PRICE")
            gross = expected_price * quantity
            expected_fee = round(max(gross * fees["commission_rate"], fees["min_commission"])
                                 + (gross * fees["stamp_tax_rate"] if side == "SELL" else 0), 4)
            equal(trade["gross_value"], gross, "GROSS_VALUE")
            equal(trade["fee"], expected_fee, "FEE")
            require(trade["reality_flag"] == "OK", "TRADE_REALITY")
            state = bundle["states"].loc[(bundle["states"].symbol == symbol) &
                                           (bundle["states"].trade_date == day)].iloc[0]
            require(bool(state.listed) and not bool(state.delisted) and state.suspension_status == "TRADING",
                    "EXECUTION_STATE")
            if side == "BUY":
                require(bool(state.universe_member) and state.st_status == "NORMAL"
                        and state.eligibility_status == "ELIGIBLE", "BUY_ELIGIBILITY")
            by_day[day].append(trade)
            filled_quantities[trade["order_id"]] += quantity
        for key, order in orders.items():
            require(filled_quantities[key] == order["filled_quantity"] <= order["quantity"], "ORDER_FILL_QUANTITY")
        cash, previous, peak, drawdown, total_fees = 1_000_000., 1_000_000., 1_000_000., 0., 0.
        quantities = dict.fromkeys(w["symbols"], 0)
        lots = {symbol: [] for symbol in w["symbols"]}
        checks = chain["independent_account_checks"]
        require([r["date"] for r in checks] == days and result["ledger"] == checks, "LEDGER_COVERAGE")
        require([r["date"] for r in result["daily_returns"]] == days, "RETURNS_COVERAGE")
        valuation = chain["official_valuation"]
        points = valuation["points"]
        require(valuation["event"] == "OBSERVED_AFTER_CLOSE" and [p["date"] for p in points] == days, "VALUATION_COVERAGE")
        for index, day in enumerate(days):
            for trade in sorted(by_day[day], key=lambda t: (t["filled_at"], t["id"])):
                symbol, quantity = trade["symbol"], trade["quantity"]
                if trade["side"] == "BUY":
                    cash -= trade["gross_value"] + trade["fee"]
                    quantities[symbol] += quantity
                    lots[symbol].append([day, quantity])
                else:
                    remaining = quantity
                    for lot in lots[symbol]:
                        sold = min(remaining, lot[1])
                        if sold:
                            require(lot[0] < day, "T1_SELLABILITY")
                            lot[1] -= sold
                            remaining -= sold
                    require(remaining == 0, "SELL_EXCEEDS_HOLDINGS")
                    quantities[symbol] -= quantity
                    cash += trade["gross_value"] - trade["fee"]
                require(cash >= -1e-6, "NEGATIVE_CASH")
                total_fees += trade["fee"]
            market_value = sum(quantities[symbol] * float(bundle["daily"].loc[
                (bundle["daily"].symbol == symbol) & (bundle["daily"].date == day), "close"].iloc[0])
                for symbol in w["symbols"])
            equity = round(cash + market_value, 4)
            row = checks[index]
            equal(row["cash"], round(cash, 4), "CASH")
            equal(row["market_value"], round(market_value, 4), "MARKET_VALUE")
            equal(row["equity"], equity, "EQUITY")
            require(row["position_quantities"] == quantities, "POSITIONS")
            require(0 <= row["max_abs_ledger_delta"] <= .001, "LEDGER_DELTA")
            equal(result["daily_returns"][index]["net_return"], equity / previous - 1, "DAILY_RETURN", 1e-12)
            require(points[index]["event_kind"] == "OBSERVED_AFTER_CLOSE"
                    and pd.Timestamp(points[index]["timestamp"]) == pd.Timestamp(observed[day]["close_at"]), "VALUATION_TIME")
            equal(points[index]["equity"], equity, "VALUATION_EQUITY", .0001)
            peak = max(peak, equity)
            drawdown = min(drawdown, equity / peak - 1)
            previous = equity
        expected_metrics = {"net_return": previous / 1_000_000 - 1, "max_drawdown": drawdown,
                            "total_fees": total_fees, "trade_count": len(trades)}
        require(set(result["metrics"]) == set(expected_metrics), "METRIC_FIELDS")
        for key, expected in expected_metrics.items():
            equal(result["metrics"][key], expected, "METRIC_" + key.upper(), 1e-10)
            equal(result["report"]["metrics"][key], expected, "REPORT_METRIC_" + key.upper(), 1e-10)
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        raise ValueError("FORMAL_SAVED_RESULT_STRUCTURE_INVALID") from error
    # 仅账务自洽不能证明执行了冻结规则：删除完整亏损回合也可做出自洽假账。
    # 因此用已授权的相同计划/输入纯内存重放；不调用治理登记、不增加统计曝光。
    def trajectory(value):
        account = value["chain"]
        return {"decisions": value["decisions"], "orders": account["orders"],
                "trades": account["trades"], "sizing_skips": account["sizing_skips"],
                "independent_account_checks": account["independent_account_checks"],
                "ledger": value["ledger"], "daily_returns": value["daily_returns"],
                "metrics": value["metrics"], "valuation": account["official_valuation"]}

    saved = trajectory(result)
    cache_key = stable_hash({"plan": plan, "input_identity": result["input_identity"], "trajectory": saved})
    if cache_key not in _RESULT_REPLAY_CACHE:
        receipt = {"strategy_plans": {strategy_id: plan}, "input_identity": result["input_identity"],
                   "novelty": {strategy_id: {"allowed": True}},
                   "execution_purpose": strategy_id, "execution_consumed": True}
        repeated = run_formal_account(proposal, strategy_id=strategy_id, bundle=bundle, window=w,
            costs=fees, input_identity=result["input_identity"], active_check=lambda: receipt)
        require(stable_hash(saved) == stable_hash(trajectory(repeated)), "FROZEN_TRAJECTORY_MISMATCH")
        _RESULT_REPLAY_CACHE[cache_key] = True
        while len(_RESULT_REPLAY_CACHE) > 32:
            _RESULT_REPLAY_CACHE.popitem(last=False)
    else:
        _RESULT_REPLAY_CACHE.move_to_end(cache_key)
    return {"status": "VERIFIED", "input_identity": result["input_identity"],
            "account_days": len(days), "trade_count": len(trades)}
