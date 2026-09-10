"""真实 worker 的进程退出注入；不替换领域调用或任何授权判定。"""
import os
from pathlib import Path
import sys

from chanlun_trader.synthetic_batch_resources import worker_resource_handshake

worker_resource_handshake()


def observe(frame, event, arg):
    if event != "call":
        return
    module, name = frame.f_globals.get("__name__", ""), frame.f_code.co_name
    if module == "chanlun_trader.engine.engine" and name == "run":
        Path(sys.argv[1], "r3-crash-after-performance.txt").write_text("ENGINE_BODY_NOT_ENTERED", encoding="utf-8")
        os._exit(73)


sys.setprofile(observe)
from chanlun_trader.synthetic_batch_worker import execute
execute()
