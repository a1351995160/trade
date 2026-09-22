"""公共入口验收：API 与 CLI 对同一合成请求必须语义一致。

- 只读合同端点返回实际 engine_version / indicator_contract / exit_contract；
- HTTP 入口与 CLI 入口调用同一服务函数，语义事件与账户结果必须一致；
- 经典 Web 回测入口明确标识为 legacy / 未认证，且不被静默换成新引擎；
- 不支持组合明确报错，不静默回落。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chanlun_trader.engine.behavior_service_v1 import BEHAVIOR_MODE
from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.webapp import create_app

from tests.behavior._fixtures import (
    CAL,
    ENTRY_INDEX,
    FEE_CONTRACT,
    SYMBOL,
    entry_bars,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _payload() -> dict:
    return {
        "mode": BEHAVIOR_MODE,
        "calendar": list(CAL),
        "symbols": [SYMBOL],
        "bars": {SYMBOL: entry_bars()},
        "entry_conditions": ["ABOVE_ZERO_GOLDEN_CROSS"],
        "initial_cash": 100_000.0,
        "max_positions": 1,
        "max_position_weight": 1.0,
        "exit_rules": {"stop_loss_pct": 0.03, "fixed_holding_sessions": 3},
        **FEE_CONTRACT,
    }


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """隔离 app：合成 root + 显式开启只读计算端点；不触发恢复，不写研究状态。"""
    root = tmp_path / "synthetic_root"
    root.mkdir()
    app = create_app(root, ExecutionPolicy(
        mode="READ_ONLY", workspace_kind="SYNTHETIC", allow_readonly_compute=True))
    return TestClient(app)


def test_default_read_only_policy_still_denies_behavior_endpoint(tmp_path: Path):
    """默认策略下本端点与其它 POST 一样被拒绝：写入边界未被放宽。"""
    root = tmp_path / "synthetic_root"
    root.mkdir()
    app = create_app(root, ExecutionPolicy(mode="READ_ONLY", workspace_kind="SYNTHETIC"))
    client = TestClient(app)
    response = client.post("/api/backtest/behavior", json=_payload())
    assert response.status_code == 403
    assert response.json()["code"] == "EXECUTION_POLICY_READ_ONLY"


def test_contracts_endpoint_exposes_actual_versions(client: TestClient):
    response = client.get("/api/backtest/behavior/contracts")
    assert response.status_code == 200
    body = response.json()
    assert body["supported_modes"] == [BEHAVIOR_MODE]
    assert body["indicator_contract"] == "BT_INDICATORS_V1"
    assert "ABOVE_ZERO_GOLDEN_CROSS" in body["conditions"]["MACD"]
    assert "BOTH_LINES_ABOVE_ZERO" in body["conditions"]["MACD"]
    assert "DIF_ABOVE_ZERO" in body["conditions"]["MACD"]
    assert "GOLDEN_CROSS" in body["conditions"]["MACD"]
    assert "DEATH_CROSS" in body["conditions"]["MACD"]
    assert "INTRADAY_TOUCH_STOP" in body["reserved_exit_types"]
    # 经典入口必须被明确标识为 legacy，而不是悄悄指向新引擎。
    assert body["legacy_endpoint"]["path"] == "/api/backtest"
    assert "LEGACY" in body["legacy_endpoint"]["certification"]


def test_legacy_web_backtest_endpoint_is_not_silently_replaced(client: TestClient):
    """经典 /api/backtest 仍按既有策略被拒绝，不得静默换策略。"""
    response = client.post("/api/backtest", json={"limit": 1})
    assert response.status_code == 403
    assert response.json()["code"] == "LEGACY_EXECUTION_DISABLED"


def test_http_behavior_endpoint_runs_and_reports_versions(client: TestClient):
    response = client.post("/api/backtest/behavior", json=_payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == BEHAVIOR_MODE
    assert body["engine_version"] == "2.0.0"
    assert body["indicator_contract"] == "BT_INDICATORS_V1"
    assert body["condition_contract"] == "BT_MACD_CONDITIONS_V1"
    assert body["exit_contract"] == "BT_DAILY_EXIT_V1"
    assert body["exit_execution_mode"] == "CLOSE_CONFIRM_NEXT_SESSION_OPEN"
    assert body["time_rules"]["intraday_touch"] == "NOT_SUPPORTED"
    assert body["resolved_config"]["request"]["exit_rules"]["stop_loss_pct"] == 0.03
    assert body["resolved_config"]["request"]["macd"] == {"fast": 12, "slow": 26, "signal": 9}
    assert body["resolved_config"]["request"]["kdj"] == {"n": 9, "k_period": 3, "d_period": 3}
    assert [f["side"] for f in body["fills"]] == ["BUY", "SELL"]


def test_http_rejects_file_entry_and_unsupported_combination(client: TestClient):
    payload = _payload()
    payload["dataset_path"] = "synthetic.csv"
    response = client.post("/api/backtest/behavior", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "FILE_ENTRY_NOT_AVAILABLE_OVER_HTTP"

    payload = _payload()
    payload["mode"] = "BT_BEHAVIOR_TICK_V1"
    response = client.post("/api/backtest/behavior", json=payload)
    assert response.status_code == 400
    assert "UNSUPPORTED_MODE" in response.json()["detail"]["code"]

    payload = _payload()
    payload["exit_rules"] = {"trailing_pct": 0.05}
    response = client.post("/api/backtest/behavior", json=payload)
    assert response.status_code == 400
    assert "TRAILING_PARAMETERS_INCOMPLETE" in response.json()["detail"]["code"]


def test_cli_matches_http_semantics(tmp_path: Path, client: TestClient):
    """同一合成请求：API 与 CLI 的语义事件与账户结果必须一致。"""
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_payload()), encoding="utf-8")
    result_path = tmp_path / "result.json"

    http_body = client.post("/api/backtest/behavior", json=_payload()).json()

    completed = _run_cli(
        ["--request", str(request_path), "--request-root", str(tmp_path),
         "--out", str(result_path), "--out-root", str(tmp_path)])
    assert completed.returncode == 0, completed.stderr
    cli_body = json.loads(result_path.read_text(encoding="utf-8"))

    assert cli_body["engine_version"] == http_body["engine_version"]
    assert cli_body["indicator_contract"] == http_body["indicator_contract"]
    assert cli_body["exit_contract"] == http_body["exit_contract"]
    # 买卖时间、数量、费用、净值必须完全一致（不宽容比较）。
    assert [f["fill_time"] for f in cli_body["fills"]] == [f["fill_time"] for f in http_body["fills"]]
    assert [f["quantity"] for f in cli_body["fills"]] == [f["quantity"] for f in http_body["fills"]]
    assert [f["price"] for f in cli_body["fills"]] == [f["price"] for f in http_body["fills"]]
    assert [f["fee"] for f in cli_body["fills"]] == [f["fee"] for f in http_body["fills"]]
    assert cli_body["cash"] == http_body["cash"]
    assert cli_body["final_equity"] == http_body["final_equity"]
    assert cli_body["equity_curve"] == http_body["equity_curve"]
    assert cli_body["orders"] == http_body["orders"]
    # 正式估值元数据必须一致。
    assert cli_body["official_valuation"] == http_body["official_valuation"]


def test_cli_supports_file_entry(tmp_path: Path):
    """文件入口（CSV）由 CLI 提供，文件解析是真实的。"""
    from tests.behavior._fixtures import to_dataset_csv

    csv_path = tmp_path / "synthetic.csv"
    to_dataset_csv(csv_path, {SYMBOL: entry_bars()})
    payload = _payload()
    payload.pop("bars")
    payload["dataset_path"] = csv_path.name
    payload["dataset_root"] = str(tmp_path)
    request_path = tmp_path / "request_file.json"
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    result_path = tmp_path / "result_file.json"

    completed = _run_cli(
        ["--request", str(request_path), "--request-root", str(tmp_path),
         "--out", str(result_path), "--out-root", str(tmp_path)])
    assert completed.returncode == 0, completed.stderr
    body = json.loads(result_path.read_text(encoding="utf-8"))
    assert [f["side"] for f in body["fills"]] == ["BUY", "SELL"]
    assert body["final_equity"] != 100_000.0


def test_cli_rejects_path_outside_declared_root(tmp_path: Path):
    """外部路径越出显式声明的根目录时必须拒绝，不得任意读写。"""
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(_payload()), encoding="utf-8")
    outside = tmp_path.parent / "outside_result.json"

    completed = _run_cli(
        ["--request", str(request_path), "--request-root", str(tmp_path),
         "--out", str(outside), "--out-root", str(tmp_path)])
    assert completed.returncode != 0
    assert "PATH_OUTSIDE_IO_ROOT" in (completed.stderr + completed.stdout)
    assert not outside.exists()


def test_service_rejects_dataset_path_outside_root(tmp_path: Path):
    """文件入口的 dataset_path 同样受根目录约束。"""
    from chanlun_trader.engine.behavior_service_v1 import (
        BehaviorPathError,
        BehaviorRequestV1,
        run_behavior_backtest_v1,
    )

    payload = _payload()
    payload.pop("bars")
    payload["dataset_path"] = "../outside.csv"
    payload["dataset_root"] = str(tmp_path)
    with pytest.raises(BehaviorPathError):
        run_behavior_backtest_v1(BehaviorRequestV1.from_mapping(payload))


def _run_cli(extra_args):
    return subprocess.run(
        [sys.executable, "scripts/run_behavior_backtest_v1.py", *extra_args],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def test_http_behavior_endpoint_does_not_write_research_root(tmp_path: Path):
    """只读计算端点不得在合成 root 内产生任何文件。"""
    root = tmp_path / "synthetic_root"
    root.mkdir()
    app = create_app(root, ExecutionPolicy(
        mode="READ_ONLY", workspace_kind="SYNTHETIC", allow_readonly_compute=True))
    client = TestClient(app)
    before = {p.name for p in root.rglob("*")}
    assert client.post("/api/backtest/behavior", json=_payload()).status_code == 200
    after = {p.name for p in root.rglob("*")}
    assert before == after, "只读计算端点写入了研究目录"
