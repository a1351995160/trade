"""控制者真实进程退出/重启驱动；不改写批准、事件或原账本。"""
import json
import os
from pathlib import Path
import sys

from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.synthetic_batch import SyntheticBatchServiceV1

root, identifier, action = sys.argv[1:]
service = SyntheticBatchServiceV1(root, ExecutionPolicy("GOVERNED", "SYNTHETIC"))
if action == "claim_exit":
    active = service._claim(identifier)
    print(json.dumps(active), flush=True)
    os._exit(73)
elif action == "recover":
    result = service.recover(identifier)
    print(json.dumps(result), flush=True)
    Path(root, "r3-controller-recovery-result.json").write_text(json.dumps(result), encoding="utf-8")
elif action == "revoke_head_exit":
    def interrupt_head(frame, event, arg):
        if (event == "call" and frame.f_code.co_name == "_atomic_write"
                and frame.f_globals.get("__name__") == "chanlun_trader.research_factory.durability"
                and Path(frame.f_locals["path"]).name == "head.json"):
            os._exit(73)

    sys.setprofile(interrupt_head)
    service.control(identifier, "revoke", {"confirmed": True, "test_confirmation": True})
    raise AssertionError("ACTUAL_HEAD_WRITE_NOT_INTERRUPTED")
else:
    raise ValueError("UNKNOWN_TEST_DRIVER_ACTION")
