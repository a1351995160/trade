"""限定真实输入的元数据核验；不读行情行、绩效或创建运行许可。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq

from chanlun_trader.research.validation_policy_v2 import load_validation_decision_policy_v2


INPUT_ROOT = Path("E:/llmwiki/chanlun-trading-system")
METADATA = (
    "data/research/factor_library_v1/registry.json",
    "data/research/strategy_validation/validation_decision_policy_v2.json",
    "data/research/strategy_validation/validation_decision_policy_v2.lock.json",
)
SCHEMAS = {
    "data/research/daily_all.parquet": {
        "date", "symbol", "open", "high", "low", "close", "volume", "amount",
        "prev_close", "volume_unit", "amount_unit", "price_mode", "available_at",
    },
    "data/research/strategy_validation/phase4_rerun_v2_factor_values.parquet": {
        "date", "symbol", "volume", "available_at",
    },
}


def checked_path(root: Path, relative: str) -> Path:
    path = root / relative
    if path.resolve() != path.absolute() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("INPUT_PATH_REDIRECTED")
    if not path.is_file():
        raise FileNotFoundError(relative)
    return path


def inspect(root: Path) -> dict:
    """仅三个已批准JSON全文与两个已定位Parquet的schema/footer。"""
    root = root.absolute()
    files = {name: checked_path(root, name) for name in (*METADATA, *SCHEMAS)}
    policy, _ = load_validation_decision_policy_v2(files[METADATA[1]], files[METADATA[2]])
    registry = json.loads(files[METADATA[0]].read_text(encoding="utf-8"))
    factors = registry["factors"]
    supported = [f["factor_id"] for f in factors if
                 f["implementation_status"] == "EXECUTABLE" and
                 f["required_frequency"] == "DAILY" and
                 f["feature_price_mode"] == "RAW" and
                 f["available_at_rule"] == "T_CLOSE"]
    schemas = {}
    for name, required in SCHEMAS.items():
        names = pq.read_schema(files[name]).names
        schemas[name] = {"fields": names, "missing_caller_fields": sorted(required - set(names)),
                         "row_data_read": False, "whole_file_hash_computed": False}
    return {
        "schema_version": "bounded-research-metadata-inspection-v1",
        "scope": "REAL_METADATA_ONLY", "ready_for_real_trial": False,
        "policy_hash": policy.policy_hash,
        "research_window": [policy.research_start, policy.research_end],
        "metadata_file_sha256": {name: hashlib.sha256(files[name].read_bytes()).hexdigest() for name in METADATA},
        "caller_compatible_factor_ids": supported,
        "return_5d_definition": next(f for f in factors if f["factor_id"] == "RETURN_5D"),
        "parquet_schemas": schemas,
        "unverified": ["HISTORICAL_PROVENANCE", "CALENDAR_WINDOW", "PIT_STATE",
                       "CORPORATE_ACTIONS", "CANONICAL_BUDGET_AND_HISTORY",
                       "STATISTICAL_APPLICABILITY", "USER_PROGRAM_CONFIRMATION"],
        "performance_trials_started": 0,
    }


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(inspect(INPUT_ROOT), ensure_ascii=False, indent=2))
