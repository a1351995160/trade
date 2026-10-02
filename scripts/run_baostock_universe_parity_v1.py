"""通过公共提交服务核对旧 BaoStock 与全范围账户；不自行签发授权。"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
for local_path in (ROOT, ROOT / "src"):
    if str(local_path) not in sys.path:
        sys.path.insert(0, str(local_path))

from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.exploration_governance import immutable, read_json


VERSION = "BAOSTOCK_UNIVERSE_PARITY_V1"
TOLERANCE = 1e-6
_ABSENT = "<MISSING>"


def _differences(old, new, path="", *, tolerance=TOLERANCE):
    """逐项列出差异；一分钱以内的差异也不能被舍入掉。"""
    result = []
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            result.extend(_differences(old.get(key, _ABSENT), new.get(key, _ABSENT),
                f"{path}.{key}" if path else str(key), tolerance=tolerance))
    elif isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            result.append({"path": path + ".length", "old": len(old), "new": len(new)})
        for index in range(max(len(old), len(new))):
            result.extend(_differences(old[index] if index < len(old) else _ABSENT,
                new[index] if index < len(new) else _ABSENT, f"{path}[{index}]", tolerance=tolerance))
    elif (type(old) in (int, float) and type(new) in (int, float)
          and math.isfinite(old) and math.isfinite(new)):
        if abs(old - new) > tolerance:
            result.append({"path": path, "old": old, "new": new})
    elif old != new or type(old) is not type(new):
        result.append({"path": path, "old": old, "new": new})
    return result


def validate_parity_previews_v1(old, new):
    fields = ("strategy_id", "rule", "symbols", "feature_start", "account_start", "account_end",
              "initial_cash", "max_positions", "max_symbol_exposure_bps", "costs", "benchmark", "purpose")
    differences = _differences({key: old["request"][key] for key in fields},
                              {key: new["request"][key] for key in fields}, "request", tolerance=0)
    differences += _differences(old["rule_identity"], new["rule_identity"], "rule_identity", tolerance=0)
    if old["request"]["costs"] != ["BASE", "STRESS"] or old["request"]["benchmark"] != "NONE":
        raise ValueError("PARITY_BASE_STRESS_WITHOUT_EXTRA_BENCHMARK_REQUIRED")
    if new["request"].get("version") != "FULL_UNIVERSE_SUBMISSION_V1":
        raise ValueError("PARITY_NEW_UNIVERSE_ENTRY_REQUIRED")
    if old["request"].get("version") is not None:
        raise ValueError("PARITY_OLD_PUBLIC_ENTRY_REQUIRED")
    if differences:
        raise ValueError("PARITY_REQUESTS_DIFFER:" + json.dumps(differences, ensure_ascii=False))
    return {key: deepcopy(old["request"][key]) for key in fields}


def _records(frame, columns):
    if not set(columns) <= set(frame.columns):
        raise ValueError("PARITY_INPUT_COLUMNS_MISSING:" + ",".join(sorted(set(columns) - set(frame.columns))))
    result = frame.loc[:, columns].sort_values([key for key in ("symbol", "date", "trade_date")
                                               if key in columns]).to_dict("records")
    return result


def compare_parity_inputs_v1(old, new, window):
    """额外资格元数据允许不同；原行情、换手率、事件和账户期状态必须一致。"""
    differences = _differences(old["calendar"], new["calendar"], "calendar", tolerance=0)
    columns = ["symbol", "date", "open", "high", "low", "close", "prev_close", "volume", "amount"]
    differences += _differences(_records(old["daily"], columns), _records(new["daily"], columns), "daily")
    turn_columns = ["symbol", "date", "volume", "tradestatus"]
    if "turn" in old["turn"].columns or "turn" in new["turn"].columns:
        turn_columns.append("turn")
    differences += _differences(_records(old["turn"], turn_columns), _records(new["turn"], turn_columns), "turn")
    differences += _differences(sorted(old["events"], key=lambda event: event["event_id"]),
                               sorted(new["events"], key=lambda event: event["event_id"]), "events")
    state_columns = ["symbol", "trade_date", "listed", "delisted", "universe_member",
                     "eligibility_status", "st_status", "suspension_status", "board"]
    old_states = old["states"].loc[old["states"].trade_date.ge(window["account_start"])]
    if "trade_date" in new["states"].columns:
        new_states = new["states"].loc[new["states"].trade_date.ge(window["account_start"])]
    else:
        import pandas as pd
        expanded = []
        for row in new["states"].to_dict("records"):
            for date in new["calendar"]:
                if window["account_start"] <= date and row["effective_date"] <= date <= row["valid_to"]:
                    expanded.append({**row, "trade_date": date})
        new_states = pd.DataFrame(expanded)
    differences += _differences(_records(old_states, state_columns), _records(new_states, state_columns), "states")
    source_values = lambda bundle: {value for value in bundle["source_hashes"].values()
                                   if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)}
    old_sources, new_sources = source_values(old), source_values(new)
    missing = sorted(old_sources - new_sources)
    if not old_sources or missing:
        differences.append({"path": "source_hashes.original_bytes", "old": sorted(old_sources),
                            "new": sorted(new_sources), "missing": missing})
    return {"passed": not differences, "original_source_count": len(old_sources),
            "additional_source_count": len(new_sources - old_sources), "mismatches": differences}


def _intent_map(result):
    mapping, plans = {}, {}
    for day in result["decisions"]:
        plan = day["plan"]
        binding = {"date": day["date"], "decision_at": plan["decision_at"], "next_session": plan["next_session"]}
        if plan["plan_id"] in plans:
            raise ValueError("PARITY_DUPLICATE_PLAN_ID")
        plans[plan["plan_id"]] = binding
        for intent in plan["intents"]:
            if intent["intent_id"] in mapping:
                raise ValueError("PARITY_DUPLICATE_INTENT_ID")
            mapping[intent["intent_id"]] = {**binding, **{key: value for key, value in intent.items() if key != "intent_id"}}
    return mapping, plans


def _economic_view(result):
    intents, plans = _intent_map(result)
    def reference(identity, mapping, kind):
        if identity not in mapping:
            if mapping is intents and isinstance(identity, str) and ":" in identity:
                parent, lot_id = identity.split(":", 1)
                if parent in mapping and lot_id in mapping[parent].get("exit_lot_ids", []):
                    return {**deepcopy(mapping[parent]), "exit_lot_id": lot_id}
            raise ValueError("PARITY_UNBOUND_" + kind + ":" + str(identity))
        return deepcopy(mapping[identity])
    decisions = []
    for day in result["decisions"]:
        plan = day["plan"]
        decisions.append({"date": day["date"], "decisions": deepcopy(day["decisions"]),
            "plan": {key: deepcopy(plan[key]) for key in
                ("decision_at", "next_session", "excluded", "holdings", "status", "usage_qualified",
                 "real_execution_authorized", "limits")},
            "intents": [{key: deepcopy(value) for key, value in intent.items() if key != "intent_id"}
                        for intent in plan["intents"]]})
    accounts = [{key: deepcopy(row[key]) for key in ("date", "cash", "equity", "positions")}
                for row in result["daily_accounts"]]
    state = result["final_account_checkpoint"]
    economic = deepcopy(state["economic"])
    # 这些哈希绑定不同的输入/制度版本；映射为原决策保留交易方向、时间和原因。
    for order in economic["orders"].values():
        order["intent_id"] = reference(order["intent_id"], intents, "ORDER_INTENT")
        if (order.get("signal_id") in intents or isinstance(order.get("signal_id"), str)
                and order["signal_id"].startswith("PORTFOLIO_INTENT_")):
            order["signal_id"] = reference(order["signal_id"], intents, "ORDER_SIGNAL")
        if "plan_id" in order.get("metadata", {}):
            order["metadata"]["plan_id"] = reference(order["metadata"]["plan_id"], plans, "ORDER_PLAN")
    for event in economic["events"]:
        for key in ("intent_id", "signal_id"):
            identity = event.get(key)
            if isinstance(identity, str) and (identity in intents or identity.startswith("PORTFOLIO_INTENT_")):
                event[key] = reference(identity, intents, "EVENT_" + key.upper())
    skipped = []
    for item in state["skipped_intents"]:
        row = deepcopy(item)
        row["intent_id"] = reference(row["intent_id"], intents, "SKIPPED_INTENT")
        skipped.append(row)
    return {"decisions": decisions, "daily_accounts": accounts, "fills": deepcopy(result["fills"]),
            "economic": economic, "skipped_intents": skipped, "metrics": deepcopy(result["metrics"]),
            "invariant_errors": deepcopy(state["invariant_errors"])}


def _version_additive_audit_evidence(old_view, new_view, events):
    """新增税款说明须逐项有冻结事件和原支付审计支持，未知字段仍是差异。"""
    import pandas as pd
    from chanlun_trader.research_factory.universe_dividend_accounting_v1 import TAX_TIMING_POLICY, TAX_TIMING_SOURCE
    fields = {"broker_collection_time_verified", "dividend_paid", "payment_date", "policy", "source"}
    old_rows = old_view["economic"].get("action_audit", [])
    new_rows = new_view["economic"].get("action_audit", [])
    events_by_id = {event["event_id"]: event for event in events or []}
    rows, errors = [], []
    if events is not None and len(events_by_id) != len(events):
        errors.append({"path": "events", "reason": "DUPLICATE_FROZEN_EVENT_ID"})
    for index, (old, new) in enumerate(zip(old_rows, new_rows)):
        added = set(new) - set(old)
        if not added:
            continue
        path = f"economic.action_audit[{index}]"
        own_errors = []
        def require(condition, field, reason):
            if not condition:
                own_errors.append({"path": path + "." + field, "reason": reason})
        require(added == fields, "added_fields", "ONLY_COMPLETE_REGISTERED_TAX_METADATA_EXTENSION_ALLOWED")
        require(old.get("phase") == new.get("phase") == "DEFERRED_INDIVIDUAL_TAX", "phase", "TAX_AUDIT_PHASE_REQUIRED")
        require(events is not None, "event_id", "FROZEN_EVENTS_REQUIRED_FOR_ADDITIVE_EVIDENCE")
        event = events_by_id.get(new.get("event_id"))
        require(event is not None, "event_id", "ADDITIVE_TAX_EVENT_NOT_FROZEN")
        require(new.get("broker_collection_time_verified") is False,
                "broker_collection_time_verified", "BROKER_COLLECTION_MUST_REMAIN_UNVERIFIED")
        require(new.get("policy") == TAX_TIMING_POLICY, "policy", "REGISTERED_SALE_FILL_MODELED_POLICY_REQUIRED")
        require(new.get("source") == TAX_TIMING_SOURCE, "source", "REGISTERED_TAX_SOURCE_REQUIRED")
        payments = [row for row in new_rows if row.get("event_id") == new.get("event_id")
                    and row.get("phase") == "PAYMENT"]
        proof = None
        if event is not None:
            require(event.get("event_type") == "CASH_DIVIDEND", "event_id", "CASH_DIVIDEND_EVENT_REQUIRED")
            terms = event.get("terms", {}).get("tax_rule", {})
            require(terms.get("kind") == "DEFERRED_INDIVIDUAL_2015_101"
                    and new.get("source") == terms.get("source"), "source", "SOURCE_MUST_MATCH_FROZEN_TAX_TERMS")
            require(type(new.get("payment_date")) is int and new["payment_date"] == event.get("payment_date"),
                    "payment_date", "PAYMENT_DATE_MUST_MATCH_FROZEN_EVENT")
            require(len(payments) == 1, "dividend_paid", "ONE_ORIGINAL_PAYMENT_AUDIT_REQUIRED")
            require(new.get("event_id") in new_view["economic"].get("payments", []),
                    "dividend_paid", "EVENT_MUST_BE_IN_PAID_LEDGER")
            try:
                sold_at = pd.Timestamp(new["timestamp"])
                paid_at = pd.Timestamp(payments[0]["timestamp"]) if len(payments) == 1 else None
                aware = sold_at.tzinfo is not None and paid_at is not None and paid_at.tzinfo is not None
                require(aware, "timestamp", "AWARE_SALE_AND_PAYMENT_TIMES_REQUIRED")
                if aware:
                    payment_day = int(paid_at.tz_convert("Asia/Shanghai").strftime("%Y%m%d"))
                    already_paid = paid_at <= sold_at and payment_day == event.get("payment_date")
                    require(already_paid and new.get("dividend_paid") is True,
                            "dividend_paid", "PAYMENT_MUST_ALREADY_EXIST_AT_TAX_AUDIT")
                    proof = {"event_id": event["event_id"], "payment_date": event["payment_date"],
                             "payment_audit_at": payments[0]["timestamp"], "tax_audit_at": new["timestamp"],
                             "frozen_tax_source": terms.get("source")}
            except (ValueError, KeyError, TypeError, OverflowError):
                require(False, "timestamp", "SALE_OR_PAYMENT_TIMESTAMP_INVALID")
        rows.append({"path": path, "added_fields": {key: deepcopy(new[key]) for key in sorted(added)},
                     "status": "VERIFIED" if not own_errors else "INVALID", "proof": proof})
        errors.extend(own_errors)
        if not own_errors:
            for key in fields:
                del new[key]
    return {"status": "INVALID" if errors else "VERIFIED" if rows else "NOT_PRESENT",
            "rows": rows, "errors": errors,
            "policy": "保留原税款金额/时间/受益lot核对；新增来源和模型说明只有冻结事件及已支付审计逐项支持后才单独呈报。"}


def compare_parity_results_v1(old, new, *, events=None):
    """核心交易决策、现金、持仓、成交、拒单、红利与补税都必须逐项相同。"""
    for result in (old, new):
        if (result.get("strategy_qualified") is not False
                or result.get("independent_confirmation_eligible") is not False
                or result.get("reconciliation", {}).get("passed") is not True):
            raise ValueError("PARITY_UNQUALIFIED_RECONCILED_RESULTS_REQUIRED")
    old_view, new_view = _economic_view(old), _economic_view(new)
    additions = _version_additive_audit_evidence(old_view, new_view, events)
    differences = _differences(old_view, new_view)
    differences.extend({"path": "version_additive_audit_evidence." + row["path"],
                        "old": "VERIFIED_FROZEN_EVENT_AND_PAYMENT", "new": row["reason"]}
                       for row in additions["errors"])
    return {"passed": not differences, "status": "PASS" if not differences else "MISMATCH",
            "daily_count": len(old["daily_accounts"]), "old_trade_count": len(old["fills"]),
            "new_trade_count": len(new["fills"]), "old_economic_identity": stable_hash(old_view),
            "new_economic_identity": stable_hash(new_view), "numeric_tolerance": TOLERANCE,
            "mismatch_paths": [item["path"] for item in differences], "mismatches": differences,
            "version_additive_audit_evidence": additions,
            "normalization": "仅将不同版本的计划/意图哈希映射为原决策；不删除交易原因、数量或金额。",
            "strategy_qualified": False, "independent_validation": "NOT_RUN"}


def _frozen(service, request, preview):
    authority = service.authority(preview["request"]["authorization_ref"])
    task_id = stable_hash({"preview": preview["preview_identity"], "objective_id": authority["objective_id"]})
    path = service.root / task_id
    if (path / "TASK.json").exists():
        task = service._task(task_id)
        if task["preview_identity"] != preview["preview_identity"]:
            raise ValueError("PARITY_EXISTING_FROZEN_TASK_CONFLICT")
        service.approval_preview(task_id)
        return task
    if path.exists():
        raise ValueError("PARITY_PARTIAL_FREEZE_REQUIRES_ORIGINAL_TASK_RECOVERY:" + task_id)
    return service.freeze(request, preview["preview_identity"])


def _input_for_task(task):
    from chanlun_trader.research_factory.strategy_submission_v1 import load_frozen_bundle
    job = read_json(task["job_path"])
    items = list(job["items"].values())
    if not items or any(item["loader"] != "chanlun_trader.research_factory.strategy_submission_v1:load_frozen_bundle"
                        for item in items):
        raise ValueError("PARITY_PUBLIC_FROZEN_INPUT_REQUIRED")
    if any(item["loader_kwargs"] != items[0]["loader_kwargs"] for item in items):
        raise ValueError("PARITY_COST_SCENARIOS_INPUT_CONFLICT")
    return load_frozen_bundle(**items[0]["loader_kwargs"])["frame"]


def _results(task):
    job_path = Path(task["job_path"]).absolute()
    if job_path.resolve() != job_path:
        raise ValueError("PARITY_RESULT_PATH_INVALID_OR_REDIRECTED")
    job = read_json(job_path)
    root = job_path.parent
    if Path(job["root"]) != root:
        raise ValueError("PARITY_RESULT_ROOT_CONFLICT")
    index_path = root / "RESULTS_INDEX.json"
    if index_path.resolve() != index_path:
        raise ValueError("PARITY_RESULT_PATH_INVALID_OR_REDIRECTED")
    index = read_json(index_path)["items"]
    result = {}
    for name, plan in job["plans"].items():
        if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", name) is None:
            raise ValueError("PARITY_RESULT_NAME_INVALID")
        from chanlun_trader.research_factory.formal_account_backend_v1 import normalized_costs
        matches = [cost for cost in ("BASE", "STRESS") if plan["backend"]["costs"] == normalized_costs(cost)]
        if len(matches) != 1:
            raise ValueError("PARITY_COST_MODEL_NOT_REGISTERED")
        cost = matches[0]
        if cost in result or cost not in {"BASE", "STRESS"}:
            raise ValueError("PARITY_EXACT_TWO_COST_SCENARIOS_REQUIRED")
        item = index[name]
        path = root / (name + "_RESULT.json")
        settlement_path = root / (name + "_SETTLEMENT.json")
        if (Path(item["result"]) != path or Path(item["settlement"]) != settlement_path
                or path.resolve() != path or settlement_path.resolve() != settlement_path):
            raise ValueError("PARITY_RESULT_PATH_INVALID_OR_REDIRECTED")
        raw = path.read_bytes()
        settlement = read_json(settlement_path)
        if (hashlib.sha256(raw).hexdigest() != item["sha256"]
                or settlement["result_sha256"] != item["sha256"]):
            raise ValueError("PARITY_RESULT_SETTLEMENT_CONFLICT")
        result[cost] = json.loads(raw)
    if set(result) != {"BASE", "STRESS"}:
        raise ValueError("PARITY_EXACT_TWO_COST_SCENARIOS_REQUIRED")
    return result


def run_baostock_universe_parity_v1(old_service, new_service, old_request, new_request, output_root, *, execute=False):
    root = Path(output_root).absolute()
    if root.resolve() != root:
        raise ValueError("PARITY_OUTPUT_REDIRECTED")
    previews = {"old": old_service.preview(old_request), "new": new_service.preview(new_request)}
    scope = validate_parity_previews_v1(previews["old"], previews["new"])
    plan = {"version": VERSION, "scope": scope,
            "previews": {key: value["preview_identity"] for key, value in previews.items()},
            "criteria": "原件与输入相同；BASE/STRESS均通过治理证据且逐日经济结果一致；差异必须原样报告。"}
    plan["parity_identity"] = stable_hash(plan)
    immutable(root / "FROZEN_PARITY_PLAN.json", plan)
    if not execute:
        return {"version": VERSION, "status": "PREVIEW_ONLY", "plan": plan,
                "account_executed": False, "budget_created": False, "strategy_qualified": False}
    tasks = {"old": _frozen(old_service, old_request, previews["old"]),
             "new": _frozen(new_service, new_request, previews["new"])}
    frozen_inputs = {side: _input_for_task(task) for side, task in tasks.items()}
    inputs = compare_parity_inputs_v1(frozen_inputs["old"], frozen_inputs["new"], scope)
    immutable(root / "INPUT_COMPARISON.json", inputs)
    if not inputs["passed"]:
        return {"version": VERSION, "status": "INPUT_MISMATCH", "plan": plan, "inputs": inputs,
                "tasks": tasks, "account_executed": False, "strategy_qualified": False}
    runs = {}
    for side, service in (("old", old_service), ("new", new_service)):
        task = tasks[side]
        service.approve(task["task_id"], previews[side]["preview_identity"])
        runs[side] = service.start(task["task_id"])
        verification = runs[side].get("verification", {})
        if (runs[side].get("status") != "ACCOUNT_VERIFIED"
                or verification.get("advance_allowed") is not True
                or not verification.get("items")
                or any(item.get("advance_allowed") is not True or item.get("status") != "PASS"
                       for item in verification["items"].values())):
            blocked = {"version": VERSION, "status": "EVIDENCE_BLOCKED", "plan": plan,
                       "inputs": inputs, "tasks": tasks, "runs": runs, "strategy_qualified": False}
            immutable(root / (side + "_EVIDENCE_BLOCKED.json"), blocked)
            return blocked
    values = {side: _results(task) for side, task in tasks.items()}
    comparisons = {cost: compare_parity_results_v1(values["old"][cost], values["new"][cost],
                                                   events=frozen_inputs["old"]["events"])
                   for cost in ("BASE", "STRESS")}
    passed = all(row["passed"] for row in comparisons.values())
    result = {"version": VERSION, "status": "PARITY_VERIFIED" if passed else "PARITY_MISMATCH",
              "plan": plan, "inputs": inputs, "tasks": tasks, "runs": runs, "comparisons": comparisons,
              "account_executed": True, "strategy_qualified": False, "independent_validation": "NOT_RUN",
              "paper": "NOT_RUN"}
    immutable(root / "PARITY_RESULT.json", result)
    return result


def reverify_baostock_universe_parity_v1(parity_result_path, output_root=None):
    """独立只读核验原四个账户，不批准、执行或登记任何新预算。"""
    from chanlun_trader.research_factory.research_evidence_v1 import verify_job_evidence
    source = Path(parity_result_path).absolute()
    if source.resolve() != source or not source.is_file():
        raise ValueError("PARITY_REVERIFY_ORIGINAL_RESULT_REQUIRED")
    original_bytes = source.read_bytes()
    original = json.loads(original_bytes)
    if (original.get("version") != VERSION or original.get("account_executed") is not True
            or set(original.get("tasks", {})) != {"old", "new"}):
        raise ValueError("PARITY_REVERIFY_COMPLETED_ORIGINAL_TASKS_REQUIRED")
    plan = original["plan"]
    if plan["parity_identity"] != stable_hash({key: value for key, value in plan.items() if key != "parity_identity"}):
        raise ValueError("PARITY_REVERIFY_ORIGINAL_PLAN_CHANGED")
    if read_json(source.parent / "FROZEN_PARITY_PLAN.json") != plan:
        raise ValueError("PARITY_REVERIFY_FROZEN_PLAN_CONFLICT")
    tasks = original["tasks"]
    jobs, budgets = {}, {}
    for side, task in tasks.items():
        path = Path(task["job_path"]).absolute()
        if path.resolve() != path or hashlib.sha256(path.read_bytes()).hexdigest() != task["job_sha256"]:
            raise ValueError("PARITY_REVERIFY_ORIGINAL_JOB_CHANGED")
        job = read_json(path)
        if {name: row["plan_id"] for name, row in job["plans"].items()} != task["plan_ids"]:
            raise ValueError("PARITY_REVERIFY_ORIGINAL_JOB_PLANS_CHANGED")
        if len(job["plans"]) != 2:
            raise ValueError("PARITY_REVERIFY_EXACT_FOUR_ORIGINAL_ACCOUNTS_REQUIRED")
        jobs[side] = job
        budget = Path(job["budget_path"]).absolute()
        if budget.resolve() != budget or not budget.is_file():
            raise ValueError("PARITY_REVERIFY_ORIGINAL_BUDGET_REQUIRED")
        budgets[str(budget)] = hashlib.sha256(budget.read_bytes()).hexdigest()
    checks = {side: {name: verify_job_evidence(task["job_path"], name=name)
                    for name in jobs[side]["plans"]} for side, task in tasks.items()}
    evidence_passed = all(check.get("status") == "PASS" and check.get("advance_allowed") is True
                          for rows in checks.values() for check in rows.values())
    if not evidence_passed:
        if {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in budgets} != budgets:
            raise ValueError("PARITY_BUDGET_CHANGED_DURING_READ_ONLY_REVERIFY")
        raise ValueError("PARITY_REVERIFY_ORIGINAL_EVIDENCE_NOT_PASS:" + json.dumps(checks, ensure_ascii=False))
    inputs = {side: _input_for_task(task) for side, task in tasks.items()}
    input_check = compare_parity_inputs_v1(inputs["old"], inputs["new"], plan["scope"])
    values = {side: _results(task) for side, task in tasks.items()}
    comparisons = {cost: compare_parity_results_v1(values["old"][cost], values["new"][cost],
                                                  events=inputs["old"]["events"])
                   for cost in ("BASE", "STRESS")}
    after = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in budgets}
    if after != budgets:
        raise ValueError("PARITY_BUDGET_CHANGED_DURING_READ_ONLY_REVERIFY")
    passed = evidence_passed and input_check["passed"] and all(row["passed"] for row in comparisons.values())
    result = {"version": VERSION, "operation": "READ_ONLY_REVERIFY_EXISTING_ACCOUNTS",
        "status": "PARITY_VERIFIED" if passed else "PARITY_MISMATCH" if evidence_passed else "EVIDENCE_BLOCKED",
        "source_parity_result": str(source), "source_parity_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "comparator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "plan": plan, "tasks": tasks, "inputs": input_check, "verification": checks, "comparisons": comparisons,
        "budget_proof": {"before": budgets, "after": after, "unchanged": True},
        "new_accounts_executed": False, "new_budget_registered": False,
        "strategy_qualified": False, "independent_validation": "NOT_RUN", "paper": "NOT_RUN"}
    result["verification_identity"] = stable_hash(result)
    root = Path(output_root).absolute() if output_root is not None else source.parent
    if root.resolve() != root:
        raise ValueError("PARITY_OUTPUT_REDIRECTED")
    path = root / ("VERIFIED_PARITY_" + result["verification_identity"] + ".json")
    immutable(path, result)
    return {**result, "report_path": str(path)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reverify", help="只读复核已有PARITY_RESULT.json；不重新执行账户")
    parser.add_argument("--workspace-root")
    for side in ("old", "new"):
        parser.add_argument("--" + side + "-deployment")
        parser.add_argument("--" + side + "-request")
    parser.add_argument("--output-root")
    parser.add_argument("--execute", action="store_true", help="仅使用部署已登记、覆盖四个工程账户的用户授权")
    args = parser.parse_args(argv)
    if args.reverify:
        if args.execute or any(getattr(args, key) is not None for key in
                               ("workspace_root", "old_deployment", "new_deployment", "old_request", "new_request")):
            parser.error("--reverify只允许配合--output-root")
        result = reverify_baostock_universe_parity_v1(args.reverify, args.output_root)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if result["status"] == "PARITY_VERIFIED" else 1
    if any(getattr(args, key) is None for key in
           ("workspace_root", "old_deployment", "new_deployment", "old_request", "new_request", "output_root")):
        parser.error("运行对照需提供workspace、两deployment/request及output-root")
    from scripts.lifecycle_deployment_v2 import build_submission_service
    old_service = build_submission_service(args.workspace_root, read_json(args.old_deployment))
    new_service = build_submission_service(args.workspace_root, read_json(args.new_deployment))
    result = run_baostock_universe_parity_v1(old_service, new_service,
        read_json(args.old_request), read_json(args.new_request), args.output_root, execute=args.execute)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result["status"] in {"PREVIEW_ONLY", "PARITY_VERIFIED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
