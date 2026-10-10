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

from scripts.lifecycle_deployment_v2 import approve_continuous_evidence, approve_continuous_scope, build_lifecycle_service, build_submission_service


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest='operation', required=True)
    publication = commands.add_parser('publication', help='只读查询固定发布验收包，不加载数据或执行服务')
    publication.add_argument('--long-horizon', action='store_true', help='查询新版本252/504日工程验收；默认沿用旧凭证')
    for action in ('preview', 'owner-approve', 'owner-approve-evidence', 'create', 'status', 'start', 'advance', 'pause', 'resume', 'revoke', 'grant', 'handover'):
        command = commands.add_parser('continuous-' + action, help='固定部署的 V4 持续研究控制')
        command.add_argument('--workspace-root', required=True)
        command.add_argument('--deployment', required=True, help='维护者登记的统一生命周期 JSON')
        command.add_argument('--research-id', required=True)
        if action in {'create', 'grant'}:
            command.add_argument('--approval-reference', required=True, help='Owner CLI 返回的 approval_ref JSON 文件')
        if action in {'pause', 'resume', 'revoke'}:
            command.add_argument('--reason', required=True)
        if action in {'preview', 'owner-approve', 'grant'}:
            command.add_argument('--grant', required=action == 'grant', help='增量授权 JSON；不提供时预览/批准原始总授权')
        if action in {'owner-approve', 'owner-approve-evidence'}:
            command.add_argument('--approver', required=True)
        if action == 'owner-approve-evidence':
            command.add_argument('--evidence', required=True, help='已经实际审核的固定schema原件JSON')
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
        from scripts.run_strategy_lifecycle_v1 import load_config
        config = load_config(args.deployment, Path(args.workspace_root) / 'lifecycle_jobs')
        if args.operation.startswith('continuous-'):
            action = args.operation.removeprefix('continuous-')
            grant = load_config(args.grant, Path(args.workspace_root) / 'lifecycle_jobs') if getattr(args, 'grant', None) else None
            if action == 'owner-approve-evidence':
                evidence = load_config(args.evidence, Path(args.workspace_root) / 'lifecycle_jobs')
                result = approve_continuous_evidence(config, args.research_id, evidence, approver=args.approver)
            elif action == 'owner-approve':
                result = approve_continuous_scope(args.workspace_root, config, args.research_id,
                    approver=args.approver, grant=grant)
            else:
                controller = build_lifecycle_service(args.workspace_root, config).continuous
                if controller is None:
                    raise ValueError('CONTINUOUS_DEPLOYMENT_REQUIRED')
                if action == 'preview':
                    result = controller.preview(args.research_id, grant=grant)
                elif action == 'status':
                    result = controller.status(args.research_id)
                else:
                    payload = {}
                    if action in {'create', 'grant'}:
                        reference = load_config(args.approval_reference, Path(args.workspace_root) / 'lifecycle_jobs')
                        payload['approval_ref'] = reference.get('approval_ref', reference)
                    if action in {'pause', 'resume', 'revoke'}:
                        payload['reason'] = args.reason
                    if action == 'grant':
                        payload['grant'] = grant
                    result = controller.perform(args.research_id, action, payload)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
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
