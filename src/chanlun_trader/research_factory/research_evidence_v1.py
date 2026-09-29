"""离线、只读研究证据门：冻结原件与成交独立重建；不产生预算或资格。"""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from .budget import BudgetLedgerMismatchError, SearchBudgetRegistryV1
from .common import stable_hash

VERSION = "RESEARCH_EVIDENCE_V1"


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _number(value):
    _require(type(value) in (int, float) and math.isfinite(value), "NONFINITE_ACCOUNT_NUMBER")
    return float(value)


def _same(actual, expected, reason, tolerance=.02):
    _require(abs(_number(actual) - _number(expected)) <= tolerance, reason)


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _verify_pending_exit(day, lot_id, lot, *, bars, states, fills, orders, strategy_id, all_lots=None, prior_decisions=()):
    """以冻结日线独立推导可卖数量；不信订单自报拒绝原因，不重跑引擎。"""
    symbol = lot['symbol']
    raw, security = bars[(day, symbol)], states[(day, symbol)]
    sold = [trade for trade in fills if trade['side'] == 'SELL' and trade['symbol'] == symbol
            and trade.get('lot_id') == lot_id]
    # 这些状态在订单创建前就阻断；原件本身足以证明等待，不要求伪造拒单。
    blocked = (not security['listed'] or security['delisted']
               or security['suspension_status'] == 'SUSPENDED' or _number(raw['volume']) <= 0
               or day <= lot['bought'])
    if blocked:
        _require(not sold, 'EXIT_FILL_WHILE_NOT_TRADABLE')
        return
    if security['suspension_status'] != 'TRADING':
        raise KeyError('EXIT_TRADABILITY_EVIDENCE_INCOMPLETE')
    board = security['board']
    if board not in {'SZ_MAIN', 'SH_MAIN', 'MAIN', 'CHINEXT', 'STAR', 'BSE'}:
        raise KeyError('EXIT_PRICE_LIMIT_REGIME_UNKNOWN')
    pct = .2 if board in {'CHINEXT', 'STAR'} else .3 if board == 'BSE' else .05 if security['st_status'] == 'ST' else .1
    previous, opening = _number(raw['prev_close']), _number(raw['open'])
    if previous <= 0:
        raise KeyError('EXIT_PREVIOUS_CLOSE_MISSING')
    # 同原发布日线制度：先判涨停，再判跌停；涨停不阻止既有持仓卖出。
    at_up = opening >= round(previous * (1 + pct), 2) * .98
    at_down = not at_up and opening <= round(previous * (1 - pct), 2) * 1.02
    history = [(date, row) for (date, ticker), row in bars.items()
               if ticker == symbol and date < day and _number(row['volume']) > 0]
    volume = _number(max(history, key=lambda item: item[0])[1]['volume']) if history else 0
    full_exit = any(item['strategy_id'] == strategy_id and item['symbol'] == symbol
                    and item['side'] == 'SELL' and 'exit_lot_ids' not in item
                    for item in prior_decisions)
    matching = []
    for order in orders.values():
        if (order['strategy_id'] != strategy_id or order['symbol'] != symbol
                or order['side'] != 'SELL' or not (order.get('lot_id') == lot_id
                    or (full_exit and order.get('lot_id') is None))):
            continue
        stamp = pd.Timestamp(order['created_at'])
        _require(stamp.tzinfo is not None, 'EXIT_ORDER_TIME_INVALID')
        local = stamp.tz_convert('Asia/Shanghai')
        if int(local.strftime('%Y%m%d')) == day:
            _require((local.hour, local.minute) == (9, 30), 'EXIT_ORDER_NOT_AT_NEXT_OPEN')
            matching.append(order)
    if not matching:
        # 无客观阻断依据且无订单，不能用正确的SELL决策冒充退出执行。
        raise KeyError('EXIT_ORDER_EVIDENCE_MISSING')
    _require(len(matching) == 1, 'DUPLICATE_PENDING_EXIT_ORDER')
    order = matching[0]
    if order.get('lot_id') is None:
        if all_lots is None:
            raise KeyError('EXIT_FIFO_LOTS_REQUIRED')
        # lots由原始BUY成交按时间插入；同日批次仍保持成交FIFO顺序。
        eligible = [(key, value) for key, value in all_lots.items()
                    if value['symbol'] == symbol and value['remaining'] > 0 and value['bought'] < day]
        eligible.sort(key=lambda item: item[1]['bought'])
        _require(any(key == lot_id for key, _ in eligible), 'EXIT_FIFO_PENDING_LOT_NOT_SELLABLE')
        quantity = sum(value['remaining'] for _, value in eligible)
        sold = [trade for trade in fills if trade['side'] == 'SELL' and trade['symbol'] == symbol
                and trade.get('order_id') == order['order_id']]
        _require(all(trade.get('lot_id') is None or (len(eligible) == 1 and trade['lot_id'] == eligible[0][0])
                     for trade in sold), 'EXIT_FIFO_FILL_LOT_CONFLICT')
    else:
        eligible, quantity = [(lot_id, lot)], lot['remaining']
    expected = 0 if at_down else min(quantity, int(volume * .10))
    _require(order['quantity'] == quantity, 'EXIT_ORDER_QUANTITY_CONFLICT')
    _require(order.get('submitted_at') is not None and order.get('eligible_at') is not None,
             'EXIT_ORDER_NOT_SUBMITTED')
    for field in ('submitted_at', 'eligible_at'):
        _require(pd.Timestamp(order[field]) == pd.Timestamp(order['created_at']), 'EXIT_ORDER_SUBMISSION_TIME_CONFLICT')
    _require(all(trade.get('order_id') == order['order_id'] for trade in sold), 'EXIT_FILL_ORDER_CONFLICT')
    _require(sum(trade['quantity'] for trade in sold) == expected, 'EXIT_EXECUTED_QUANTITY_CONFLICT')
    _require(order['filled_quantity'] == expected and order['remaining_quantity'] == quantity - expected,
             'EXIT_ORDER_FILL_TOTAL_CONFLICT')
    # 将全仓成交独立按FIFO归属。容量只覆盖前笔时，后笔合法保留待退出。
    expected_left, actual_left = expected, sum(trade['quantity'] for trade in sold)
    allocated = {}
    for key, value in eligible:
        expected_lot = min(value['remaining'], expected_left)
        actual_lot = min(value['remaining'], actual_left)
        _require(actual_lot == expected_lot, 'EXIT_FIFO_ALLOCATION_CONFLICT')
        allocated[key] = actual_lot
        expected_left -= expected_lot
        actual_left -= actual_lot
    return allocated[lot_id]


