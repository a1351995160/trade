"""范围申请不授予权限；现场上下文与预算改变必须使原申请失效。"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from chanlun_trader.webapp import create_app
from chanlun_trader.research_factory.batch_scope_request import BatchScopeRequestServiceV1
from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.autonomous_control_plane import AutonomousResearchControlPlaneV1
from test_candidate_generation_governance_v1 import OBJECTIVE_ID, _fixture_root, _prepare


def setup(root):
    _fixture_root(root)
    service = BatchScopeRequestServiceV1(root)
    context = service.context(OBJECTIVE_ID)
    scope = {
        "schema_version": "batch-scope-request-v1", "objective_id": OBJECTIVE_ID,
        "context_hash": context["context_hash"], "data_manifest_hash": context["data_manifest_hash"],
        "dataset_ids": ["daily_ohlcva_raw"], "data_start": "2025-01-01", "data_end": "2025-06-30",
        "actions": ["GENERATE_CANDIDATE_PROPOSAL"], "model": "NONE",
        "limits": {"candidates": 1, "trials": 0, "batches": 1, "model_calls": 0, "tokens": 0,
                   "cost_minor_units": 0, "currency": "CNY", "wall_seconds": 30, "memory_mib": 256},
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "withdrawn": False, "stop_conditions": context["required_stop_conditions"],
    }
    return service, scope


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_request_is_read_only_including_default_web_policy(tmp_path):
    service, scope = setup(tmp_path)
    before = snapshot(tmp_path)
    result = service.check(OBJECTIVE_ID, json.dumps(scope))
    assert result["request_valid"], result
    assert result["execution_authorized"] is False
    assert result["authorization_status"] == "WAITING_POLICY_APPROVAL"
    with TestClient(create_app(tmp_path)) as client:
        response = client.get(f"/api/research-console/{OBJECTIVE_ID}/batch-scope-request", params={"scope": json.dumps(scope)})
        assert response.status_code == 200, response.text
        assert response.json()["request_hash"] == result["request_hash"]
        assert client.post(f"/api/research-console/{OBJECTIVE_ID}/autonomous-control-plane/tick", json={}).status_code == 403
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("key,value,code", [
    ("objective_id", "OTHER", "IDENTITY_CHANGED"),
    ("context_hash", "old", "IDENTITY_CHANGED"),
    ("data_manifest_hash", "old", "IDENTITY_CHANGED"),
    ("withdrawn", True, "REQUEST_WITHDRAWN"),
    ("expires_at", "2000-01-01T00:00:00Z", "REQUEST_EXPIRED"),
    ("expires_at", "2030-01-01T00:00:00", "timezone_aware"),
    ("data_end", "2030-01-01", "DATA_WINDOW_OUT_OF_SCOPE"),
    ("dataset_ids", ["missing"], "DATA_NOT_READY"),
    ("actions", ["AUTO_APPROVE"], "UNKNOWN_OR_DUPLICATE_ACTION"),
    ("stop_conditions", [], "too_short"),
    ("permission", True, "extra_forbidden"),
])
def test_invalid_request_never_authorizes(tmp_path, key, value, code):
    service, scope = setup(tmp_path)
    scope[key] = value
    result = service.check(OBJECTIVE_ID, json.dumps(scope))
    assert not result["request_valid"]
    assert not result["execution_authorized"]
    assert code in {item["code"] for item in result["errors"]}, result


@pytest.mark.parametrize("key,value,code", [("trials", 100, "INSUFFICIENT_CANONICAL_BUDGET"),
    ("tokens", True, "int_type"), ("candidates", -1, "greater_than_equal"),
    ("model_calls", 1, "MODEL_LIMITS_REQUIRED")])
def test_limits_are_explicit_and_do_not_change_budget(tmp_path, key, value, code):
    service, scope = setup(tmp_path)
    before = snapshot(tmp_path)
    scope["limits"][key] = value
    result = service.check(OBJECTIVE_ID, json.dumps(scope))
    assert not result["request_valid"]
    assert code in {item["code"] for item in result["errors"]}, result
    assert snapshot(tmp_path) == before


def test_data_change_invalidates_old_request(tmp_path):
    service, scope = setup(tmp_path)
    path = tmp_path / "data/research/data_capability.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["datasets"][0]["data_version"] = "synthetic-new-version"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = service.check(OBJECTIVE_ID, json.dumps(scope))
    assert not result["request_valid"]
    assert {"context_hash", "data_manifest_hash"} <= {item["field"] for item in result["errors"]}


def test_one_tick_limit_reports_stopped_after_real_proposal(tmp_path):
    root = _prepare(tmp_path)
    plane = AutonomousResearchControlPlaneV1(root, execution_policy=ExecutionPolicy("GOVERNED", "SYNTHETIC"))
    result = plane.loop(OBJECTIVE_ID, max_ticks=1)
    assert result["results"][0]["execution"]["execution_status"] == "COMPLETED"
    assert result["ticks_executed"] == 1
    assert result["stopped"] is True
    assert result["stop_reason"] == "MAX_TICKS_REACHED"
    resumed = plane.loop(OBJECTIVE_ID, max_ticks=2)
    assert resumed["ticks_executed"] == 1
    assert resumed["stop_reason"] == "HUMAN_CONFIRMATION_REQUIRED"
    assert resumed["results"][0]["execution"] is None
