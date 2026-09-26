"""一次范围明确的真实探索：create / run / status / revoke；不调用正式晋级。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'src'))

from chanlun_trader.research_factory.bounded_research_v1 import BoundedResearchSessionV1, file_hash
from chanlun_trader.research_factory.bounded_model_v1 import BoundedCodexInvokerV1
from chanlun_trader.research_factory.research_diagnostics_v1 import render_markdown
from scripts.verify_s1_price_recovery_v1 import _source_paths
from scripts.verify_s1_public_entry_v1 import _load_bundle
from scripts.verify_s1_corporate_chain_v1 import checked_events


def freeze_s1_manifest():
    """只冻结已验收样本来源，不选择或开放新的历史窗口。"""
    pilot=json.loads((ROOT/'reports/all_indicator_fixed_strategy_pilot_20260925/PROBE.json').read_text(encoding='utf-8'))
    reference=json.loads((ROOT/'reports/s1_trusted_baseline_20260925/PRICE_PIT_RESTART.json').read_text(encoding='utf-8'))
    turn=ROOT/'reports/all_indicator_fixed_strategy_pilot_20260925/BAOSTOCK_TURN.parquet'
    # 使用当前工作区已归档的同内容快照，并继续核对旧验收哈希。
    manifest=turn.with_suffix('.manifest.json')
    paths=_source_paths(Path(pilot['input']['path']),turn,Path(pilot['states_input']['path']),
        Path('E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/materialized-v3/HISTORICAL_POOL_AND_STATE.parquet'),
        manifest, ROOT/'reports/s1_trusted_baseline_20260925/BAOSTOCK_ACTIONS.json')
    for key,path in paths.items():
        if file_hash(path)!=reference['source_hashes'][key+'_sha256']:
            raise ValueError('BOUNDED_FROZEN_SOURCE_MISMATCH:'+key)
    events=checked_events(json.loads(paths['corporate_actions'].read_text(encoding='utf-8')))
    return {'profile':'S1_MODELED_DAILY','paths':{k:str(v) for k,v in paths.items()},
            'source_hashes':reference['source_hashes'],'input_identity':reference['input_identity'],
            'events':list(events),'account_dates':reference['account_dates'],
            'prior_exposure':'S1_PREVIOUSLY_EXPOSED_NOT_INDEPENDENT_CONFIRMATION'}


def load_s1(manifest,strategy):
    if manifest['profile']!='S1_MODELED_DAILY':
        raise ValueError('BOUNDED_REAL_PROFILE_REQUIRED')
    refs=manifest['paths']
    paths=_source_paths(*(Path(refs[k]) for k in ('daily','turn','states','historical_states','turn_manifest','corporate_actions')))
    for key,path in paths.items():
        if file_hash(path)!=manifest['source_hashes'][key+'_sha256']:
            raise ValueError('BOUNDED_DATA_FILE_CHANGED:'+key)
    events=checked_events(json.loads(paths['corporate_actions'].read_text(encoding='utf-8')))
    if list(events)!=manifest['events']:
        raise ValueError('BOUNDED_EVENTS_CHANGED')
    audit=[]
    bundle=_load_bundle(paths,manifest['source_hashes'],audit,events,strategy)
    return bundle,events


def write_report(session,status):
    from chanlun_trader.research_factory.bounded_research_v1 import _read
    lines=['# 有预算的自主研究任务', '',f"任务：{status['objective_id']}",
           f"状态：{status['status']}", '策略资格：尚未评审；真实前瞻观察天数：0。',
           '本次沿用已曝光的两证券历史样本，结论只用于探索，不证明策略有效。']
    if status.get('reason'):lines.append('停止原因：'+status['reason'])
    for row in status['candidates']:
        name=row['candidate_id']
        lines.extend(['', '## '+name, '账户完成：'+str(row['account_completed'])])
        if row.get('proposal'):
            lines.extend([row['proposal']['hypothesis'],'修订理由：'+row['proposal']['change_reason'],
                          '父候选：'+str(row.get('parent'))])
        if row['diagnostic_available']:
            lines.append(render_markdown(_read(session.path(name,'DIAGNOSTIC.json'))))
        elif row.get('reason'):lines.append(row['reason'])
    session.path('SUMMARY.md').write_text('\n\n'.join(lines)+'\n',encoding='utf-8')
    session.path('SUMMARY.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['create','run','tick','status','revoke'])
    parser.add_argument('--root',required=True)
    parser.add_argument('--approval-statement')
    parser.add_argument('--attempts',type=int,default=5)
    parser.add_argument('--wall-seconds',type=int,default=3600)
    args=parser.parse_args()
    if args.operation=='create':
        session=BoundedResearchSessionV1.create(args.root,objective_id='BOUNDED_S1_RESEARCH_20260926',
            input_manifest=freeze_s1_manifest(),approval_statement=args.approval_statement,
            max_attempts=args.attempts,wall_seconds=args.wall_seconds)
        status=session.status()
    else:
        session=BoundedResearchSessionV1(args.root)
        if args.operation=='revoke':session.revoke('OPERATOR_STOP');status=session.status()
        elif args.operation=='status':status=session.status()
        else:
            from chanlun_trader.research_factory.autonomous_orchestrator_v2 import AutonomousResearchOrchestratorV2
            status=AutonomousResearchOrchestratorV2.run_bounded_exploration(
                session.root,loader=load_s1,invoker=BoundedCodexInvokerV1(),single_step=args.operation=='tick')
    write_report(session,status)
    print(json.dumps({k:v for k,v in status.items() if k not in ('candidates','budget')},ensure_ascii=False))
    return 1 if status['status']=='BLOCKED' else 0


if __name__=='__main__':
    raise SystemExit(main())
