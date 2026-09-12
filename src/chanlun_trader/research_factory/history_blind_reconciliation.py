"""限定历史权威目录的只读盲化投影，不创建或修复任何研究记录。"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from .budget import SearchBudgetRegistryV1
from chanlun_trader.research.strategy_validation import TrialRegistryV1


IDENTITIES = frozenset({
    "schema_version", "objective_id", "parent_objective_id", "target_objective_id",
    "programme_id", "program_id", "batch_id", "candidate_id", "candidate_hash",
    "family_id", "multiple_testing_family_id", "decision_family_id", "trial_id",
    "hypothesis_id", "policy_id", "policy_hash", "validation_policy_hash",
    "budget_reservation_identity", "reservation_id", "source_objective_id",
    "content_hash", "registry_hash", "family_snapshot_hash", "dataset_hash",
    "engine_hash", "governance_policy_hash", "created_at", "expires_at", "revoked_at",
    "authorization_id", "decision_hash", "receipt_id", "effective_from", "frozen_at",
    "family_definition_hash", "ledger_hash", "adjustment_method", "family_scope",
})
COUNTS = frozenset({"limit", "used", "reserved", "remaining", "max_total_trials",
    "max_batches", "trial_count", "event_count", "trial_number", "exposure_count",
    "validation_access_count", "train_access_count", "performance_access_count", "hypothesis_slots"})
FLAGS = frozenset({"performance_accessed", "performance_complete", "final_adjudicated",
    "registry_committed", "revoked", "immutable", "parent_family_inherited",
    "immutable_after_confirmation", "pre_registered_before_predictive_results"})
CONTAINERS = frozenset({"events", "records", "items", "candidates", "contracts",
    "members", "member_trial_ids", "trial_ids", "candidate_ids", "family_members",
    "trials", "registrations", "policy_identity", "lineage", "objective",
    "candidate_identity", "parent_candidate_identity_refs", "parent_objective_identity_refs"})
STATES = frozenset({"ACTIVE", "PAUSED", "REVOKED", "EXPIRED", "REGISTERED", "COMPLETED",
    "INVALIDATED", "SUPERSEDED", "CONSUMED", "RELEASED", "RESERVED", "FROZEN",
    "AUTHORIZED", "CONFIRMED", "PENDING", "PERFORMANCE_ACCESSED"})
TOKEN = re.compile(r"[A-Za-z0-9_:.+/@\\-]{1,400}\Z")


def blind_fields(value):
    """只输出声明字段及类型；不输出自由文本、分类、错误信息或未知字段。"""
    if isinstance(value, list):
        return [blind_fields(item) for item in value if isinstance(item, (dict, list))]
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, item in value.items():
        if key in IDENTITIES or key.endswith("_ref"):
            if isinstance(item, str) and TOKEN.fullmatch(item):
                result[key] = item
        elif key in COUNTS and type(item) is int and item >= 0:
            result[key] = item
        elif key in FLAGS and type(item) is bool:
            result[key] = item
        elif key in {"status", "authorization_status"} and isinstance(item, str) and item in STATES:
            result[key] = item
        elif key in CONTAINERS:
            if key.endswith("_ids") and isinstance(item, list):
                result[key] = [x for x in item if isinstance(x, str) and TOKEN.fullmatch(x)]
            else:
                if key == "contracts" and isinstance(item, dict):
                    result[key] = [blind_fields(v) for v in item.values() if isinstance(v, dict)]
                else:
                    result[key] = blind_fields(item)
    return result


def reconcile_history(root: Path) -> dict:
    root = root.resolve()
    patterns = ["objectives/*.json", "scope/*.json", "lineage/*.json",
                "multiple_testing/*/*.json", "batches/*/batch_plan.json",
                "batches/*/candidates.json", "batches/*/durable_frozen_candidate_contracts.json",
                "batches/*/search_budget_registry.json", "batches/*/factory_trial_ledger.json",
                "batches/*/trial_registry.json", "batches/*/trial_manifest.json"]
    paths = sorted({p for pattern in patterns for p in root.glob(pattern)})
    files, budgets, trials, conflicts = [], [], [], []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if path.resolve() != path or not path.is_file():
            conflicts.append({"path": relative, "code": "SOURCE_PATH_REDIRECTED"})
            continue
        before = path.stat()
        raw = path.read_bytes()
        try:
            payload = json.loads(raw)
            projected = blind_fields(payload)
            entry = {"path": relative, "sha256_type": "ORIGINAL_FILE_BYTES",
                     "sha256": hashlib.sha256(raw).hexdigest(), "projection": projected}
            if path.name == "search_budget_registry.json":
                # 原reader允许旧缺失计数默认0，本核验先拒绝缺字段再复用。
                if not isinstance(payload.get("buckets"), list) or not payload["buckets"]:
                    raise ValueError("BUDGET_BUCKETS_MISSING")
                if any(not {"kind", "key", "limit", "used", "reserved"} <= set(b) for b in payload["buckets"]):
                    raise ValueError("BUDGET_COUNTS_MISSING")
                budget = SearchBudgetRegistryV1(payload["objective_id"], path=path)
                snapshot = budget.snapshot()
                budget_entry = {"path": relative, "objective_id": projected.get("objective_id"),
                    "buckets": snapshot["buckets"], "active_reservations": snapshot["active_reservations"],
                    "settled_reservations": snapshot["settled_reservations"]}
                budgets.append(budget_entry)
            if path.name == "trial_registry.json":
                latest = TrialRegistryV1(path).latest()
                events = TrialRegistryV1(path).events()
                for trial_id, record in latest.items():
                    history = [e for e in events if e.get("trial_id") == trial_id]
                    exposed = any(e.get("performance_accessed") is True for e in history)
                    known = all(type(e.get("performance_accessed")) is bool for e in history)
                    trials.append({"path": relative, **blind_fields(record),
                                   "ever_performance_accessed": exposed if exposed or known else None,
                                   "event_count_observed": len(history)})
            files.append(entry)
        except Exception as exc:
            # 异常消息可能包含原始数据，日志只返回类型。
            conflicts.append({"path": relative, "code": "SCHEMA_OR_READER_REJECTED",
                              "exception_type": type(exc).__name__})
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            conflicts.append({"path": relative, "code": "SOURCE_CHANGED_DURING_READ"})
    batch_dirs = sorted(p for p in (root / "batches").iterdir() if p.is_dir())
    missing = [{"batch": p.name, "missing": [name for name in
               ("search_budget_registry.json", "trial_registry.json", "factory_trial_ledger.json")
               if not (p / name).is_file()]} for p in batch_dirs]
    duplicates = [key for key, count in Counter(t.get("trial_id") for t in trials).items() if count > 1]
    objectives = {f["projection"].get("objective_id"): f["projection"] for f in files if f["path"].startswith("objectives/")}
    for budget in budgets:
        oid = budget["objective_id"]
        bucket = next((b for b in budget["buckets"] if b["kind"] == "objective" and b["key"] == oid), None)
        source = objectives.get(oid, {})
        if bucket and source.get("max_total_trials") != bucket["limit"]:
            conflicts.append({"path": budget["path"], "code": "OBJECTIVE_BUDGET_LIMIT_REQUIRES_AUTHORIZATION_LINEAGE",
                              "objective_limit": source.get("max_total_trials"), "budget_limit": bucket["limit"]})
        consumed = sum(v == "CONSUMED" for v in budget["settled_reservations"].values())
        budget["settlement_counts"] = dict(Counter(budget["settled_reservations"].values()))
        if bucket and consumed != bucket["used"]:
            conflicts.append({"path": budget["path"], "code": "SETTLEMENT_USED_COUNT_MISMATCH"})
        related = [t for t in trials if t["path"].rsplit("/", 1)[0] == budget["path"].rsplit("/", 1)[0]]
        budget["observed_exposed_trials_in_same_directory"] = sum(t["ever_performance_accessed"] is True for t in related)
        budget["trial_registry_present"] = (root / budget["path"]).with_name("trial_registry.json").is_file()
    return {"schema_version": "history-budget-blind-reconciliation-v2", "authority": False,
        "status": "PARTIAL_HISTORY_NOT_ZERO", "source_root": str(root), "files": files,
        "budgets": budgets, "trials": trials, "missing_by_batch": [x for x in missing if x["missing"]],
        "conflicts": conflicts, "duplicate_trial_ids_across_files": duplicates,
        "observed_unique_trial_ids": len({t.get("trial_id") for t in trials}),
        "observed_exposed_trial_ids": len({t.get("trial_id") for t in trials if t["ever_performance_accessed"] is True}),
        "train_exposure_total": None, "validation_exposure_total": None,
        "exposure_split_reason": "NO_VERIFIED_WINDOW_ACCESS_LEDGER_IN_THIS_PROJECTION",
        "global_remaining_budget": None, "new_performance_trials": 0,
        "writes_to_authority": 0, "outcome_values_returned": False}
