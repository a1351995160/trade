"""规划、冻结或核验已有Owner批准的失败后接续；不签发许可、不预留或派发。"""
import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
SPEC_FIELDS = frozenset({'authorization_id', 'task_ref', 'predecessor_manifest_ref', 'source_replacements',
    'runtime_source_refs', 'economic_source_refs', 'proposal_ref', 'ci_ref'})


def read_reference(path, digest):
    from chanlun_trader.research_factory.secure_file_reference_v1 import read_pinned_json
    return read_pinned_json({'path': path, 'sha256': digest}, root=ROOT,
        error_code='FAILED_ACCOUNT_RECOVERY_SETUP_REFERENCE_CHANGED')


def plan(spec):
    from chanlun_trader.research_factory.failed_account_recovery_v1 import FailedAccountRecoveryV1
    if not isinstance(spec, dict) or set(spec) not in (SPEC_FIELDS, SPEC_FIELDS | {'calendar_deadline_extension'}):
        raise ValueError('FAILED_ACCOUNT_RECOVERY_SETUP_SPEC_FIELDS_INVALID')
    return FailedAccountRecoveryV1.summary(ROOT, **spec)


def freeze(spec, *, approval_ref):
    from chanlun_trader.research_factory.failed_account_recovery_v1 import FailedAccountRecoveryV1
    summary = plan(spec)
    reference = FailedAccountRecoveryV1.freeze(ROOT, summary, approval_ref=approval_ref)
    return {'status': 'FAILED_ACCOUNT_RECOVERY_FROZEN_NO_RESERVATION_OR_DISPATCH',
        'manifest_ref': reference, 'recovery_id': summary['recovery_id'], 'dispatched_segments': 0,
        'reservation_delta_applied': 0}


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('action', choices=('plan', 'freeze', 'inspect'))
    cli.add_argument('--spec')
    cli.add_argument('--spec-sha256')
    cli.add_argument('--approval')
    cli.add_argument('--approval-sha256')
    cli.add_argument('--manifest')
    cli.add_argument('--manifest-sha256')
    args = cli.parse_args(argv)
    if args.action == 'inspect':
        if not args.manifest or not args.manifest_sha256:
            cli.error('inspect requires --manifest and --manifest-sha256')
        from chanlun_trader.research_factory.failed_account_recovery_v1 import FailedAccountRecoveryV1
        context = FailedAccountRecoveryV1.read(ROOT, {'path': args.manifest, 'sha256': args.manifest_sha256})
        result = {'status': 'FAILED_ACCOUNT_RECOVERY_VERIFIED_NO_DISPATCH',
            'failed_account_recovery': context.proof(), 'dispatched_segments': 0}
    else:
        if not args.spec or not args.spec_sha256:
            cli.error('plan/freeze requires --spec and --spec-sha256')
        spec = read_reference(args.spec, args.spec_sha256)
        if args.action == 'plan':
            result = {'status': 'FAILED_ACCOUNT_RECOVERY_PLANNED_NO_DISPATCH',
                'summary': plan(spec), 'dispatched_segments': 0}
        else:
            if not args.approval or not args.approval_sha256:
                cli.error('freeze requires the registered Owner approval reference')
            result = freeze(spec, approval_ref=read_reference(args.approval, args.approval_sha256))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
