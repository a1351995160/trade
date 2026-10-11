"""对已有报告工程失败执行一次专门批准的有账修复，不重新执行账户。"""
import argparse
import json
from pathlib import Path
import sys


ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]


def advance(root,manifest_ref,*,step=True):
    from chanlun_trader.research_factory.report_repair_protocol_v1 import ReportRepairV1
    from chanlun_trader.research_factory.exploration_governance import read_json
    from scripts.run_strategy_account_v1 import run_long_horizon_compute
    context=ReportRepairV1(root,manifest_ref).validate()
    result=run_long_horizon_compute(context.job_path,context.authority,context.request,'REPORT',
        step=step,_report_repair_ref=manifest_ref)
    if step and result['status']!='COMPLETED':return result
    task=read_json(context.job_path.parent.parent/'TASK.json')
    outcome={'task_id':task['task_id'],'status':'ACCOUNT_VERIFIED',
        'verification':read_json(context.job_path.parent/'VERIFICATION.json'),
        'reports':result['result'] if step else result,'dispatched_segments':0,
        'input_identity':task['input_identity'],
        'phase':context.request['phase'],'strategy_qualified':False}
    # rule 的身份由公共工厂计算，不能把 payload 中不存在/未经校验的标签当作身份。
    from chanlun_trader.research_factory.strategy_submission_v1 import public_rule_factory
    outcome['rule_identity']=public_rule_factory(context.request['rule'],context.request['strategy_id']).rule_identity
    receipt=context.receipt(outcome)
    return {'status':'REPORT_REPAIRED','repair_id':context.repair_id,
        'receipt':str(context.output_root/'REPAIR_COMPLETION.json'),
        'receipt_identity':receipt['identity'],'dispatched_segments':result.get('dispatched_segments',0) if step else None}


def feedback(root,manifest_ref):
    from chanlun_trader.research_factory.report_repair_protocol_v1 import ReportRepairV1
    from chanlun_trader.research_factory.business_validation_protocol_v1 import (
        BusinessValidationProtocolV1,evaluate_business_reports)
    from chanlun_trader.research_factory.exploration_governance import immutable,read_json
    from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
    from scripts.run_strategy_account_v1 import sha
    context=ReportRepairV1(root,manifest_ref).validate(for_dispatch=False)
    receipt=context.verify_receipt();outcome=receipt['outcome']
    task_path=context.job_path.parent.parent/'TASK.json';task=read_json(task_path)
    refs=BusinessValidationProtocolV1.verify_report_repair_refs(task,outcome,root=root,
        manifest_ref=manifest_ref,minimum_sessions=504)
    operation=context.meter(for_dispatch=False).campaign_operation
    campaign=ResearchCampaignV1(operation['root'],operation['authorization_id'])
    contract=campaign.peek_status()['base_authorization']['scope_policy']['summary']['contract']
    reports={cost:read_json(row['report_ref']['research_report']) for cost,row in refs.items()}
    value={'evaluation':evaluate_business_reports(contract,reports,minimum_sessions=504),
        'report_refs':refs,'rule_identity':outcome['rule_identity'],'task_id':task['task_id'],
        'task_sha256':sha(task_path),'repair_manifest':manifest_ref,
        'repair_receipt':{'path':str(context.output_root/'REPAIR_COMPLETION.json'),
            'sha256':sha(context.output_root/'REPAIR_COMPLETION.json')},
        'original_candidate_status':'FAILED','engineering_status':'REPORT_REPAIRED',
        'account_summaries':{cost:{key:item for key,item in report['account'].items() if key!='episodes'}
            for cost,report in reports.items()},'signal_evidence':{cost:report['signal'] for cost,report in reports.items()},
        'independent_days':0,'formal_qualification':False,'paper_days':0}
    target=context.output_root/'FEEDBACK.json';immutable(target,value)
    return {'status':'REPAIRED_FEEDBACK_VERIFIED','feedback':{'path':str(target),'sha256':sha(target)},
        'repair_id':context.repair_id,'original_candidate_status':'FAILED'}


def main(argv=None):
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('action',choices=('advance','run','reconcile','feedback','verify'))
    cli.add_argument('--manifest',required=True)
    cli.add_argument('--manifest-sha256',required=True)
    args=cli.parse_args(argv);reference={'path':args.manifest,'sha256':args.manifest_sha256}
    from chanlun_trader.research_factory.report_repair_protocol_v1 import ReportRepairV1
    if args.action in ('advance','run'):
        result=advance(ROOT,reference,step=args.action=='advance')
    elif args.action=='reconcile':
        from scripts.run_strategy_account_v1 import reconcile_long_horizon_compute
        context=ReportRepairV1(ROOT,reference).validate(for_dispatch=False)
        reconcile_long_horizon_compute(context.job_path,'REPORT',_report_repair_ref=reference)
        result={'status':'REPORT_REPAIR_RECONCILED_NO_DISPATCH','repair_id':context.repair_id}
    elif args.action=='feedback':result=feedback(ROOT,reference)
    else:
        record=ReportRepairV1(ROOT,reference).validate(for_dispatch=False).verify_receipt()
        result={'status':'REPORT_REPAIR_RECEIPT_VERIFIED','receipt_identity':record['identity']}
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
