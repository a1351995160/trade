"""策略档案、有效性评审、前瞻模拟观察与共享组合的统一入口。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from chanlun_trader.research_factory.strategy_qualification_v1 import BoundedStrategyArchiveV1
from chanlun_trader.research_factory.public_strategy_archive_v3 import PublicStrategyArchiveV3, archive_for_ids
from chanlun_trader.research_factory.forward_snapshot_v1 import SnapshotStoreV1
from chanlun_trader.research_factory.forward_paper_v1 import ForwardPaperSessionV1


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest='operation', required=True)
    for operation in ('lifecycle-preview', 'lifecycle-create', 'lifecycle-start', 'lifecycle-status',
                      'lifecycle-pause', 'lifecycle-resume', 'lifecycle-tick'):
        command = commands.add_parser(operation)
        command.add_argument('--workspace-root', required=True)
        command.add_argument('--bindings', required=True)
        if operation == 'lifecycle-create':
            command.add_argument('--config', required=True)
        elif operation != 'lifecycle-preview':
            command.add_argument('--job-id', required=True)
    for operation in ('freeze', 'review', 'revoke-strategy'):
        command = commands.add_parser(operation)
        command.add_argument('--archive-root', required=True)
        if operation == 'freeze':
            command.add_argument('--research-root', required=True)
            command.add_argument('--candidate-id', required=True)
        else:
            command.add_argument('--strategy-id', required=True)
        if operation == 'revoke-strategy':
            command.add_argument('--reason', required=True)
    public = commands.add_parser('public-freeze')
    public.add_argument('--archive-root', required=True)
    public.add_argument('--job-path', required=True)
    public.add_argument('--name', required=True)
    daily = commands.add_parser('daily-plan')
    daily.add_argument('--root', required=True)
    capture = commands.add_parser('capture')
    capture.add_argument('--snapshot-root', required=True)
    capture.add_argument('--phase', choices=('OPEN', 'CLOSE'), required=True)
    capture.add_argument('--symbols', nargs='+', required=True)
    for operation in ('validation-preview', 'validation-freeze', 'validation-status'):
        command = commands.add_parser(operation)
        if operation == 'validation-status':
            command.add_argument('--protocol-path', required=True)
        else:
            command.add_argument('--archive-root', required=True)
            command.add_argument('--config', required=True)
    for operation in ('formal-review', 'formal-register', 'formal-run', 'formal-status', 'formal-calendar', 'formal-feedback'):
        command = commands.add_parser(operation)
        command.add_argument('--archive-root', required=True)
        if operation in ('formal-review', 'formal-register', 'formal-run'):
            command.add_argument('--config', required=True)
        if operation not in ('formal-review', 'formal-register'):
            command.add_argument('--batch-id', required=True)
        if operation == 'formal-calendar':
            command.add_argument('--end', required=True, type=int)
    for operation in ('create-paper', 'advance', 'status', 'report', 'revoke-paper'):
        command = commands.add_parser(operation)
        command.add_argument('--root', required=True)
        if operation == 'create-paper':
            command.add_argument('--config', required=True)
        if operation == 'advance':
            command.add_argument('--snapshot-root', required=True)
            command.add_argument('--snapshot-id', required=True)
        if operation == 'revoke-paper':
            command.add_argument('--reason', required=True)
    return cli


def execute(args):
    if args.operation.startswith('lifecycle-'):
        from scripts.lifecycle_deployment_v2 import build_lifecycle_service
        bindings = load_config(args.bindings, Path(args.workspace_root) / 'lifecycle_jobs')
        service = build_lifecycle_service(args.workspace_root, bindings)
        action = args.operation.removeprefix('lifecycle-')
        if action == 'preview':
            return service.inspect()
        if action == 'create':
            return service.create_job(**load_config(args.config, Path(args.workspace_root) / 'lifecycle_jobs'))
        return getattr(service.jobs, action)(args.job_id)
    if args.operation.startswith('validation-'):
        from chanlun_trader.research_factory.validation_protocol_v2 import (
            freeze_protocol, load_protocol, preview_protocol,
        )
        if args.operation == 'validation-status':
            return load_protocol(args.protocol_path)
        config = load_config(args.config, args.archive_root)
        if not isinstance(config, dict) or set(config) - {
                'strategy_ids', 'symbols', 'not_before', 'route', 'metadata_report'}:
            raise ValueError('VALIDATION_CONFIG_UNKNOWN_FIELDS')
        operation = preview_protocol if args.operation == 'validation-preview' else freeze_protocol
        return operation(archive_root=args.archive_root, **config)
    if args.operation.startswith('formal-'):
        from chanlun_trader.research_factory.formal_assessment_v1 import FormalAssessmentServiceV1
        from chanlun_trader.research_factory.formal_evidence_v1 import CalendarEvidenceStoreV1
        service = FormalAssessmentServiceV1(args.archive_root)
        if args.operation == 'formal-review':
            return service.readiness(**load_config(args.config, service.root))
        if args.operation == 'formal-register':
            return service.register(**load_config(args.config, service.root))
        if args.operation == 'formal-run':
            return service.run(args.batch_id, **load_config(args.config, service.root))
        if args.operation == 'formal-feedback':
            return service.feedback(args.batch_id)
        if args.operation == 'formal-calendar':
            plan = service.plan(args.batch_id)
            return CalendarEvidenceStoreV1(service.path(args.batch_id, 'calendar')).capture_tdx(
                start=plan['not_before'], end=args.end)
        return service.status(args.batch_id)
    if args.operation == 'public-freeze':
        return PublicStrategyArchiveV3(args.archive_root).freeze(args.job_path, args.name)
    if args.operation == 'daily-plan':
        from chanlun_trader.research_factory.trusted_daily_plan_v1 import trusted_daily_plan
        return trusted_daily_plan(ForwardPaperSessionV1(args.root))
    if args.operation in ('freeze', 'review', 'revoke-strategy'):
        archive = (BoundedStrategyArchiveV1(args.archive_root) if args.operation == 'freeze'
                   else archive_for_ids(args.archive_root, [args.strategy_id]))
        if args.operation == 'freeze':
            return archive.freeze(args.research_root, args.candidate_id)
        if args.operation == 'review':
            return archive.review(args.strategy_id)
        return archive.revoke(args.strategy_id, args.reason)
    if args.operation == 'capture':
        value = SnapshotStoreV1(args.snapshot_root).capture_tdx(phase=args.phase, symbols=args.symbols)
        return {key:value[key] for key in ('snapshot_id', 'profile', 'phase', 'market_date', 'received_at')}
    if args.operation == 'create-paper':
        config = load_config(args.config, args.root)
        allowed = {'archive_root', 'strategy_ids', 'policy', 'calendar', 'warmup', 'purpose', 'profile',
                   'observation_policy', 'company_actions', 'portfolio_review_root'}
        if not isinstance(config, dict) or set(config) - allowed:
            raise ValueError('PAPER_CONFIG_UNKNOWN_FIELDS')
        session = ForwardPaperSessionV1.create(args.root, **config)
        return session.status()
    session = ForwardPaperSessionV1(args.root)
    if args.operation == 'advance':
        return session.ingest(args.snapshot_root, args.snapshot_id)
    if args.operation == 'revoke-paper':
        return session.revoke(args.reason)
    return session.status()


def load_config(filename, paper_root):
    # 配置只从所选观察目录的父目录读取，拒绝越界、链接重定向及非JSON文件。
    base = Path(paper_root).absolute().parent
    path = Path(filename).absolute()
    if (base.resolve() != base or path.resolve() != path or '..' in path.parts
            or not path.is_relative_to(base) or path.suffix.lower() != '.json'):
        raise ValueError('PAPER_CONFIG_PATH_OUTSIDE_OBSERVATION_WORKSPACE')
    if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('PAPER_CONFIG_FILE_INVALID')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def render_report(value):
    state = value.get('state') or {}
    economic = state.get('economic') or {}
    plan = value.get('next_plan') or {}
    lines = ['# 前瞻模拟观察日报', '',
        f"状态：{value['status']}；数据类型：{value['profile']}；用途：{value['purpose']}。",
        f"真实观察：{value['real_observation_days']} 天；其中合格观察：{value['qualified_observation_days']} 天。",
        ('权威策略资格仍有效，但本次观察已停止新增买入，不视为观察通过；没有真实券商成交。'
         if value.get('authority_strategy_qualified', False) else
         '策略具备有条件的正式观察资格；没有真实券商成交。' if value.get('strategy_qualified', False)
         else '策略尚未取得正式资格；没有真实券商成交。'), '',
        f"账户净值：{state.get('equity', '尚无行情')}；现金：{economic.get('cash', '尚无行情')}；累计模拟成交：{len(economic.get('trades', []))} 笔。",
        f"下一计划交易日：{plan.get('next_session', '暂无')}。", '',
        '| 策略 | 证券 | 意图 | 原因 |', '|---|---|---|---|']
    for item in plan.get('intents', []):
        lines.append(f"| {item['strategy_id']} | {item['symbol']} | {item['side']} | {item['reason']} |")
    if not plan.get('intents'):
        lines.append('| — | — | 无新交易意图 | 等待数据或未满足规则 |')
    lines.extend(['', *value.get('limitations', [])])
    return '\n'.join(lines) + '\n'


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = execute(args)
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
        print(json.dumps({'status':'BLOCKED', 'reason':str(error)}, ensure_ascii=False))
        return 1
    print(render_report(result) if args.operation == 'report' else json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
