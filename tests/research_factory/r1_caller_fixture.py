"""首次审批前声明合同；输入均现场生成，不使用历史研究文件。"""
from dataclasses import fields, replace
import json
import hashlib

import pandas as pd

from p3c_scenario import Scenario
from r1_fixture import write_json
from chanlun_trader.research.unified_factor import UnifiedFactorDefinition, UnifiedFactorRegistry, field_node
from chanlun_trader.research.strategy_candidate import StrategyCandidateSpec
from chanlun_trader.research.strategy_semantic import build_semantic_record, _semantic_fingerprint
from chanlun_trader.research.validation_policy_v2 import load_validation_decision_policy_v2
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.durability import DurableFrozenCandidateContractV1
from chanlun_trader.research_factory.predictive_executor import CanonicalPredictiveExecutorV1


def synthetic_factor_registry():
    attrs = {field.name: "SYNTHETIC_TEST_ONLY" for field in fields(UnifiedFactorDefinition)}
    attrs.update(factor_id="VOLUME_ACCEL", version="v1", family="VOLUME", theme=[],
        canonical_formula=field_node("volume"), operator_graph=field_node("volume"), inputs=["volume"],
        required_fields=["volume"], required_frequency="DAILY", lookback=2, min_warmup_bars=2,
        cross_sectional=False, feature_price_mode="RAW", available_at_rule="T_CLOSE", pit_safe=True,
        pit_status="PIT_VERIFIED", data_support_status="FULL", data_dependencies=[],
        source_type="DERIVED_INTERNAL", source_commit=None, attribution=[], clean_room_reimplementation=False,
        implementation_status="EXECUTABLE", lifecycle_status="DISCOVERED", is_proxy=False)
    return UnifiedFactorRegistry([UnifiedFactorDefinition(**attrs)])


def fixture(root, *, structure_exit=False, file_registry_identity=False, sessions=None, validation_ready=False, objective_id=None):
    scenario = (Scenario(root) if objective_id is None else Scenario(root, objective_id)).initialize()
    sessions = sessions if sessions is not None else [20250715, 20250716, 20250717, 20250718, 20250721, 20250722,
                20250723, 20250724, 20250725, 20250728, 20250729, 20250730, 20250731]
    policy_path = root / "data/research/strategy_validation/validation_decision_policy_v2.json"
    policy, _ = load_validation_decision_policy_v2(policy_path)
    registry = synthetic_factor_registry()
    registry_path = root / "data/research/factor_library_v1/registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry.write(registry_path)
    objective_path = root / f"data/research/research_factory/objectives/{scenario.objective_id}.json"
    objective = json.loads(objective_path.read_bytes())
    objective["policy_identity"].update({key: getattr(policy, key) for key in ("policy_id", "policy_version", "policy_hash")})
    objective["factor_event_registry_identities"]["factor_registry"]["hash"] = stable_hash(registry.to_dict())
    if file_registry_identity:
        registry_path = root / "data/research/unified_factor_registry/registry.json"
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry.write(registry_path)
        objective["factor_event_registry_identities"]["factor_registry"] = {
            "path": registry_path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(registry_path.read_bytes()).hexdigest(),
        }
    objective["research_period_identity"] = {"id": "R1_CALLER_SYNTHETIC", "start": sessions[2], "end": sessions[-1]}
    write_json(objective_path, objective)
    return materialized_fixture(root, scenario, policy, sessions, structure_exit=structure_exit, validation_ready=validation_ready)


