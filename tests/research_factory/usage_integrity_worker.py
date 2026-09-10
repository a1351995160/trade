"""新进程消费实际服务生成的资格；不替换领域服务或权限。"""
import json
import os
from pathlib import Path
import sys

from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.engineering_workspace import load_engineering_workspace
from chanlun_trader.research_factory.synthetic_usage import SyntheticUsageServiceV1


service = load_engineering_workspace(Path(sys.argv[1]))
candidate = next(iter(service.sources))
policy = ExecutionPolicy("GOVERNED", "SYNTHETIC")
if len(sys.argv) > 2:
    usage = SyntheticUsageServiceV1(service)
    request_id = usage.inspect()["records"][0]["request_id"]
    # 故障注入只放在真实不可变事件写入之后、提交指针写入之前。
    usage._commit_head = lambda directory: os._exit(73)
    usage.perform(sys.argv[2], policy, {"confirmed": True,
        "context_hash": service.inspect()["context_hash"], "request_id": request_id})
    raise AssertionError("CRASH_POINT_NOT_REACHED")
results = {}
for name, operation in {
    "inspect": lambda: SyntheticUsageServiceV1(service).inspect(),
    "active": lambda: SyntheticUsageServiceV1(service).active(candidate, "PAPER_REPLAY"),
    "preview": lambda: service.preview(service.sources[candidate]["inputs"]["exec_calendar"][0]),
    "advance": lambda: service.advance(policy, {"confirmed": True,
        "context_hash": service.inspect()["context_hash"], "candidate_id": candidate, "event_count": 10}),
}.items():
    try:
        value = operation()
        results[name] = {"blocked": False, "value": value}
    except (ValueError, OSError) as exc:
        results[name] = {"blocked": True, "error": str(exc)}
print(json.dumps(results, default=str))
