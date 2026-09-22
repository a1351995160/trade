"""V2 公共入口验收：API / CLI / Web 使用同一服务，语义一致。

- 合同端点由**注册表动态提供**（界面无需硬编码 MACD/KDJ）；
- HTTP 与 CLI 对同一请求给出相同的语义事件与账户结果；
- 默认只读策略下端点仍被拒绝（写入边界未被放宽）；
- 不支持组合明确报错，不静默回落。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from chanlun_trader.engine.behavior_service_v2 import BEHAVIOR_MODE_V2
from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.webapp import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]


def _days(count: int = 130) -> list:
    days = [int((pd.Timestamp("2024-09-02") + pd.Timedelta(days=i)).strftime("%Y%m%d"))
            for i in range(count + 60)]
    return [d for d in days if pd.Timestamp(str(d)).weekday() < 5][:count]


def _bars(count: int = 130) -> list:
    days = _days(count)
    closes = [10.0 + 0.06 * i + 0.8 * np.sin(i / 7) for i in range(count)]
    volumes = [1_000_000.0 * (1 + 0.5 * np.sin(i / 4)) for i in range(count)]
    return [{"date": days[i], "open": closes[i] * 0.999, "high": closes[i] * 1.02,
             "low": closes[i] * 0.98, "close": closes[i], "volume": volumes[i],
             "amount": closes[i] * volumes[i]} for i in range(count)]


def _payload() -> dict:
    days = _days()
    return {
        "mode": BEHAVIOR_MODE_V2,
        "calendar": days,
        "symbols": ["600000.SH"],
        "bars": {"600000.SH": _bars()},
        "indicators": [{"indicator_id": "RSI", "params": {"window": 14}},
                       {"indicator_id": "EMA", "params": {"window": 20}}],
        "entry_condition": {
            "op": "and",
            "args": [
                {"op": "gt", "args": [{"op": "indicator", "args": ["RSI"],
                                       "params": {"output": "rsi"}},
                                      {"op": "const", "params": {"value": 40}}]},
                {"op": "gt", "args": [{"op": "field", "args": ["close"]},
                                      {"op": "indicator", "args": ["EMA"],
                                       "params": {"output": "ema"}}]},
            ],
        },
        "exit_rules": {"stop_loss_pct": 0.05, "fixed_holding_sessions": 10},
        "initial_cash": 100_000.0,
        "max_positions": 1,
        "max_position_weight": 1.0,
    }


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    root = tmp_path / "synthetic_root"
    root.mkdir()
    app = create_app(root, ExecutionPolicy(
        mode="READ_ONLY", workspace_kind="SYNTHETIC", allow_readonly_compute=True))
    return TestClient(app)


def _run_cli(extra_args):
    return subprocess.run(
        [sys.executable, "scripts/run_behavior_backtest_v2.py", *extra_args],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


# --------------------------------------------------------------------------
# 1. 合同端点由注册表动态提供
# --------------------------------------------------------------------------
def test_contracts_endpoint_is_registry_driven(client: TestClient):
    response = client.get("/api/backtest/behavior/contracts")
    assert response.status_code == 200
    body = response.json()
    assert BEHAVIOR_MODE_V2 in body["supported_modes_v2"]
    assert body["registry_version"] == "BT_INDICATOR_REGISTRY_V2"
    ids = {item["indicator_id"] for item in body["indicators"]}
    # 多家族指标都在，而不是只有 MACD/KDJ
    assert {"MACD", "KDJ", "RSI", "BOLLINGER", "ATR", "DMI", "SAR", "OBV", "MFI",
            "CCI", "WILLIAMS_R", "ROC", "KELTNER", "DONCHIAN", "OBV"} <= ids
    assert len(body["families"]) >= 6
    for item in body["indicators"]:
        assert item["outputs"], item["indicator_id"]
        assert "params" in item and "warmup_bars" in item
    # 自定义 fixture 与退出类型
    assert "BREAKOUT_WITH_VOLUME_CONFIRMATION" in body["custom_entry_conditions"]
    assert "ATR_DISTANCE_STOP" in body["supported_exit_types_v2"]
    assert "INTRADAY_TOUCH_STOP" in body["unsupported_exit_types_v2"]


def test_default_read_only_policy_still_denies_v2_endpoint(tmp_path: Path):
    """默认策略下 V2 端点与其它 POST 一样被拒绝：写入边界未被放宽。"""
    root = tmp_path / "synthetic_root"
    root.mkdir()
    app = create_app(root, ExecutionPolicy(mode="READ_ONLY", workspace_kind="SYNTHETIC"))
    response = TestClient(app).post("/api/backtest/behavior/v2", json=_payload())
    assert response.status_code == 403
    assert response.json()["code"] == "EXECUTION_POLICY_READ_ONLY"


def test_v2_endpoint_does_not_write_research_root(tmp_path: Path):
    root = tmp_path / "synthetic_root"
    root.mkdir()
    app = create_app(root, ExecutionPolicy(
        mode="READ_ONLY", workspace_kind="SYNTHETIC", allow_readonly_compute=True))
    client = TestClient(app)
    before = {p.name for p in root.rglob("*")}
    assert client.post("/api/backtest/behavior/v2", json=_payload()).status_code == 200
    assert {p.name for p in root.rglob("*")} == before


def test_v2_endpoint_runs_and_reports_versions(client: TestClient):
    response = client.post("/api/backtest/behavior/v2", json=_payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == BEHAVIOR_MODE_V2
    assert body["registry_version"] == "BT_INDICATOR_REGISTRY_V2"
    assert body["condition_contract"] == "BT_CONDITION_LAYER_V2"
    assert body["exit_contract"] == "BT_DAILY_EXIT_V2"
    assert body["exit_execution_mode"] == "CLOSE_CONFIRM_NEXT_SESSION_OPEN"
    assert body["time_rules"]["intraday_touch"] == "NOT_SUPPORTED"
    resolved = body["resolved_config"]["request"]
    assert {item["indicator_id"] for item in resolved["indicators"]} == {"RSI", "EMA"}
    assert resolved["exit_rules"]["stop_loss_pct"] == 0.05
    assert resolved["entry_condition"]["op"] == "and"
    assert [f["side"] for f in body["fills"]][:1] == ["BUY"]


def test_v2_endpoint_rejects_file_entry_and_bad_requests(client: TestClient):
    payload = _payload()
    payload["dataset_path"] = "synthetic.csv"
    payload["dataset_root"] = "."
    assert client.post("/api/backtest/behavior/v2", json=payload).status_code == 400

    payload = _payload()
    payload["mode"] = "BT_BEHAVIOR_TICK_V1"
    response = client.post("/api/backtest/behavior/v2", json=payload)
    assert response.status_code == 400
    assert "UNSUPPORTED_MODE" in response.json()["detail"]["code"]

    payload = _payload()
    payload["entry_condition"] = {"op": "eval", "args": []}
    response = client.post("/api/backtest/behavior/v2", json=payload)
    assert response.status_code == 400
    assert "UNKNOWN_OPERATOR" in response.json()["detail"]["code"]

    payload = _payload()
    payload["indicators"] = [{"indicator_id": "NOT_AN_INDICATOR"}]
    response = client.post("/api/backtest/behavior/v2", json=payload)
    assert response.status_code == 400


# --------------------------------------------------------------------------
# 2. API 与 CLI 语义一致
# --------------------------------------------------------------------------
def test_cli_matches_http_semantics(tmp_path: Path, client: TestClient):
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_payload()), encoding="utf-8")
    result_path = tmp_path / "result.json"

    http_body = client.post("/api/backtest/behavior/v2", json=_payload()).json()
    completed = _run_cli(["--request", str(request_path), "--request-root", str(tmp_path),
                          "--out", str(result_path), "--out-root", str(tmp_path)])
    assert completed.returncode == 0, completed.stderr
    cli_body = json.loads(result_path.read_text(encoding="utf-8"))

    for key in ("mode", "engine_version", "registry_version", "condition_contract",
                "exit_contract", "cash", "final_equity", "official_valuation"):
        assert cli_body[key] == http_body[key], key
    assert [f["fill_time"] for f in cli_body["fills"]] == [f["fill_time"] for f in http_body["fills"]]
    assert [f["quantity"] for f in cli_body["fills"]] == [f["quantity"] for f in http_body["fills"]]
    assert [f["price"] for f in cli_body["fills"]] == [f["price"] for f in http_body["fills"]]
    assert [f["fee"] for f in cli_body["fills"]] == [f["fee"] for f in http_body["fills"]]
    assert cli_body["orders"] == http_body["orders"]
    assert cli_body["equity_curve"] == http_body["equity_curve"]
    assert cli_body["resolved_config"]["request"]["indicators"] == \
        http_body["resolved_config"]["request"]["indicators"]


def test_cli_supports_file_entry(tmp_path: Path):
    """文件入口（CSV）由 CLI 提供，文件解析是真实的。"""
    days = _days()
    frame = pd.DataFrame(_bars())
    frame.insert(0, "symbol", "600000.SH")
    csv_path = tmp_path / "synthetic.csv"
    frame.to_csv(csv_path, index=False, encoding="utf-8")

    payload = _payload()
    payload.pop("bars")
    payload["dataset_path"] = csv_path.name
    payload["dataset_root"] = str(tmp_path)
    request_path = tmp_path / "request_file.json"
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    result_path = tmp_path / "result_file.json"

    completed = _run_cli(["--request", str(request_path), "--request-root", str(tmp_path),
                          "--out", str(result_path), "--out-root", str(tmp_path)])
    assert completed.returncode == 0, completed.stderr
    body = json.loads(result_path.read_text(encoding="utf-8"))
    assert [f["side"] for f in body["fills"]][:1] == ["BUY"]
    assert body["final_equity"] != 100_000.0


def test_cli_rejects_path_outside_declared_root(tmp_path: Path):
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_payload()), encoding="utf-8")
    outside = tmp_path.parent / "outside_v2_result.json"
    completed = _run_cli(["--request", str(request_path), "--request-root", str(tmp_path),
                          "--out", str(outside), "--out-root", str(tmp_path)])
    assert completed.returncode != 0
    assert "PATH_OUTSIDE_IO_ROOT" in (completed.stderr + completed.stdout)
    assert not outside.exists()


def test_cli_lists_registry_indicators():
    completed = _run_cli(["--list-indicators"])
    assert completed.returncode == 0, completed.stderr
    output = completed.stdout
    assert "BT_INDICATOR_REGISTRY_V2" in output
    for indicator_id in ("RSI", "BOLLINGER", "ATR", "DMI", "SAR", "OBV", "MFI", "KELTNER"):
        assert indicator_id in output, indicator_id
    assert "BREAKOUT_WITH_VOLUME_CONFIRMATION" in output
