from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory.orchestrator_launcher import OrchestratorLaunchError, OrchestratorProcessLauncherV1


OBJECTIVE_ID = "GOVERNED_OBJECTIVE_V1"
EXECUTION_ID = "GOVERNANCE_EXECUTION_V1"


def _write_json(root: Path, relative: str, payload: dict) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _fixture(tmp_path: Path) -> None:
    _write_json(tmp_path, f"data/research/research_factory/objectives/{OBJECTIVE_ID}.json", {"objective_id": OBJECTIVE_ID, "lifecycle_state": "READY"})
    _write_json(tmp_path, f"reports/research_orchestrator_v2/{OBJECTIVE_ID}/activation_eligibility.json", {"objective_id": OBJECTIVE_ID, "eligibility": "READY", "activation_authorized": True, "activation_mode": "CREATE_AND_ACTIVATE", "target_state": "NEED_AI_RESEARCH_DESIGN"})


def test_launcher_starts_fixed_orchestrator_once(tmp_path: Path) -> None:
    _fixture(tmp_path)
    calls: list[tuple[list[str], dict]] = []

    def fake_popen(command, **kwargs):
        calls.append((list(command), kwargs))
        return SimpleNamespace(pid=4321)

    launcher = OrchestratorProcessLauncherV1(tmp_path, popen=fake_popen)
    launcher._pid_alive = lambda pid: pid == 4321
    first = launcher.start(OBJECTIVE_ID, execution_id=EXECUTION_ID)
    second = launcher.start(OBJECTIVE_ID, execution_id=EXECUTION_ID)

    assert first["status"] == "STARTED"
    assert second["idempotent"] is True
    assert len(calls) == 1
    assert calls[0][0][1:6] == ["-m", "chanlun_trader.research_orchestrator", "start", "--root", str(tmp_path.resolve())]
    assert calls[0][0][6:8] == ["--objective-id", OBJECTIVE_ID]
    assert calls[0][1]["shell"] is False
    assert json.loads((tmp_path / f"reports/research_orchestrator_v2/{OBJECTIVE_ID}/process_launch.json").read_text(encoding="utf-8"))["process_identity"]["pid"] == 4321


def test_launcher_rejects_missing_activation_eligibility(tmp_path: Path) -> None:
    _write_json(tmp_path, f"data/research/research_factory/objectives/{OBJECTIVE_ID}.json", {"objective_id": OBJECTIVE_ID, "lifecycle_state": "READY"})

    with pytest.raises(OrchestratorLaunchError) as error:
        OrchestratorProcessLauncherV1(tmp_path, popen=lambda *_args, **_kwargs: SimpleNamespace(pid=1)).start(OBJECTIVE_ID, execution_id=EXECUTION_ID)

    assert error.value.code == "ACTIVATION_ELIGIBILITY_MISSING"
