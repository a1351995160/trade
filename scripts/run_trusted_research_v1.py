"""无 AI 依赖的公共策略提交入口；配置仅登记数据和已有授权。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))

from scripts.lifecycle_deployment_v2 import build_submission_service


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest='operation', required=True)
    publication = commands.add_parser('publication', help='只读查询固定发布验收包，不加载数据或执行服务')
    publication.add_argument('--long-horizon', action='store_true', help='查询新版本252/504日工程验收；默认沿用旧凭证')
    for operation in ('capabilities', 'preview', 'diagnose', 'scan', 'freeze', 'approval', 'approve', 'start', 'pause', 'resume', 'status'):
        command = commands.add_parser(operation)
        command.add_argument('--workspace-root', required=True)
        command.add_argument('--deployment', required=True, help='维护者登记的 JSON 配置')
        if operation in {'preview', 'diagnose', 'scan', 'freeze'}:
            command.add_argument('--request', required=True, help='声明式策略 JSON')
        if operation in {'approval', 'approve', 'start', 'pause', 'resume', 'status'}:
            command.add_argument('--task-id', required=True)
        if operation in {'diagnose', 'scan', 'freeze', 'approve'}:
            command.add_argument('--preview-identity', required=True)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    if args.operation == 'publication':
        from chanlun_trader.research_factory.research_capabilities_v1 import published_acceptance, published_long_horizon_acceptance
        reader = published_long_horizon_acceptance if args.long_horizon else published_acceptance
        print(json.dumps(reader(), ensure_ascii=False, indent=2))
        return 0
    try:
        config = json.loads(Path(args.deployment).read_text(encoding='utf-8-sig'))
        service = build_submission_service(args.workspace_root, config)
        if args.operation == 'capabilities':
            result = service.capabilities()
        elif args.operation in {'preview', 'diagnose', 'scan', 'freeze'}:
            request = json.loads(Path(args.request).read_text(encoding='utf-8-sig'))
            result = service.preview(request) if args.operation == 'preview' else getattr(service, args.operation)(request, args.preview_identity)
        elif args.operation == 'approval':
            result = service.approval_preview(args.task_id)
        elif args.operation == 'approve':
            result = service.approve(args.task_id, args.preview_identity)
        else:
            result = getattr(service, args.operation)(args.task_id)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        print(json.dumps({'status': 'REJECTED', 'reason': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
