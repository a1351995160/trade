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
from chanlun_trader.research_factory.forward_snapshot_v1 import SnapshotStoreV1
from chanlun_trader.research_factory.forward_paper_v1 import ForwardPaperSessionV1


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest='operation', required=True)
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
    capture = commands.add_parser('capture')
    capture.add_argument('--snapshot-root', required=True)
    capture.add_argument('--phase', choices=('OPEN', 'CLOSE'), required=True)
    capture.add_argument('--symbols', nargs='+', required=True)
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
    if args.operation in ('freeze', 'review', 'revoke-strategy'):
        archive = BoundedStrategyArchiveV1(args.archive_root)
        if args.operation == 'freeze':
            return archive.freeze(args.research_root, args.candidate_id)
        if args.operation == 'review':
            return archive.review(args.strategy_id)
        return archive.revoke(args.strategy_id, args.reason)
    if args.operation == 'capture':
        value = SnapshotStoreV1(args.snapshot_root).capture_tdx(phase=args.phase, symbols=args.symbols)
        return {key:value[key] for key in ('snapshot_id', 'profile', 'phase', 'market_date', 'received_at')}
    if args.operation == 'create-paper':
        config = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
        allowed = {'archive_root', 'strategy_ids', 'policy', 'calendar', 'warmup', 'purpose', 'profile'}
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


def render_report(value):
    state = value.get('state') or {}
    economic = state.get('economic') or {}
    plan = value.get('next_plan') or {}
    lines = ['# 前瞻模拟观察日报', '',
        f"状态：{value['status']}；数据类型：{value['profile']}；用途：{value['purpose']}。",
        f"真实观察：{value['real_observation_days']} 天；其中合格观察：{value['qualified_observation_days']} 天。",
        '策略尚未取得正式资格；没有真实券商成交。', '',
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
    except (ValueError, PermissionError, RuntimeError, OSError, KeyError, TypeError) as error:
        print(json.dumps({'status':'BLOCKED', 'reason':str(error)}, ensure_ascii=False))
        return 1
    print(render_report(result) if args.operation == 'report' else json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
