"""自包含数据包：定义、行情、状态均为生成的测试资料，无历史产物。"""
import json
from dataclasses import fields

import pandas as pd

from p3c_scenario import Scenario
from chanlun_trader.research.unified_factor import UnifiedFactorDefinition, UnifiedFactorRegistry, field_node
from chanlun_trader.research_factory.common import stable_hash


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def dataset(root):
    scenario = Scenario(root).initialize().ready()
    contract = scenario.proposal["durable_contract"]
    write_json(root / "contract.json", contract)
    # 仅测试字段和预热的传递，不恢复或声称知道真实 VOLUME_ACCEL 公式。
    attributes = {item.name: "SYNTHETIC_TEST_ONLY" for item in fields(UnifiedFactorDefinition)}
    attributes.update(factor_id="VOLUME_ACCEL", version="v1", family="VOLUME", theme=[],
        canonical_formula=field_node("volume"), operator_graph=field_node("volume"), inputs=["volume"],
        required_fields=["volume"], required_frequency="DAILY", lookback=2, min_warmup_bars=2,
        cross_sectional=False, feature_price_mode="RAW", available_at_rule="T_CLOSE", pit_safe=True,
        pit_status="PIT_VERIFIED", data_support_status="FULL", data_dependencies=[],
        source_type="DERIVED_INTERNAL", source_commit=None, attribution=[], clean_room_reimplementation=False,
        implementation_status="EXECUTABLE", lifecycle_status="DISCOVERED", is_proxy=False)
    registry = UnifiedFactorRegistry([UnifiedFactorDefinition(**attributes)])
    registry.write(root / "factors.json")
    sessions = [20240701, 20240702, 20240703, 20240704]
    symbols = ["000001.SZ", "600000.SH"]
    daily, states, factors = [], [], []
    for day in sessions:
        for symbol in symbols:
            stamp = f"{str(day)[:4]}-{str(day)[4:6]}-{str(day)[6:]}T15:00:00+08:00"
            daily.append(dict(date=day, symbol=symbol, open=10., high=11., low=9., close=10., volume=100., amount=1000., available_at=stamp, volume_unit="SHARE", amount_unit="CNY", price_mode="RAW"))
            states.append(dict(date=day, symbol=symbol, listed=True, st=False, suspended=False, liquid=True, available_at=stamp, corporate_action="NONE_CONFIRMED"))
            if day >= 20240703:
                factors.append(dict(date=day, symbol=symbol, VOLUME_ACCEL=100., available_at=stamp))
    pd.DataFrame(daily).to_parquet(root / "daily.parquet", index=False)
    pd.DataFrame(states).to_parquet(root / "state.parquet", index=False)
    pd.DataFrame(factors).to_parquet(root / "values.parquet", index=False)
    policy = "data/research/strategy_validation/validation_decision_policy_v2.json"
    bundle = dict(scope="SYNTHETIC", dataset_id="R1_SYNTHETIC", dataset_version="1",
        contract="contract.json", contract_hash=contract["content_hash"],
        policy=policy, policy_lock=policy.replace(".json", ".lock.json"),
        policy_hash=json.loads((root / policy).read_bytes())["policy_hash"],
        factor_registry="factors.json", registry_hash=stable_hash(registry.to_dict()),
        calendar_sessions=sessions, symbols=symbols, daily="daily.parquet", security_state="state.parquet", factors="values.parquet",
        manifest_claim="COMPLETE", definition_scope="SYNTHETIC_TEST_ONLY_NOT_CANONICAL_FORMULA")
    write_json(root / "readiness.json", bundle)
    return bundle
