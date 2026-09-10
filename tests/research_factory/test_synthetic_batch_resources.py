"""真实 OS 内存和时间限制；不以打印的配置值代替执行证据。"""
import os
from pathlib import Path
import sys
import time

import pytest

from chanlun_trader.synthetic_batch_resources import run_bounded_worker


@pytest.mark.parametrize("action", ["normal", "memory", "timeout"])
def test_actual_worker_resource_boundaries(tmp_path, action):
    source = Path(__file__).resolve().parents[2]
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(source / "tests/isolation"), str(source / "src")]))
    started = []
    begin = time.monotonic()
    result = run_bounded_worker([sys.executable, str(Path(__file__).with_name("batch_resource_worker.py")), action],
        root=tmp_path, memory_mib=96, wall_seconds=1 if action == "timeout" else 10,
        on_started=started.append, environment=environment)
    output = Path(os.environ["CHANLUN_PROCESS_EVIDENCE_DIR"]) / "batch-resources"
    output.mkdir(parents=True, exist_ok=True)
    (output / (action + "-stdout.bin")).write_bytes(result["stdout"])
    (output / (action + "-stderr.bin")).write_bytes(result["stderr"])
    assert len(started) == 1
    if action == "normal":
        assert result["returncode"] == 0, result["stderr"].decode()
        assert b"BOUNDED_WORKER_COMPLETED" in result["stdout"]
    else:
        assert result["returncode"] != 0
        if action == "memory":
            assert b"MemoryError" in result["stderr"] or result["returncode"] in {3221225495, -1073741801}
        else:
            assert time.monotonic() - begin < 5
