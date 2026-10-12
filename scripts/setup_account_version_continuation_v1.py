"""账户版本接续的元信息规划和显式 Owner 批准接线；不派发 worker。"""
import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]


def read_reference(path, digest):
    from chanlun_trader.research_factory.secure_file_reference_v1 import read_pinned_json
    return read_pinned_json({'path': path, 'sha256': digest}, root=ROOT,
                            error_code='ACCOUNT_VERSION_SETUP_REFERENCE')


def plan(spec):
    from chanlun_trader.research_factory.account_version_capability_bridge_v1 import AccountVersionCapabilityBridgeV1
    from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
    required = {'authorization_id', 'task_ref', 'source_replacements', 'runtime_source_refs',
                'predecessor_bridge_ref', 'proposal_ref', 'ci_ref', 'session_refs'}
    if not isinstance(spec, dict) or set(spec) != required:
        raise ValueError('ACCOUNT_VERSION_SETUP_SPEC_FIELDS_INVALID')
    # 只读原授权原件；新能力桥安装前不能经当前能力校验循环自举。
    campaign = ResearchCampaignV1(ROOT, spec['authorization_id'])
    from chanlun_trader.research_factory.exploration_governance import read_json
    base = read_json(campaign.authorization_path)
    bridge = AccountVersionCapabilityBridgeV1.summary(ROOT, base,
        source_replacements=spec['source_replacements'], runtime_source_refs=spec['runtime_source_refs'],
        predecessor_bridge_ref=spec['predecessor_bridge_ref'])
    return {'capability_bridge_summary': bridge, 'base_authorization': base}


def freeze(spec, *, bridge_approval_ref, approval_ref=None):
    from chanlun_trader.research_factory.account_version_capability_bridge_v1 import AccountVersionCapabilityBridgeV1
    from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
    intent = plan(spec)
    bridge = AccountVersionCapabilityBridgeV1.install(ROOT, intent['base_authorization'],
        intent['capability_bridge_summary'], approval_ref=bridge_approval_ref)
    summary = AccountVersionContinuationV1.summary(ROOT, authorization_id=spec['authorization_id'],
        task_ref=spec['task_ref'], capability_bridge_ref=bridge, proposal_ref=spec['proposal_ref'],
        ci_ref=spec['ci_ref'], session_refs=spec['session_refs'])
    if approval_ref is None:
        return {'status': 'OWNER_CONTINUATION_APPROVAL_REQUIRED', 'summary': summary,
                'capability_bridge_ref': bridge, 'dispatched_segments': 0}
    manifest = AccountVersionContinuationV1.freeze(ROOT, summary, approval_ref=approval_ref)
    return {'status': 'ACCOUNT_VERSION_FROZEN_NO_DISPATCH', 'manifest_ref': manifest,
            'capability_bridge_ref': bridge, 'dispatched_segments': 0}


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('action', choices=('plan', 'freeze', 'inspect'))
    cli.add_argument('--spec')
    cli.add_argument('--spec-sha256')
    cli.add_argument('--bridge-approval')
    cli.add_argument('--bridge-approval-sha256')
    cli.add_argument('--approval')
    cli.add_argument('--approval-sha256')
    cli.add_argument('--manifest')
    cli.add_argument('--manifest-sha256')
    args = cli.parse_args(argv)
    if args.action == 'inspect':
        if not args.manifest or not args.manifest_sha256:
            cli.error('inspect requires --manifest and --manifest-sha256')
        from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
        context = AccountVersionContinuationV1(ROOT,
            {'path': args.manifest, 'sha256': args.manifest_sha256}).validate(for_dispatch=False)
        result = {'status': 'ACCOUNT_VERSION_VERIFIED_NO_DISPATCH', 'account_version_continuation': context.proof()}
    else:
        if not args.spec or not args.spec_sha256:
            cli.error('plan/freeze requires --spec and --spec-sha256')
        spec = read_reference(args.spec, args.spec_sha256)
        if args.action == 'plan':
            result = {'status': 'ACCOUNT_VERSION_PLANNED_NO_DISPATCH', **plan(spec)}
        else:
            if not args.bridge_approval or not args.bridge_approval_sha256:
                cli.error('freeze requires the registered Owner bridge approval')
            if bool(args.approval) != bool(args.approval_sha256):
                cli.error('approval path and sha256 must be supplied together')
            result = freeze(spec,
                bridge_approval_ref=read_reference(args.bridge_approval, args.bridge_approval_sha256),
                approval_ref=(read_reference(args.approval, args.approval_sha256) if args.approval else None))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
