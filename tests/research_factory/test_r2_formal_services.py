"""隔离子进程内的真实 R2 服务组合，不安装 pytest 的替代 runner。"""
import json
import os
from pathlib import Path
import subprocess
import sys


def run_worker(tmp_path, name, *args):
    source = Path(__file__).resolve().parents[2]
    output = Path(os.environ["CHANLUN_PROCESS_EVIDENCE_DIR"]) / "r2-formal" / tmp_path.name[:64]
    output.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(source / "tests/isolation"), str(source / "src")]))
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).with_name(name + ".py")), *map(str, args)],
            cwd=tmp_path, env=environment, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        (output / (name + "-stdout.bin")).write_bytes(exc.stdout or b"")
        (output / (name + "-stderr.bin")).write_bytes(exc.stderr or b"")
        raise
    (output / (name + "-stdout.bin")).write_bytes(result.stdout)
    (output / (name + "-stderr.bin")).write_bytes(result.stderr)
    return result


def test_formal_creation_actual_engines_adjudication_registry_and_restart(tmp_path):
    root = tmp_path / "fresh-synthetic"
    result = run_worker(tmp_path, "r2_service_worker", root)
    assert result.returncode == 0, result.stdout.decode("utf-8") + result.stderr.decode("utf-8")
    summary = json.loads((root / "r2-safe-summary.json").read_bytes())
    assert summary["engineering_chain_complete"] is True
    assert summary["counts"]["engine_run"] == 2
    assert summary["counts"]["performance_access"] == 1
    assert summary["counts"]["external_ai_attempts"] == 0
    assert summary["classification"] == "BLOCKED"
    assert summary["qualified_real_strategies"] == summary["real_observation_days"] == 0
    restarted = run_worker(tmp_path, "r2_recovery_worker", root)
    assert restarted.returncode == 0, restarted.stderr.decode("utf-8")
    assert json.loads(restarted.stdout)["canonical_files_unchanged"] is True


def test_performance_access_process_exit_requires_actual_confirmed_same_trial_recovery(tmp_path):
    root = tmp_path / "fresh-synthetic"
    crashed = run_worker(tmp_path, "r2_service_worker", root, "crash")
    assert crashed.returncode == 73, crashed.stdout.decode("utf-8") + crashed.stderr.decode("utf-8")
    recovered = run_worker(tmp_path, "r2_recovery_worker", root, "interrupted")
    assert recovered.returncode == 0, recovered.stderr.decode("utf-8")
    result = json.loads(recovered.stdout)
    assert result["recovery_completed"] is True and result["same_trial"] is True
    assert result["calls"] == {"engine": 2, "performance": 0}
