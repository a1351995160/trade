"""合成拒绝证据必须在失败断言前保存，隔离退出保持不变。"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_phase3c_restart_v1 import finish


def test_denied_process_keeps_raw_evidence_and_nonzero_exit(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2]
    evidence = tmp_path / "evidence"
    workspace = tmp_path / "synthetic"
    workspace.mkdir()
    monkeypatch.setenv("CHANLUN_PROCESS_EVIDENCE_DIR", str(evidence))
    environment = dict(os.environ, CHANLUN_TEST_ISOLATION="1",
                       CHANLUN_PROTECTED_ROOT=os.environ.get("CHANLUN_PROTECTED_ROOT", str(tmp_path / "protected")),
                       PYTHONPATH=str(source / "tests/isolation"))
    # 审计事件发生在系统进程创建前；故意不存在的程序不会被执行。
    code = "import subprocess; print('synthetic-output', flush=True); subprocess.Popen(['r1-denied-synthetic.exe', 'SECRET_SENTINEL'])"
    process = subprocess.Popen([sys.executable, "-c", code], cwd=workspace, env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with pytest.raises(AssertionError):
        finish(process)
    assert process.returncode == 79
    saved = list(evidence.iterdir())
    assert len(saved) == 1
    assert (saved[0] / "stdout.bin").read_bytes().strip() == b"synthetic-output"
    stderr = (saved[0] / "stderr.bin").read_bytes().decode("utf-8")
    row = next(line for line in stderr.splitlines() if line.startswith("RESEARCH_PROCESS_DENIED="))
    denied = json.loads(row.split("=", 1)[1])
    assert denied["executable"] == "r1-denied-synthetic.exe"
    assert "SECRET_SENTINEL" not in row
    assert denied["argv"][-1] in {"<REDACTED>", "<REDACTED_COMMAND_LINE>"}
    assert any(frame["function"] == "_execute_child" and frame["line"] > 0 for frame in denied["stack"])
    assert '"process_calls": 1' in stderr
    assert "RESEARCH_PROCESS_DISABLED" in stderr
    assert json.loads((saved[0] / "result.json").read_text())["stream_capture"] == "RAW_BYTES"
    assert list(workspace.iterdir()) == []