def materialized_fixture(root, scenario, policy, sessions, *, structure_exit=False, validation_ready=False, holding_period=None):
    """只通过真实审批/冻结/物化构建候选与合成行情；不写 Objective、预算或家族。"""
    objective = json.loads((root / f"data/research/research_factory/objectives/{scenario.objective_id}.json").read_bytes())
    design = scenario.design_input()
    old = DurableFrozenCandidateContractV1.from_dict(design["durable_contract"])
    seed = old.reconstruct_candidate().candidate.to_dict()
    seed["max_positions"] = policy.max_positions
    if holding_period is not None:
        seed["holding_period"] = holding_period
    if validation_ready:
        seed["candidate_status"] = "VALIDATION_READY"
        seed["risk_filters"] = [{"type": "PIT_UNIVERSE"}]
        seed["execution_filters"] = [{"type": "TRADABILITY", "price_limit": "FAIL_CLOSED", "suspension": "FAIL_CLOSED"}]
        seed["ranking_rule"].update(status="DETERMINISTIC", tie_breaker="SYMBOL_ASC")
        seed["entry_timing"]["execution_time"] = "NEXT_SESSION_OPEN"
        seed["t_plus_1_contract"]["same_session_sell_forbidden"] = True
        seed["exit_rule"]["holding_period_trading_sessions"] = seed["holding_period"]
    record = build_semantic_record(StrategyCandidateSpec.create(seed), design["hypothesis"])
    if structure_exit:
        # 新合成设计在首次审批前显式声明零成交活动结构失效，不改已冻结记录。
        record = replace(record, exit_predicate=replace(record.exit_predicate,
            exit_type="STRUCTURE_INVALIDATION", logic="OR",
            factor_conditions=({"factor_id": "VOLUME_ACCEL", "operator": "LE", "value": 0.},)))
        record = replace(record, semantic_fingerprint=_semantic_fingerprint(record.signal_predicate, record.exit_predicate))
    contract = DurableFrozenCandidateContractV1.from_semantic_record(record, design["hypothesis"],
        factor_event_registry_identities=objective["factor_event_registry_identities"],
        research_period_identity=objective["research_period_identity"], policy_identity=objective["policy_identity"],
        source_provenance=dict(old.source_provenance), created_frozen_timestamp=old.created_frozen_timestamp)
    design["durable_contract"] = contract.to_dict()
    scenario.design(design)
    scenario.approve()
    assert scenario.plane.tick(scenario.objective_id)["execution"]["execution_status"] == "COMPLETED"
    scenario.freeze()
    assert scenario.plane.tick(scenario.objective_id)["execution"]["execution_status"] == "COMPLETED"
    scenario.confirm()
    contract = DurableFrozenCandidateContractV1.from_dict(scenario.proposal["durable_contract"])
    record = contract.reconstruct_candidate()
    symbols = ["000001.SZ", "600000.SH"]
    normalized = root / "data/research/security_state/normalized"
    for folder, status in (("st_state", "NORMAL"), ("suspension_state", "TRADING")):
        (normalized / folder).mkdir(parents=True, exist_ok=True)
        for symbol in symbols:
            (normalized / folder / ("symbol=" + symbol.replace(".", "_") + ".jsonl")).write_text(
                "\n".join(json.dumps(dict(trade_date=day, status=status)) for day in sessions[2:]), encoding="utf-8")
    for relative, value in {
        "data/research/security_state/normalized/security_master_v2/records.json": [dict(symbol=s, exchange=s[-2:], list_date=20200101) for s in symbols],
        "data/research/security_state/raw/trade_calendar.json": {"trade_dates": sessions},
        "data/research/data_routing/routing_policy.json": {"scope": "SYNTHETIC_TEST_ONLY"},
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, value)
    daily = pd.DataFrame([dict(date=d, symbol=s, open=10., high=11., low=9., close=10., prev_close=10.,
        volume=1000000., amount=10000000., available_at=f"{str(d)[:4]}-{str(d)[4:6]}-{str(d)[6:]}T15:00:00+08:00",
        volume_unit="SHARE", amount_unit="CNY", price_mode="RAW") for d in sessions for s in symbols])
    factors = daily.loc[daily.date >= sessions[2], ["date", "symbol", "volume", "available_at"]].copy()
    factors["VOLUME_ACCEL"] = factors.volume
    if structure_exit:
        daily.loc[daily.date.isin(sessions[4:6]), "volume"] = 0.
        daily.loc[daily.date.isin(sessions[4:6]), "amount"] = 0.
        factors.loc[factors.date.isin(sessions[4:6]), ["volume", "VOLUME_ACCEL"]] = 0.
    daily.to_parquet(root / "data/research/daily_all.parquet", index=False)
    cache = root / "data/research/strategy_validation/phase4_rerun_v2_factor_values.parquet"
    factors.to_parquet(cache, index=False)
    return CanonicalPredictiveExecutorV1(root, scenario.objective_id), policy, contract, record, cache
