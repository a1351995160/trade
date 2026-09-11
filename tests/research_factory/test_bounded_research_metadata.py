"""元数据诊断不读取数据行，也不把缺失时点认证为真实可执行。"""
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scripts.inspect_bounded_research_metadata import METADATA, SCHEMAS, checked_path, inspect
from chanlun_trader.research.validation_policy_v2 import (
    default_validation_decision_policy_v2, lock_payload, policy_payload,
)


def test_schema_only_preserves_missing_evidence(tmp_path, monkeypatch):
    policy = default_validation_decision_policy_v2()
    for relative in (*METADATA, *SCHEMAS):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
    policy_path = tmp_path / METADATA[1]
    policy_path.write_text(json.dumps(policy_payload(policy)), encoding="utf-8")
    (tmp_path / METADATA[2]).write_text(json.dumps(lock_payload(policy, policy_path)), encoding="utf-8")
    factor = dict(factor_id="RETURN_5D", implementation_status="EXECUTABLE",
                  required_frequency="DAILY", feature_price_mode="RETURN_ONLY", available_at_rule="T_CLOSE")
    (tmp_path / METADATA[0]).write_text(json.dumps({"factors": [factor]}), encoding="utf-8")
    for relative in SCHEMAS:
        pq.write_table(pa.table({"date": [20250801], "symbol": ["FORBIDDEN_ROW"]}), tmp_path / relative)

    def forbidden(*args, **kwargs):
        raise AssertionError("ROW_READER_MUST_NOT_RUN")

    monkeypatch.setattr(pq, "read_table", forbidden)
    result = inspect(tmp_path)
    assert result["ready_for_real_trial"] is False
    assert result["caller_compatible_factor_ids"] == []
    assert "FORBIDDEN_ROW" not in json.dumps(result)
    assert all("available_at" in value["missing_caller_fields"] for value in result["parquet_schemas"].values())
    policy_path.write_text("{}", encoding="utf-8")
    with pytest.raises(Exception, match="hash mismatch"):
        inspect(tmp_path)


def test_path_escape_rejected_before_read(tmp_path):
    with pytest.raises(ValueError, match="INPUT_PATH_REDIRECTED"):
        checked_path(tmp_path, "../outside.json")