def reconstruct_account(bundle, window, result, *, initial_cash, costs, strategy_id, rule=None):
    """不读取引擎余额作输入；只使用冻结行情、公司行动条款、实际导出成交。"""
    bars = {(int(row["date"]), row["symbol"]): row for row in bundle["daily"].to_dict("records")}
    states = {(int(row["trade_date"]), row["symbol"]): row for row in bundle["states"].to_dict("records")}
    days = [int(value) for value in window["calendar"] if window["account_start"] <= value <= window["account_end"]]
    _require([int(row["date"]) for row in result["daily_accounts"]] == days, "ACCOUNT_DATES_CONFLICT")
    events = bundle["events"]
    _require(len({event["event_id"] for event in events}) == len(events), "DUPLICATE_CORPORATE_ACTION")
    for event in events:
        _require(event["event_type"] == "CASH_DIVIDEND", "UNSUPPORTED_CORPORATE_ACTION")
        _require(event["terms"]["tax_rule"]["kind"] == "DEFERRED_INDIVIDUAL_2015_101", "UNKNOWN_DIVIDEND_TAX")
    fills = result["fills"]
    _require(len({trade["trade_id"] for trade in fills}) == len(fills), "DUPLICATE_TRADE")
    stamps = [pd.Timestamp(trade["fill_time"]) for trade in fills]
    _require(all(stamp.tzinfo is not None for stamp in stamps) and stamps == sorted(stamps), "TRADE_ORDER_OR_TIME_INVALID")
    by_day = defaultdict(list)
    for trade, stamp in zip(fills, stamps):
        day = int(stamp.tz_convert("Asia/Shanghai").strftime("%Y%m%d"))
        _require(day in days and trade["symbol"] in window["symbols"] and trade["strategy_id"] == strategy_id,
                 "TRADE_SCOPE_CONFLICT")
        _require((stamp.tz_convert("Asia/Shanghai").hour, stamp.tz_convert("Asia/Shanghai").minute) == (9, 30), "TRADE_OPEN_TIME_CONFLICT")
        by_day[day].append(trade)
    cash = _number(initial_cash)
    lots, quantities, entitlements, paid, lot_rights = {}, {}, {}, set(), {}
    fees = tax_total = 0.0
    daily, peak, drawdown, risk_state = [], cash, 0.0, {}
    risk = (rule or {}).get("exits", {})
    enabled = any(risk.get(key) is not None for key in ("stop_loss_pct", "take_profit_pct", "trailing_pct"))
    traces = result.get("final_account_checkpoint", {}).get("rule_exit_states", {}).get(strategy_id, {}).get("evaluations", [])
    trace_map = {(row["date"], row["lot_id"]): row for row in traces}
    _require(len(trace_map) == len(traces), "DUPLICATE_EXIT_EVALUATION")
    decisions = {row["date"]: row["decisions"] for row in result.get("decisions", [])}
    verified_exit_rows = 0
    for index, day in enumerate(days):
        if enabled:
            for lot_id, state in risk_state.items():
                lot = lots[lot_id]
                if state['pending'] and lot['remaining']:
                    _verify_pending_exit(day, lot_id, lot, bars=bars, states=states, fills=by_day[day],
                        orders=result['final_account_checkpoint']['economic']['orders'], strategy_id=strategy_id,
                        all_lots=lots, prior_decisions=decisions.get(days[index - 1], []))
        for event in events:
            event_id = event["event_id"]
            if event["payment_date"] == day:
                amount = entitlements.get(event_id, 0) * _number(event["terms"]["cash_per_share"])
                cash += amount
                if amount:
                    paid.add(event_id)
        for trade in by_day[day]:
            symbol, side, quantity = trade["symbol"], trade["side"], trade["quantity"]
            _require(side in ("BUY", "SELL") and type(quantity) is int and quantity > 0, "INVALID_TRADE_QUANTITY")
            raw, security = bars[(day, symbol)], states[(day, symbol)]
            _require(security["listed"] and not security["delisted"] and security["suspension_status"] == "TRADING", "UNTRADABLE_FILL")
            direction = 1 if side == "BUY" else -1
            price = round(_number(raw["open"]) * (1 + direction * costs["slippage_bps"]), 4)
            fee = round(max(quantity * price * costs["commission_rate"], costs["min_commission"])
                        + (0 if side == "BUY" else quantity * price * costs["stamp_tax_rate"]), 4)
            _same(trade["price"], price, "FILL_PRICE_CONFLICT", 1e-6)
            _same(trade["fee"], fee, "FILL_FEE_CONFLICT", .0001)
            if side == "BUY":
                _require(quantity % 100 == 0 and security["st_status"] == "NORMAL", "INVALID_BUY_LOT_OR_ST")
                _require(trade.get("lot_id") and trade["lot_id"] not in lots, "BUY_LOT_IDENTITY_REQUIRED")
                lots[trade["lot_id"]] = {"symbol": symbol, "remaining": quantity, "bought": day, "price": price}
            else:
                remaining = quantity
                for lot_id, lot in lots.items():
                    if lot["symbol"] != symbol or not lot["remaining"] or not remaining:
                        continue
                    if trade.get("lot_id") is not None and trade["lot_id"] != lot_id:
                        continue
                    _require(day > lot["bought"], "T1_FILL_CONFLICT")
                    sold = min(remaining, lot["remaining"])
                    for event in events:
                        event_id = event["event_id"]
                        rights = lot_rights.get(event_id, {})
                        taxed = min(sold, rights.get(lot_id, 0))
                        if taxed:
                            _require(day >= event["payment_date"], "SALE_BEFORE_DIVIDEND_PAYMENT")
                            bought_at, sold_at = pd.Timestamp(str(lot["bought"])), pd.Timestamp(str(day))
                            rate = .2 if sold_at <= bought_at + pd.DateOffset(months=1) else (.1 if sold_at <= bought_at + pd.DateOffset(years=1) else 0.)
                            tax = round(taxed * event["terms"]["cash_per_share"] * rate, 4)
                            cash -= tax
                            tax_total += tax
                            rights[lot_id] -= taxed
                    lot["remaining"] -= sold
                    remaining -= sold
                _require(remaining == 0, "SELL_EXCEEDS_OWNED_LOTS")
            cash -= direction * quantity * price + fee
            fees += fee
            quantities[symbol] = quantities.get(symbol, 0) + direction * quantity
            _require(cash >= -.02, "NEGATIVE_CASH")
        for event in events:
            if event["record_date"] == day:
                lot_rights[event["event_id"]] = {key: lot["remaining"] for key, lot in lots.items()
                                                if lot["symbol"] == event["symbol"] and lot["remaining"]}
                entitlements[event["event_id"]] = sum(lot_rights[event["event_id"]].values())
        receivable = sum(entitlements.get(event["event_id"], 0) * event["terms"]["cash_per_share"]
                         for event in events if event["effective_date"] <= day and event["event_id"] not in paid)
        equity = cash + receivable + sum(quantity * bars[(day, symbol)]["close"] for symbol, quantity in quantities.items())
        row = result["daily_accounts"][index]
        _same(row["cash"], cash, "DAILY_CASH_CONFLICT")
        _same(row["equity"], equity, "DAILY_EQUITY_CONFLICT")
        actual_positions = {(item["strategy_id"], item["symbol"]): item["quantity"] for item in row["positions"]}
        _require(len(actual_positions) == len(row["positions"]) and actual_positions == {(strategy_id, key): value for key, value in quantities.items()}, "DAILY_POSITIONS_CONFLICT")
        peak = max(peak, equity)
        drawdown = max(drawdown, 1 - equity / peak)
        daily.append({"date": day, "cash": cash, "equity": equity, "positions": deepcopy(quantities)})
        if enabled:
            for lot_id, lot in lots.items():
                if not lot["remaining"]:
                    continue
                gross = sum(event["terms"]["cash_per_share"] for event in events
                            if event["event_id"] in paid and event["effective_date"] <= day
                            and lot_rights.get(event["event_id"], {}).get(lot_id, 0))
                close = bars[(day, lot["symbol"])]["close"]
                comparison = close + gross
                state = risk_state.setdefault(lot_id, {"peak": lot["price"], "active": False, "pending": []})
                if not state["pending"]:
                    state["peak"] = max(state["peak"], comparison)
                    if risk.get("trailing_pct") is not None:
                        state["active"] |= state["peak"] >= lot["price"] * (1 + risk["trailing_activate_pct"])
                    hits = []
                    if risk.get("stop_loss_pct") is not None and comparison <= lot["price"] * (1-risk["stop_loss_pct"]): hits.append("EXIT_FIXED_COST_STOP")
                    if state["active"] and comparison <= state["peak"]*(1-risk["trailing_pct"]): hits.append("EXIT_TRAILING_STOP")
                    if risk.get("take_profit_pct") is not None and comparison >= lot["price"]*(1+risk["take_profit_pct"]): hits.append("EXIT_FIXED_TAKE_PROFIT")
                    state["pending"] = hits
                trace = trace_map.get((day, lot_id))
                _require(trace is not None, "EXIT_EVALUATION_MISSING")
                verified_exit_rows += 1
                _same(trace["entry_price"], lot["price"], "EXIT_COST_ANCHOR_CONFLICT", 1e-6)
                _same(trace["raw_close"], close, "EXIT_RAW_PRICE_CONFLICT", 1e-6)
                _same(trace["comparison_close"], comparison, "EXIT_COMPARISON_CONFLICT", 1e-6)
                _require(bool(trace["reasons"]) == bool(state["pending"]), "EXIT_TRIGGER_CONFLICT")
                if state["pending"]:
                    _require(trace["reasons"][0] == state["pending"][0], "EXIT_REASON_CONFLICT")
                    if day != days[-1]:
                        rows = [item for item in decisions.get(day, []) if item["strategy_id"] == strategy_id and item["symbol"] == lot["symbol"]]
                        _require(len(rows) == 1 and rows[0]["side"] == "SELL" and ("exit_lot_ids" not in rows[0] or lot_id in rows[0]["exit_lot_ids"]), "EXIT_INTENT_MISSING")
    _require(not enabled or verified_exit_rows == len(traces), "EXTRANEOUS_EXIT_EVALUATION")
    if enabled and risk.get("trailing_pct") is not None:
        trailing = result["final_account_checkpoint"]["rule_exit_states"][strategy_id]["trailing"]
        _require(set(trailing) == set(risk_state), "TRAILING_LOT_SET_CONFLICT")
        for lot_id, state in risk_state.items():
            _same(trailing[lot_id]["peak_close"], state["peak"], "TRAILING_PEAK_CONFLICT", 1e-6)
            _require(trailing[lot_id]["activated"] == state["active"], "TRAILING_ACTIVATION_CONFLICT")
            _same(trailing[lot_id]["line"], state["peak"] * (1-risk["trailing_pct"]) if state["active"] else 0., "TRAILING_LINE_CONFLICT", 1e-6)
    final = result["final_account_checkpoint"]["economic"]
    _same(final["cash"], cash, "FINAL_CASH_CONFLICT")
    _same(final["dividend_tax_withheld"], tax_total, "FINAL_DIVIDEND_TAX_CONFLICT", .001)
    _require(final["trades"] == fills, "FINAL_TRADE_EXPORT_CONFLICT")
    _require({key: value["remaining_quantity"] for key, value in final["lots"].items()} == {key: value["remaining"] for key, value in lots.items()}, "FINAL_LOTS_CONFLICT")
    metrics = {"net_return": daily[-1]["equity"] / initial_cash - 1, "max_drawdown": drawdown,
               "total_fees": fees, "trade_count": len(fills)}
    for key, value in metrics.items():
        _same(result["metrics"][key], value, "METRIC_CONFLICT:" + key, 1e-6)
    return {"daily_accounts": daily, "metrics": metrics, "dividend_tax": tax_total,
            "exit_behavior": "VERIFIED" if enabled else "NOT_CONFIGURED", "exit_rows": verified_exit_rows}


