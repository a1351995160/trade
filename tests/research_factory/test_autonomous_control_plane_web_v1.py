from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from chanlun_trader import webapp
from chanlun_trader.research_console import ResearchConsoleReadService

from test_autonomous_control_plane_v1 import OBJECTIVE_ID, _pending_design_root


def test_control_plane_console_read_and_local_tick_routes_are_outcome_blind(tmp_path: Path, monkeypatch) -> None:
    application = webapp.create_app(tmp_path, webapp.ExecutionPolicy("GOVERNED", "SYNTHETIC"))
    root = _pending_design_root(tmp_path)
    monkeypatch.setattr(application.state.services, "research_console_service", ResearchConsoleReadService(root))

    with TestClient(application) as client:
        read_response = client.get(f"/api/research-console/{OBJECTIVE_ID}/autonomous-control-plane")
        tick_response = client.post(f"/api/research-console/{OBJECTIVE_ID}/autonomous-control-plane/tick", json={"dry_run": True})

    assert read_response.status_code == 200
    assert tick_response.status_code == 200
    read_payload = read_response.json()
    tick_payload = tick_response.json()
    assert read_payload["outcome_blind"] is True
    assert read_payload["decision"]["selected_action"]["action_type"] == "WAIT_FOR_AI_DESIGN_CONFIRMATION"
    assert tick_payload["dry_run"] is True
    assert tick_payload["execution"] is None
    assert tick_payload["stop_reason"] == "HUMAN_CONFIRMATION_REQUIRED"
    assert not (root / "reports/research_candidates").exists()
