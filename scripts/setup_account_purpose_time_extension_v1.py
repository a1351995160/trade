"""规划、冻结或核验已有 Owner 批准的原两账户附加限时证明；不会派发或扩限。"""
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
        error_code='ACCOUNT_PURPOSE_TIME_EXTENSION_SETUP_REFERENCE_CHANGED')


def plan(spec):
    from chanlun_trader.research_factory.account_purpose_time_extension_v1 import AccountPurposeTimeExtensionV1
    if not isinstance(spec, dict) or set(spec) != SPEC_FIELDS:
        raise ValueError('ACCOUNT_PURPOSE_TIME_EXTENSION_SETUP_SPEC_FIELDS_INVALID')
    return AccountPurposeTimeExtensionV1.summary(ROOT, **spec)


def freeze(spec, *, approval_ref):
    from chanlun_trader.research_factory.account_purpose_time_extension_v1 import AccountPurposeTimeExtensionV1
    summary = plan(spec)
    reference = AccountPurposeTimeExtensionV1.freeze(ROOT, summary, approval_ref=approval_ref)
    return {'status': 'ACCOUNT_PURPOSE_TIME_EXTENSION_FROZEN_NO_RESERVATION_OR_DISPATCH',
        'manifest_ref': reference, 'extension_id': summary['extension_id'], 'dispatched_segments': 0,
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
        from chanlun_trader.research_factory.account_purpose_time_extension_v1 import AccountPurposeTimeExtensionV1
        context = AccountPurposeTimeExtensionV1.read(ROOT, {'path': args.manifest, 'sha256': args.manifest_sha256})
        result = {'status': 'ACCOUNT_PURPOSE_TIME_EXTENSION_VERIFIED_NO_DISPATCH',
            'account_purpose_time_extension': context.proof(), 'dispatched_segments': 0}
    else:
        if not args.spec or not args.spec_sha256:
            cli.error('plan/freeze requires --spec and --spec-sha256')
        spec = read_reference(args.spec, args.spec_sha256)
        if args.action == 'plan':
            result = {'status': 'ACCOUNT_PURPOSE_TIME_EXTENSION_PLANNED_NO_DISPATCH',
                'summary': plan(spec), 'dispatched_segments': 0}
        else:
            if not args.approval or not args.approval_sha256:
                cli.error('freeze requires the registered Owner approval reference')
            result = freeze(spec, approval_ref=read_reference(args.approval, args.approval_sha256))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
