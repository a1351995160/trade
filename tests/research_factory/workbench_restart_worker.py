"""仅凭显式配置在新进程重建Web服务，再经公共API继续合成事件。"""
import json
import os
import sys

from fastapi.testclient import TestClient
from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.engineering_workspace import load_engineering_workspace
from chanlun_trader.webapp import create_app

if os.environ.get("CHANLUN_TEST_ISOLATION") != "1":
    raise RuntimeError("IMPORT_TIME_ISOLATION_REQUIRED")
service = load_engineering_workspace(sys.argv[1])
app = create_app(service.root, ExecutionPolicy("GOVERNED", "SYNTHETIC"), engineering_workbench=service)
with TestClient(app, base_url="http://127.0.0.1") as client:
    response = client.get("/api/research-engineering/workbench")
    assert response.status_code == 200, response.text
    view = response.json()
    candidate = view["sources"][0]["candidate_id"]
    assert view["paper"][candidate]["completed_events"] == 9
    result = client.post("/api/research-engineering/workbench/advance", json={"confirmed": True,
        "context_hash": view["context_hash"], "candidate_id": candidate,
        "event_count": view["paper"][candidate]["total_events"]})
    assert result.status_code == 200, result.text
    print(json.dumps(result.json()), flush=True)