def verify_job_evidence(job_path, *, name):
    """只读冻结 INPUT 路径；缺证 INCOMPLETE，矛盾 FAIL，全部一致才 PASS。"""
    root = Path(job_path).resolve().parent
    try:
        job = _json(job_path)
        _require(Path(job["root"]).resolve() == root, "JOB_ROOT_CONFLICT")
        plan = job["plans"][name]
        _require(plan["plan_id"] == stable_hash({key: value for key, value in plan.items() if key != "plan_id"}), "PLAN_HASH_CONFLICT")
        item = job["items"][name]
        _require(item == plan["runtime"], "RUNTIME_PLAN_CONFLICT")
        _require(item["loader"] == "chanlun_trader.research_factory.strategy_submission_v1:load_frozen_bundle", "OFFLINE_INPUT_LOADER_UNSUPPORTED")
        receipt = _json(root / "CONFIRMATION.json")
        _require(receipt["receipt_id"] == stable_hash({key: value for key, value in receipt.items() if key != "receipt_id"}), "RECEIPT_HASH_CONFLICT")
        _require(receipt["strategy_plans"] == job["plans"] and receipt["objective_id"] == job["objective_id"]
                 and receipt["input_identity"] == job["input_identity"] and receipt["budget_path"] == job["budget_path"], "RECEIPT_JOB_CONFLICT")
        _require(receipt["novelty"][name]["allowed"] is True and receipt["novelty"][name]["plan_id"] == plan["plan_id"], "NOVELTY_BINDING_CONFLICT")
        source = receipt["source"]
        if source['origin'] == 'CAMPAIGN_V1':
            from .etf_account_governance_v1 import validate_campaign_source
            campaign = validate_campaign_source(source, job['plans'], job['objective_id'])
            operation = campaign.status()['operations'][source['operation_ids'][name]]
            _require(operation['status'] in {'RUNNING', 'UNKNOWN', 'COMPLETED'}, 'CAMPAIGN_ACCOUNT_NOT_STARTED')
        else:
            _require(source["origin"] == "USER_EXPLICIT_CURRENT_TASK" and bool(source["statement"])
                     and source["approved_plan_ids"] == {key: value["plan_id"] for key, value in job["plans"].items()}, "APPROVAL_SCOPE_NOT_VERIFIED")
        start, settlement = _json(root / (name + "_START.json")), _json(root / (name + "_SETTLEMENT.json"))
        _require(start["kind"] == name and start["receipt_id"] == receipt["receipt_id"] and start["counted_before_account_calculation"] is True, "START_BINDING_CONFLICT")
        _require(all(settlement.get(key) == value for key, value in start.items()) and settlement["completed"] is True and settlement["error"] is None, "SETTLEMENT_CONFLICT")
        _require(pd.Timestamp(receipt["recorded_at"]) <= pd.Timestamp(start["started_at"]) < pd.Timestamp(receipt["expires_at"])
                 and pd.Timestamp(start["started_at"]) <= pd.Timestamp(settlement["settled_at"]), "AUTHORIZATION_TIME_CONFLICT")
        resource = _json(root / (name + "_RESOURCE.json"))
        _require(type(resource["returncode"]) is int and resource["returncode"] == 0 and not resource.get("timed_out"), "WORKER_NOT_SUCCESSFUL")
        worker = _json(root / (name + "_WORKER.json"))
        access = _json(root / (name + "_INPUT_ACCESS.json"))
        direct_pid = worker["pid"] == access["reader_pid"]
        windows_child = (access.get("resource_platform") == resource.get("resource_platform") == "nt"
                         and access.get("reader_parent_pid") == worker["pid"] == access.get("launcher_pid")
                         and resource.get("launcher_pid") == worker["pid"]
                         and access.get("windows_job_verified") is True and resource.get("windows_job_bound") is True)
        _require(worker["purpose"] == name and (direct_pid or windows_child) and access["purpose"] == name
                 and access["input_identity"] == job["input_identity"] and access["loader"] == item["loader"] and access["loader_kwargs"] == item["loader_kwargs"], "INPUT_ACCESS_CONFLICT")
        if not Path(job["budget_path"]).is_file():
            raise FileNotFoundError("BUDGET_FILE_MISSING")
        budget = SearchBudgetRegistryV1(job["objective_id"], job["budget_path"]).snapshot()
        _require(budget["settled_reservations"].get(start["reservation"]) == "CONSUMED", "BUDGET_NOT_CONSUMED")
        buckets = [row for row in budget["buckets"] if row["kind"] == "strategy_interface_account_v1" and row["key"] == receipt["receipt_id"] + ":" + name]
        _require(len(buckets) == 1 and buckets[0]["used"] == 1 and buckets[0]["reserved"] == 0, "BUDGET_BUCKET_CONFLICT")
        expected_sources = {**item["source_hashes"], **plan["strategy"]["source_hashes"], **plan["backend"]["source_hashes"]}
        _require(bool(expected_sources) and all(job["source_hashes"].get(path) == digest for path, digest in expected_sources.items()), "SOURCE_COVERAGE_CONFLICT")
        for path, digest in job["source_hashes"].items():
            archive = root / "source-archive" / (digest + "_" + Path(path).name)
            _require(_sha(archive) == digest, "SOURCE_ARCHIVE_CONFLICT")
        result_path = root / (name + "_RESULT.json")
        digest = _sha(result_path)
        index = _json(root / "RESULTS_INDEX.json")["items"][name]
        _require(Path(index["result"]).resolve() == result_path and Path(index["settlement"]).resolve() == root / (name + "_SETTLEMENT.json")
                 and index["sha256"] == digest == settlement["result_sha256"], "RESULT_HASH_BINDING_CONFLICT")
        from .strategy_submission_v1 import load_frozen_bundle
        loaded = load_frozen_bundle(**item["loader_kwargs"])
        frozen = _json(item["loader_kwargs"]["path"])
        _require(loaded["input_identity"] == job["input_identity"] and frozen["window"] == plan["backend"]["window"], "FROZEN_INPUT_SCOPE_CONFLICT")
        result = _json(result_path)
        _require(result["strategy_plan"] == plan and result["input_identity"] == job["input_identity"], "RESULT_PLAN_CONFLICT")
        audit = reconstruct_account(loaded["frame"], frozen["window"], result,
            initial_cash=plan["backend"]["initial_cash"], costs=plan["backend"]["costs"], strategy_id=name,
            rule=plan["strategy"]["parameters"].get("candidate_payload"))
        return {"version": VERSION, "status": "PASS", "advance_allowed": True, "reasons": [],
                "plan_id": plan["plan_id"], "result_sha256": digest, "input_identity": loaded["input_identity"],
                "account_audit": audit, "data_qualification": frozen.get("qualification"),
                "evidence_layers": {"account_reconciled": True, "input_profile": loaded["frame"]["profile"],
                    "real_data_validity": "NOT_ESTABLISHED_BY_ACCOUNT_RECONCILIATION", "historical_screen": "NOT_ASSESSED",
                    "independent_validation": "NOT_ASSESSED", "formal_qualification": False}}
    except (FileNotFoundError, KeyError) as exc:
        return {"version": VERSION, "status": "INCOMPLETE", "advance_allowed": False, "reasons": [str(exc)]}
    except (ValueError, TypeError, PermissionError, OSError, BudgetLedgerMismatchError) as exc:
        return {"version": VERSION, "status": "FAIL", "advance_allowed": False, "reasons": [str(exc)]}
