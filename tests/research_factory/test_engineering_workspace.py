"""工作台配置与真实文件身份核对、进程恢复；不携带历史内存对象。"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from test_engineering_workbench import workbench, GOVERNED
from chanlun_trader.research_factory.engineering_workspace import save_engineering_workspace, load_engineering_workspace
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.source_dependencies import SOURCE_ROOT


def test_load_configuration_reprepares_actual_inputs_without_writing(tmp_path):
    service = workbench(tmp_path)
    config = save_engineering_workspace(tmp_path / "workbench.json", service)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    restored = load_engineering_workspace(config)
    assert restored.inspect() == service.inspect()
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert not service.output_root.exists()


def test_changed_input_and_tampered_configuration_are_rejected(tmp_path):
    service = workbench(tmp_path)
    config = save_engineering_workspace(tmp_path / "workbench.json", service)
    path = next(iter(service.sources.values()))["root"] / "data/research/daily_all.parquet"
    frame = pd.read_parquet(path)
    frame.loc[0, "volume"] += 100
    frame.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="WORKSPACE_INPUT_CHANGED"):
        load_engineering_workspace(config)
    value = json.loads(config.read_text(encoding="utf-8"))
    value["planning_initial_cash"] += 1
    config.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="WORKSPACE_CONFIG_IDENTITY_CONFLICT"):
        load_engineering_workspace(config)


def test_relative_escape_rejected_before_contract_read(tmp_path):
    service = workbench(tmp_path)
    config = save_engineering_workspace(tmp_path / "workbench.json", service)
    value = json.loads(config.read_text(encoding="utf-8"))
    value["sources"][0]["contract_ref"] = "../not-authorized.json"
    value["manifest_identity"] = stable_hash({key: item for key, item in value.items() if key != "manifest_identity"})
    config.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="WORKSPACE_RELATIVE_PATH_REQUIRED"):
        load_engineering_workspace(config)


def test_nonfinite_planning_cash_rejected_even_with_recomputed_content_hash(tmp_path):
    service = workbench(tmp_path)
    config = save_engineering_workspace(tmp_path / "workbench.json", service)
    value = json.loads(config.read_text(encoding="utf-8"))
    value["planning_initial_cash"] = float("nan")
    value["manifest_identity"] = stable_hash({key: item for key, item in value.items() if key != "manifest_identity"})
    config.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="WORKSPACE_PLANNING_CASH_INVALID"):
        load_engineering_workspace(config)


def test_new_process_rebuilds_public_application_and_resumes_paper(tmp_path):
    service = workbench(tmp_path)
    config = save_engineering_workspace(tmp_path / "workbench.json", service)
    candidate = next(iter(service.sources))
    service.advance(GOVERNED, {"confirmed": True, "context_hash": service.inspect()["context_hash"],
        "candidate_id": candidate, "event_count": 9})
    before = {path: path.read_bytes() for path in service.root.rglob("*") if path.is_file()}
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(SOURCE_ROOT / "tests/isolation"), str(SOURCE_ROOT / "src")]))
    evidence = Path(os.environ.get("CHANLUN_PROCESS_EVIDENCE_DIR", str(tmp_path / "evidence"))) / "workbench-restart"
    evidence.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("workbench_restart_worker.py")), str(config)],
            cwd=tmp_path, env=environment, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        (evidence / "stdout.bin").write_bytes(exc.stdout or b"")
        (evidence / "stderr.bin").write_bytes(exc.stderr or b"")
        raise
    (evidence / "stdout.bin").write_bytes(result.stdout)
    (evidence / "stderr.bin").write_bytes(result.stderr)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    completed = json.loads(result.stdout)
    assert completed["status"] == "REPLAY_COMPLETE"
    assert completed["completed_events"] == completed["total_events"]
    assert completed["real_observation_days"] == 0
    assert before == {path: path.read_bytes() for path in service.root.rglob("*") if path.is_file()}
    assert service.inspect()["paper"][candidate]["state"] == completed["state"]
