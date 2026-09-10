"""现场生成合成输入后显式启动本机工作台；不是完整R2或真实运行入口。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from chanlun_trader.execution_policy import ExecutionPolicy
from chanlun_trader.research_factory.engineering_workbench import EngineeringWorkbenchV1
from chanlun_trader.webapp import create_app
from test_portfolio_plan import setup


def build(root: Path, *, governed: bool = False):
    if os.environ.get("CHANLUN_TEST_ISOLATION") != "1":
        raise ValueError("IMPORT_TIME_ISOLATION_REQUIRED")
    if not root.is_absolute() or root.exists() or root.resolve() != root:
        raise ValueError("FRESH_ABSOLUTE_SYNTHETIC_ROOT_REQUIRED")
    root.mkdir(parents=True)
    sources, policy, ledger = setup(root / "inputs", count=2)
    for index, source in enumerate(sources.values()):
        source["root"] = root / "inputs" / str(index)
    service = EngineeringWorkbenchV1(root / "inputs", sources, ledger, policy, root / "output")
    execution = ExecutionPolicy("GOVERNED" if governed else "READ_ONLY", "SYNTHETIC")
    app = create_app(service.root, execution, engineering_workbench=service)
    print(json.dumps({"synthetic_root": str(root), "mode": execution.mode, "qualified_strategies": 0,
        "full_r2_certification": False, "real_execution_authorized": False}), flush=True)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--governed", action="store_true", help="明确允许合成工程操作，仍需页面逐次确认")
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(build(args.root, governed=args.governed), host="127.0.0.1", port=args.port)
