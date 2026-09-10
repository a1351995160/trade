"""隔离工作区的来源声明、人工测试确认及只读启动预览入口。"""
import argparse
import json
from pathlib import Path

from ..execution_policy import ExecutionPolicy
from .synthetic_novelty import SyntheticNoveltyBindingServiceV1
from .synthetic_novelty_start import SyntheticNoveltyTrialStartServiceV1


def main(argv=None):
    parser = argparse.ArgumentParser(description="合成新颖性比较集服务；不启动研究或授予预算")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--mode", choices=["READ_ONLY", "GOVERNED"], default="READ_ONLY")
    commands = parser.add_subparsers(dest="command", required=True)
    declare = commands.add_parser("declare-sources", help="声明部署来源范围，只能扩充")
    declare.add_argument("--source", action="append", required=True)
    preview = commands.add_parser("preview", help="从全部已声明 registry 生成确认预览")
    preview.add_argument("--candidate-id", required=True)
    preview.add_argument("--contract-hash", required=True)
    confirm = commands.add_parser("confirm", help="人工操作方显式确认合成预览")
    confirm.add_argument("--preview-id", required=True)
    confirm.add_argument("--confirm", action="store_true", required=True)
    history = commands.add_parser("history", help="查询已完成测试确认")
    history.add_argument("--preview-id", required=True)
    for name in ("start-readiness", "start-preview"):
        command = commands.add_parser(name, help="通过原启动服务查询其他门禁，不启动")
        command.add_argument("--preview-id", required=True)
        command.add_argument("--objective-id", required=True)
    args = parser.parse_args(argv)
    policy = ExecutionPolicy(args.mode, "SYNTHETIC")
    service = SyntheticNoveltyBindingServiceV1(args.root)
    if args.command == "declare-sources":
        result = service.declare_sources(policy, args.source)
    elif args.command == "preview":
        result = service.preview(policy, args.candidate_id, args.contract_hash)
    elif args.command == "confirm":
        result = service.confirm(policy, {"preview_id": args.preview_id, "confirmed": args.confirm})
    elif args.command == "history":
        preview, receipt = service.historical_confirmation(args.preview_id)
        result = {"preview": preview, "receipt": receipt}
    else:
        start = SyntheticNoveltyTrialStartServiceV1(args.root, policy, args.preview_id)
        operation = start.readiness if args.command == "start-readiness" else start.preview
        result = operation(args.objective_id)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
