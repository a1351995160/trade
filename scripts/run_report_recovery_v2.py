"""一次显式有账报告恢复的公共执行/核对入口；不运行原账户。"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]


def advance(root, manifest_ref, *, step=True):
    from chanlun_trader.research_factory.report_recovery_protocol_v2 import ReportRecoveryV2
    from chanlun_trader.research_factory.exploration_governance import read_json
    from chanlun_trader.research_factory.strategy_submission_v1 import public_rule_factory
    from scripts.run_strategy_account_v1 import run_long_horizon_compute
    context = ReportRecoveryV2(root, manifest_ref).validate()
    result = run_long_horizon_compute(context.job_path, context.authority, context.request, 'REPORT',
                                     step=step, _report_repair_ref=manifest_ref)
    if result.get('status') in ('PAUSED', 'CONTINUE'):
        return result
    task = read_json(context.task_root / 'TASK.json')
    outcome = {'task_id': task['task_id'], 'status': 'ACCOUNT_VERIFIED',
        'verification': read_json(context.job_path.parent / 'VERIFICATION.json'),
        'reports': result['result'] if step else result,
        'dispatched_segments': len(context.meter(for_dispatch=False).status()['segments']),
        'input_identity': task['input_identity'], 'phase': context.request['phase'], 'strategy_qualified': False,
        'rule_identity': public_rule_factory(context.request['rule'], context.request['strategy_id']).rule_identity}
    receipt = context.receipt(outcome)
    return {'status': 'REPORT_RECOVERED', 'repair_id': context.repair_id,
        'receipt': str(context.output_root / 'REPAIR_COMPLETION.json'), 'receipt_identity': receipt['identity'],
        'dispatched_segments': result.get('dispatched_segments', 0) if step else None}


def feedback(root, manifest_ref):
    from chanlun_trader.research_factory.report_recovery_protocol_v2 import ReportRecoveryV2
    from chanlun_trader.research_factory.business_validation_protocol_v1 import BusinessValidationProtocolV1, evaluate_business_reports
    from chanlun_trader.research_factory.exploration_governance import immutable, read_json
    from scripts.run_strategy_account_v1 import sha
    context = ReportRecoveryV2(root, manifest_ref).validate(for_dispatch=False)
    receipt = context.verify_receipt()
    outcome = receipt['outcome']
    task_path = context.task_root / 'TASK.json'
    task = read_json(task_path)
    refs = BusinessValidationProtocolV1.verify_report_repair_refs(task, outcome, root=root,
        manifest_ref=manifest_ref, minimum_sessions=504)
    contract = context.campaign.peek_status()['base_authorization']['scope_policy']['summary']['contract']
    reports = {cost: read_json(row['report_ref']['research_report']) for cost, row in refs.items()}
    value = {'evaluation': evaluate_business_reports(contract, reports, minimum_sessions=504),
        'report_refs': refs, 'rule_identity': outcome['rule_identity'], 'task_id': task['task_id'],
        'task_sha256': sha(task_path), 'repair_manifest': manifest_ref,
        'repair_receipt': {'path': str(context.output_root / 'REPAIR_COMPLETION.json'),
                           'sha256': sha(context.output_root / 'REPAIR_COMPLETION.json')},
        'parent_terminal_ref': context.summary['parent_terminal_ref'],
        'original_candidate_status': 'FAILED', 'engineering_status': 'REPORT_RECOVERED',
        'account_summaries': {cost: {key: item for key, item in report['account'].items() if key != 'episodes'}
                              for cost, report in reports.items()},
        'signal_evidence': {cost: report['signal'] for cost, report in reports.items()},
        'independent_days': 0, 'formal_qualification': False, 'paper_days': 0}
    target = context.output_root / 'FEEDBACK.json'
    immutable(target, value)
    return {'status': 'RECOVERED_FEEDBACK_VERIFIED', 'feedback': {'path': str(target), 'sha256': sha(target)},
            'repair_id': context.repair_id, 'original_candidate_status': 'FAILED'}


def main(argv=None):
    from chanlun_trader.research_factory.report_recovery_protocol_v2 import ReportRecoveryV2
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('action', choices=('advance', 'run', 'reconcile', 'feedback', 'verify', 'pause', 'resume'))
    cli.add_argument('--manifest', required=True)
    cli.add_argument('--manifest-sha256', required=True)
    cli.add_argument('--reason')
    args = cli.parse_args(argv)
    reference = {'path': args.manifest, 'sha256': args.manifest_sha256}
    if args.action in ('advance', 'run'):
        result = advance(ROOT, reference, step=args.action == 'advance')
    elif args.action == 'feedback':
        result = feedback(ROOT, reference)
    else:
        context = ReportRecoveryV2(ROOT, reference).validate(for_dispatch=False)
        if args.action == 'verify':
            result = {'status': 'REPORT_RECOVERY_RECEIPT_VERIFIED', 'identity': context.verify_receipt()['identity']}
        elif args.action == 'reconcile':
            from scripts.run_strategy_account_v1 import reconcile_long_horizon_compute
            reconcile_long_horizon_compute(context.job_path, 'REPORT', _report_repair_ref=reference)
            result = {'status': 'REPORT_RECOVERY_RECONCILED_NO_DISPATCH'}
        else:
            if not args.reason:
                cli.error('report control requires reason')
            result = context.control(args.action.upper(), args.reason)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
