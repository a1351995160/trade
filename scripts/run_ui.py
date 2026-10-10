"""启动 Web UI。用法：python scripts/run_ui.py [端口]"""
from __future__ import annotations

import sys
import argparse
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn

from chanlun_trader.webapp import create_app


def lifecycle_service(root, filename):
    from scripts.lifecycle_deployment_v2 import build_lifecycle_service
    if root is None:
        raise ValueError('LIFECYCLE_EXPLICIT_RESEARCH_ROOT_REQUIRED')
    root, filename = Path(root).absolute(), Path(filename).absolute()
    if (root.resolve() != root or filename.resolve() != filename or '..' in filename.parts
            or not filename.is_relative_to(root) or filename.suffix.lower() != '.json'
            or filename.stat().st_size > 20 * 1024 * 1024):
        raise ValueError('LIFECYCLE_CONFIG_OUTSIDE_WORKSPACE')
    config = json.loads(filename.read_text(encoding='utf-8-sig'))
    return build_lifecycle_service(root, config)


def main() -> None:
    parser = argparse.ArgumentParser(description="以只读模式启动 Web 控制台")
    parser.add_argument("port", nargs="?", type=int, default=8000)
    parser.add_argument("--research-root", type=Path, help="显式绑定只读研究目录")
    parser.add_argument('--lifecycle-config', type=Path, help='工作区内的显式生命周期部署配置 JSON')
    parser.add_argument('--submission-config', type=Path, help='维护者登记的数据目录和授权引用JSON')
    parser.add_argument('--allow-trusted-research', action='store_true', help='显式允许已登记公共研究操作；原授权仍须逐项核验')
    args = parser.parse_args()
    service = lifecycle_service(args.research_root, args.lifecycle_config) if args.lifecycle_config else None
    submission = None
    if args.submission_config:
        if args.research_root is None:
            parser.error('--submission-config requires --research-root')
        from scripts.run_strategy_lifecycle_v1 import load_config
        from scripts.lifecycle_deployment_v2 import build_submission_service
        submission = build_submission_service(args.research_root, load_config(args.submission_config, args.research_root / 'lifecycle_jobs'))
    from chanlun_trader.execution_policy import ExecutionPolicy
    policy = ExecutionPolicy(mode='GOVERNED' if args.allow_trusted_research else 'READ_ONLY',
                             allow_trusted_research=args.allow_trusted_research)
    uvicorn.run(create_app(args.research_root, policy, lifecycle_service=service, submission_service=submission), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
