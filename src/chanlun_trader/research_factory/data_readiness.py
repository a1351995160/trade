"""候选绑定的只读合成数据核验；诊断结果不是研究执行许可。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from chanlun_trader.execution_policy import ExecutionPolicy, validate_research_root
from chanlun_trader.research.guard import ResearchDataAccessGuard
from chanlun_trader.research.io_safety import GuardedResearchReader
from chanlun_trader.research.strategy_semantic import StrategyCandidateCompilerV2
from chanlun_trader.research.unified_factor import AsOfDataView, FactorCompiler, UnifiedFactorRegistry, dependency_ids
from chanlun_trader.research.validation_policy_v2 import load_validation_decision_policy_v2
from .common import stable_hash
from .durability import DurableFrozenCandidateContractV1
from .source_dependencies import SOURCE_ROOT


OHLCVA = ["open", "high", "low", "close", "volume", "amount"]


def derive_requirements(contract, policy, registry):
    """复用合同重建、语义编译和冻结因子定义，不推测缺失的定义。"""
    payload = contract.provider_candidate_payload()
    StrategyCandidateCompilerV2().compile(contract.reconstruct_candidate())
    factors, missing, visiting = {}, [], set()

    def visit(factor_id):
        if factor_id in visiting:
            raise ValueError("FACTOR_DEPENDENCY_CYCLE")
        if factor_id in factors:
            return
        definition = registry.get(factor_id)
        if definition is None:
            missing.append("FACTOR_DEFINITION:" + factor_id)
            return
        visiting.add(factor_id)
        for dependency in dependency_ids(definition.operator_graph):
            visit(dependency)
        visiting.remove(factor_id)
        factors[factor_id] = definition.to_dict()

    for factor_id in contract.factor_ids:
        visit(factor_id)
    return {
        "objective_id": contract.policy_identity.get("objective_id"),
        "candidate_id": contract.candidate_id, "candidate_hash": contract.candidate_hash,
        "contract_schema": contract.contract_schema_version, "contract_hash": contract.content_hash,
        "policy_identity": dict(contract.policy_identity), "validated_policy_hash": policy.policy_hash,
        "registry_identities": dict(contract.factor_event_registry_identities),
        "research_period_identity": dict(contract.research_period_identity),
        "frequency": payload["required_frequency"], "required_data": payload["required_data"],
        "daily_fields": sorted(set(OHLCVA).union(*(set(d["required_fields"]) for d in factors.values()))),
        "factor_definitions": factors, "event_ids": list(contract.event_ids),
        "warmup_bars": max((max(d["lookback"], d["min_warmup_bars"]) for d in factors.values()), default=0) if not missing else None,
        "pit_dependencies": dict(contract.pit_dependencies),
        "entry_timing": dict(contract.entry_timing), "holding_sessions": contract.holding_period_trading_sessions,
        "price_mode": payload["price_mode"], "timezone": "Asia/Shanghai",
        "execution_dependencies": {"daily": [*OHLCVA, "prev_close"], "benchmark": "corrected.legacy.load_market_index", "minute": "NOT_VERIFIED_FOR_CORRECTED_RUNNER", "corporate_action": "KNOWN_NONE_ONLY; processor unsupported"},
        "available_at": dict(payload["available_at_contract"]),
        "missing": missing,
        "unverified": ["CORRECTED_EXECUTION_INPUT_PARITY", "REAL_DATA", "HISTORICAL_SOURCE_AVAILABILITY"],
    }


def inspect_dataset(source_root, dataset_root, *, start, end, previous_identity=None):
    """核验临时自包含合成包。真实目录入口留待具体访问范围批准。"""
    report = {
        "tool_version": "r1-data-readiness-v1", "scope": "SYNTHETIC_DAILY_INPUTS",
        "status": "NOT_VERIFIED", "real_candidate_data_readiness": "NOT_VERIFIED",
        "ready_for_real_trial": False, "real_data_access_authorized": False,
        "missing": [], "conflicts": [], "unverified": [], "reads": [],
    }
    try:
        if not isinstance(start, int) or not isinstance(end, int) or start > end:
            raise ValueError("INVALID_WINDOW")
        for day in (start, end):
            pd.Timestamp(str(day))
        ResearchDataAccessGuard().check_range(start, end)
        source = Path(source_root)
        if not source.is_absolute() or validate_research_root(source, ExecutionPolicy()) != SOURCE_ROOT:
            raise ValueError("SOURCE_ROOT_CONFLICT")
        # 先做词法边界，再检查目录；不得扫描原研究工作区来判断它是否合法。
        root = Path(dataset_root)
        # tempfile.gettempdir 首次调用会试写探测文件；inspect 不使用该副作用。
        temporary = Path(os.environ.get("TMPDIR") or os.environ.get("TEMP") or os.environ.get("TMP") or ("C:/Temp" if os.name == "nt" else "/tmp")).resolve()
        if not root.is_absolute() or root == temporary or not root.is_relative_to(temporary):
            raise ValueError("SYNTHETIC_TEMP_ROOT_REQUIRED")
        root = validate_research_root(root, ExecutionPolicy(workspace_kind="SYNTHETIC"))
        if root.is_relative_to(source) or source.is_relative_to(root):
            raise ValueError("SOURCE_DATA_ROOT_OVERLAP")
        fingerprints = {}

        def file(reference):
            validate_research_root(root, ExecutionPolicy(workspace_kind="SYNTHETIC"))
            relative = Path(reference)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("REFERENCE_PATH_ESCAPE")
            path = root / relative
            if not path.is_file() or not path.resolve().is_relative_to(root):
                raise FileNotFoundError(str(relative))
            return path

        def digest(path):
            return hashlib.sha256(path.read_bytes()).hexdigest()

        def document(reference):
            path = file(reference)
            raw = path.read_bytes()
            fingerprints[path] = hashlib.sha256(raw).hexdigest()
            return json.loads(raw)

        bundle = document("readiness.json")
        if bundle["scope"] != "SYNTHETIC" or not bundle["dataset_id"] or not bundle["dataset_version"]:
            raise ValueError("DATASET_IDENTITY_REQUIRED")
        contract = DurableFrozenCandidateContractV1.from_dict(document(bundle["contract"]))
        document(bundle["policy"])
        document(bundle["policy_lock"])
        policy, _ = load_validation_decision_policy_v2(file(bundle["policy"]), file(bundle["policy_lock"]))
        registry_payload = document(bundle["factor_registry"])
        registry = UnifiedFactorRegistry.read(file(bundle["factor_registry"]))
        requirements = derive_requirements(contract, policy, registry)
        report["requirements"] = requirements
        if not requirements["objective_id"]:
            raise ValueError("OBJECTIVE_IDENTITY_REQUIRED")
        report["missing"].extend(requirements["missing"])
        report["unverified"].extend(requirements["unverified"])
        # 诊断包显式钉住引用；不反向改写 P3-C 合同内的简化政策/registry ID。
        if bundle["contract_hash"] != contract.content_hash or bundle["policy_hash"] != policy.policy_hash or bundle["registry_hash"] != stable_hash(registry_payload):
            raise ValueError("INPUT_IDENTITY_CONFLICT")
        for key in ("policy_id", "policy_version", "policy_hash"):
            if contract.policy_identity.get(key) != getattr(policy, key):
                raise ValueError("POLICY_REFERENCE_CONFLICT:" + key)
        if contract.factor_event_registry_identities.get("factor_registry", {}).get("hash") != stable_hash(registry_payload):
            raise ValueError("REGISTRY_REFERENCE_CONFLICT")
        period = contract.research_period_identity
        if not max(int(period["start"]), policy.research_start) <= start <= end <= min(int(period["end"]), policy.research_end):
            raise ValueError("CONTRACT_POLICY_WINDOW_CONFLICT")
        sessions = bundle["calendar_sessions"]
        if not sessions or sessions != sorted(set(sessions)) or start not in sessions or end not in sessions:
            raise ValueError("CALENDAR_CONFLICT")
        ResearchDataAccessGuard().check_int_iterable(sessions)
        symbols = bundle["symbols"]
        if not symbols or len(set(symbols)) != len(symbols):
            raise ValueError("UNIVERSE_IDENTITY_REQUIRED")
        warmup = requirements["warmup_bars"]
        if warmup is None:
            raise ValueError("WARMUP_NOT_DERIVABLE")
        index = sessions.index(start)
        if index < warmup:
            report["missing"].append("WARMUP_SESSIONS")
            raise ValueError("INSUFFICIENT_WARMUP")
        days = sessions[index - warmup:sessions.index(end) + 1]
        for day in days:
            pd.Timestamp(str(day))
        requirements["requested_window"] = [start, end]
        requirements["warmup_start"] = days[0]
        requirements["calendar_hash"] = stable_hash(sessions)
        requirements["universe_hash"] = stable_hash(symbols)
        if requirements["event_ids"] or set(requirements["frequency"]) != {"DAILY"} or requirements["price_mode"] != "RAW":
            report["unverified"].append("UNSUPPORTED_EVENT_FREQUENCY_OR_ADJUSTMENT")
        for definition in requirements["factor_definitions"].values():
            if definition["implementation_status"] != "EXECUTABLE" or definition["required_frequency"] != "DAILY" or definition["feature_price_mode"] != "RAW":
                report["unverified"].append("UNSUPPORTED_FACTOR:" + definition["factor_id"])
        reader = GuardedResearchReader(audit_sink=report["reads"].append)

        def table(kind, columns):
            path = file(bundle[kind])
            # 整个合成分片须位于获准窗口。先看日期统计，再读内容/计算文件哈希。
            metadata = pq.ParquetFile(path).metadata
            for i in range(metadata.num_row_groups):
                group = metadata.row_group(i)
                dates = [group.column(j) for j in range(group.num_columns) if group.column(j).path_in_schema == "date"]
                if len(dates) != 1 or dates[0].statistics is None or not dates[0].statistics.has_min_max:
                    raise ValueError("PARTITION_DATE_EVIDENCE_MISSING")
                stats = dates[0].statistics
                if stats.min < days[0] or stats.max > end:
                    raise ValueError("PARTITION_OUTSIDE_APPROVED_WINDOW")
            fingerprints[path] = digest(path)
            frame = reader.read_parquet(path, columns=columns, start_date=days[0], end_date=end)
            if frame.duplicated(["date", "symbol"]).any():
                raise ValueError("DUPLICATE_ROWS:" + kind)
            if not set(frame["symbol"]).issubset(symbols) or not set(frame["date"]).issubset(days):
                raise ValueError("UNIVERSE_CALENDAR_CONFLICT:" + kind)
            return frame

        state = table("security_state", ["date", "symbol", "listed", "st", "suspended", "liquid", "available_at", "corporate_action"])
        expected = {(d, s) for d in days for s in symbols}
        observed_state = set(zip(state["date"], state["symbol"]))
        if observed_state != expected:
            report["missing"].append("PIT_STATE_COVERAGE")
        for column in ("listed", "st", "suspended", "liquid"):
            if not state[column].map(lambda value: isinstance(value, (bool, np.bool_))).all():
                raise ValueError("UNKNOWN_SECURITY_STATE:" + column)
        if not state["corporate_action"].eq("NONE_CONFIRMED").all():
            report["unverified"].append("CORPORATE_ACTION_UNKNOWN_OR_UNSUPPORTED")
        eligible = state[state["listed"] & ~state["suspended"]]
        expected_daily = set(zip(eligible["date"], eligible["symbol"]))
        daily = table("daily", ["date", "symbol", *requirements["daily_fields"], "available_at", "volume_unit", "amount_unit", "price_mode"])
        if set(zip(daily["date"], daily["symbol"])) != expected_daily:
            report["missing"].append("DAILY_COVERAGE_OR_UNEXPECTED_ROWS")
        report["coverage"] = {"expected_state_rows": len(expected), "actual_state_rows": len(state), "expected_daily_rows": len(expected_daily), "actual_daily_rows": len(daily)}
        numeric = daily[requirements["daily_fields"]].apply(pd.to_numeric, errors="coerce")
        if not np.isfinite(numeric.to_numpy()).all() or (numeric[OHLCVA] < 0).any().any():
            raise ValueError("INVALID_NUMERIC_OR_UNITS")
        if (numeric[["open", "high", "low", "close"]] <= 0).any().any():
            raise ValueError("NON_POSITIVE_DAILY_PRICE")
        if (daily["high"] < daily[["open", "low", "close"]].max(axis=1)).any() or (daily["low"] > daily[["open", "high", "close"]].min(axis=1)).any():
            raise ValueError("IMPOSSIBLE_OHLC")
        if not (daily["volume_unit"].eq("SHARE") & daily["amount_unit"].eq("CNY") & daily["price_mode"].eq("RAW")).all():
            raise ValueError("UNIT_OR_PRICE_MODE_CONFLICT")

        def availability(frame):
            if not frame["available_at"].map(lambda value: isinstance(value, str) and pd.Timestamp(value).tzinfo is not None).all():
                raise ValueError("TIMEZONE_REQUIRED")
            stamps = pd.to_datetime(frame["available_at"], utc=True, errors="coerce")
            close = pd.to_datetime(frame["date"].astype(str) + " 15:00:00").dt.tz_localize("Asia/Shanghai")
            if stamps.isna().any() or (stamps > close).any():
                raise ValueError("FUTURE_OR_INVALID_AVAILABLE_AT")

        availability(state)
        availability(daily)
        factors = table("factors", ["date", "symbol", *contract.factor_ids, "available_at"])
        availability(factors)
        signal_expected = {(d, s) for d, s in expected_daily if start <= d <= end}
        actual_factors = set(zip(factors["date"], factors["symbol"]))
        if not signal_expected.issubset(actual_factors):
            report["missing"].append("FACTOR_COVERAGE")
        if not np.isfinite(factors[list(contract.factor_ids)].apply(pd.to_numeric, errors="coerce").to_numpy()).all():
            raise ValueError("INVALID_FACTOR_VALUES")
        universe = {day: sorted(state.loc[(state["date"] == day) & state["listed"] & ~state["st"] & ~state["suspended"] & state["liquid"], "symbol"]) for day in days}
        view = AsOfDataView(daily, bundle["dataset_version"], requirements["universe_hash"], universe_by_date=universe)
        for factor_id in contract.factor_ids:
            definition = registry.get(factor_id)
            if definition is None or definition.implementation_status != "EXECUTABLE":
                continue
            computed = FactorCompiler(registry=registry).execute(definition, view, as_of=end)
            computed = computed.rename(columns={"timestamp": "date"})
            comparison = factors.merge(computed[["date", "symbol", "value"]], on=["date", "symbol"], how="left", validate="one_to_one")
            if not np.allclose(comparison[factor_id], comparison["value"], rtol=0, atol=0, equal_nan=False):
                raise ValueError("FACTOR_DEFINITION_VALUE_CONFLICT:" + factor_id)
        # 未声称数值核验代替因子来源真实性、公司行动处理器或 corrected 执行认证。
        source_identity = {str(p.relative_to(source)): digest(p) for p in (Path(__file__), source / "src/chanlun_trader/research/io_safety.py", source / "src/chanlun_trader/research/unified_factor.py", source / "src/chanlun_trader/research_factory/durability.py", source / "src/chanlun_trader/research/strategy_semantic.py")}
        report["input_identity"] = stable_hash({"files": {str(p.relative_to(root)): h for p, h in fingerprints.items()}, "window": [start, end], "source": source_identity, "tool": report["tool_version"]})
        report["previous_identity_matches"] = previous_identity == report["input_identity"] if previous_identity else None
        validate_research_root(root, ExecutionPolicy(workspace_kind="SYNTHETIC"))
        if any(digest(path) != value for path, value in fingerprints.items()):
            raise ValueError("INPUT_CHANGED_DURING_INSPECTION")
        validate_research_root(root, ExecutionPolicy(workspace_kind="SYNTHETIC"))
        blocking_unknown = set(report["unverified"]) - set(requirements["unverified"])
        report["status"] = "SYNTHETIC_SCOPE_READY" if not report["missing"] and not blocking_unknown else "INSUFFICIENT_OR_UNVERIFIED"
    except FileNotFoundError as exc:
        report["status"] = "INSUFFICIENT_OR_CONFLICT"
        report["missing"].append(f"FileNotFoundError:{exc}")
    except (ValueError, KeyError, RuntimeError, TypeError) as exc:
        report["status"] = "INSUFFICIENT_OR_CONFLICT"
        report["conflicts"].append(f"{type(exc).__name__}:{exc}")
    return report


def main():
    parser = argparse.ArgumentParser(description="只读核验临时合成候选数据，不授权真实研究")
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(inspect_dataset(args.source_root, args.dataset_root, start=args.start, end=args.end), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
