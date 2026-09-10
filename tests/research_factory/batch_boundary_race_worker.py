"""在真实性能边界前停住 worker，供另一操作方撤销或变更源文件。"""
import json
import os
from pathlib import Path
import sys
import time

from chanlun_trader.synthetic_batch_resources import worker_resource_handshake

worker_resource_handshake()
root = Path(sys.argv[1])
counts = {"engine": 0, "performance": 0}
paused = False


def observe(frame, event, arg):
    global paused
    if event != "call":
        return
    module, name = frame.f_globals.get("__name__", ""), frame.f_code.co_name
    if module == "chanlun_trader.engine.engine" and name == "run":
        counts["engine"] += 1
    if module == "chanlun_trader.research_factory.trial_adapter" and name == "mark_performance_accessed":
        counts["performance"] += 1
    target = (("chanlun_trader.research_factory.caller_inputs", "prepare_inputs")
        if os.environ.get("R3_TEST_RACE_STAGE") == "SNAPSHOT"
        else ("chanlun_trader.research_factory.synthetic_batch_delegation", "batch_performance_boundary"))
    if (module, name) == target and not paused:
        paused = True
        (root / "r3-performance-boundary-ready").write_text("READY", encoding="utf-8")
        deadline = time.monotonic() + 30
        while not (root / "r3-performance-boundary-release").exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("TEST_OPERATION_DID_NOT_RELEASE_BOUNDARY")
            time.sleep(0.01)


sys.setprofile(observe)
try:
    from chanlun_trader.synthetic_batch_worker import execute
    execute()
finally:
    sys.setprofile(None)
    (root / "r3-boundary-counts.json").write_text(json.dumps(counts), encoding="utf-8")
