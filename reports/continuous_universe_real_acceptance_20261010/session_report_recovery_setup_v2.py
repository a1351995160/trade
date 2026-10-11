"""本次明确批准的有限报告恢复组合根；不运行账户，不直接启动研究。"""
from __future__ import annotations

import argparse
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path('E:/llmwiki/trade-system-contract-port-v1')
REPORT = ROOT / 'reports/continuous_universe_real_acceptance_20261010'
OWNER = Path('C:/Users/84219/AppData/Local/TradeSystemAcceptance/continuous-universe-20261010')
CAMPAIGN = 'SESSION_REAL_ACCEPTANCE_20261010_2B'
OBJECTIVE = 'SESSION_REAL_ACCEPTANCE_20261010_504_2B'
PARENT_REPAIR = '0414b7205ac52a70bbd62b81739fdf778279781da9224842d99fefbf2d6a054a'
TASK = '13e83f9982b7b9fc49fbe9e479c5c7d9caf3f7726924801ad13adc46112a4872'
TASK_ROOT = REPORT / 'session_public/tasks' / TASK
PARENT_REF = {'path': str(TASK_ROOT / 'report-repairs' / PARENT_REPAIR / 'MANIFEST.json'),
              'sha256': '18f714138eec8a1dd2711ee4a03dce916df75f64d41f66f99332dc936d33ccc9'}
PROPOSAL_SHA = '7cfda0b33bd06d6bc7028d96b0affbce4940d41fa2dcdf49c4cbdc2045871f4e'
HUMAN_SHA = '1a813855e2ff78eed7a3556fa4d19b8b0707ce270caed2418ebb8b9205e7dbe6'
GRANT_ID = 'SESSION_B001_REPORT_RECOVERY_20261011'
OLD_GRANT_ID = 'SESSION_B001_REPORT_REPAIR_20261011'
OUTPUT = OWNER / 'report_recovery_setup_v2'
VERSION = 'SESSION_REPORT_RECOVERY_SETUP_V2'


def require(condition, reason):
    if not condition:
        raise PermissionError('SESSION_REPORT_RECOVERY_' + reason)


def imports():
    sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
    from chanlun_trader.research_factory.secure_file_reference_v1 import file_sha256, read_pinned_json
    return file_sha256, read_pinned_json


def ref(path):
    digest, _ = imports()
    path = Path(path)
    boundary = OWNER if path.is_relative_to(OWNER) else ROOT
    require(path.is_relative_to(boundary), 'PATH_OUTSIDE_ROOT')
    return {'path': str(path), 'sha256': digest(path, root=boundary,
        error_code='SESSION_RECOVERY_SOURCE_CHANGED')}


def read(reference):
    _, pinned = imports()
    path = Path(reference['path'])
    return pinned(reference, root=OWNER if path.is_relative_to(OWNER) else ROOT,
        maximum_bytes=64 * 1024 * 1024, error_code='SESSION_RECOVERY_SOURCE_CHANGED')


def fixed_runner():
    path = REPORT / 'session_public_runner_v1.py'
    require(ref(path)['sha256'] == '682109692103964b047a24b36c5dfa81a31a807482ee3425cee0182da5b05e10',
        'FIXED_RUNNER_CHANGED')
    spec = importlib.util.spec_from_file_location('recovery_fixed_session_runner_v1', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.verify_owner_boundary()
    require(module.ROOT == ROOT and module.OWNER_ROOT == OWNER, 'OWNER_ROOT_CONFLICT')
    return module


def human_scope():
    proposal = ref(REPORT / 'report_failure_20261011/REPORT_RENDER_CONTINUATION_RECOVERY_PROPOSAL_CN.md')
    confirmation = ref(REPORT / 'report_failure_20261011/HUMAN_REPORT_RENDER_RECOVERY_CONFIRMATION.json')
    require(proposal['sha256'] == PROPOSAL_SHA and confirmation['sha256'] == HUMAN_SHA,
        'APPROVED_SCOPE_CHANGED')
    value = read(confirmation)
    require(value.get('origin') == 'CURRENT_USER_REPLY_TO_EXACT_FINITE_REPORT_RENDER_RECOVERY_PROPOSAL'
        and value.get('user_reply') == '批准这一次有限恢复（推荐）' and value.get('proposal') == proposal
        and value.get('parent_repair_id') == PARENT_REPAIR
        and value.get('parent_manifest_sha256') == PARENT_REF['sha256']
        and value.get('campaign_id') == CAMPAIGN and value.get('objective_id') == OBJECTIVE
        and value.get('verification_jobs_delta') == value.get('maximum_additional_report_recoveries') == 1
        and value.get('effective_verification_jobs_from') == 5
        and value.get('effective_verification_jobs_to') == 6
        and value.get('recovery_total_seconds') == 28800
        and value.get('worker_seconds') == 900 and value.get('memory_mib') == 2048
        and value.get('campaign_total_wall_seconds') == 288002
        and value.get('expires_at') == '2026-10-17T19:43:08.066473+08:00'
        and value.get('account_replay_permitted') is False
        and value.get('strategy_threshold_changes_permitted') is False,
        'HUMAN_SCOPE_CONFLICT')
    return proposal, confirmation


def base_context():
    fixed = fixed_runner()
    from chanlun_trader.research_factory.campaign_scope_v1 import OwnerApprovalStoreV1
    from chanlun_trader.research_factory.research_campaign_v1 import ResearchCampaignV1
    campaign = ResearchCampaignV1(ROOT, CAMPAIGN)
    base = read(ref(campaign.authorization_path))
    require(base['authorization_id'] == CAMPAIGN and base['objective_id'] == OBJECTIVE
        and base['scope_policy']['approval_store'] == str(OWNER.resolve())
        and base['expires_at'] == '2026-10-17T19:43:08.066473+08:00'
        and base['resource_limits']['wall_seconds'] == 288002, 'BASE_OR_OWNER_CONFLICT')
    OwnerApprovalStoreV1(OWNER).require(base['scope_policy']['approval_ref'], base['scope_policy']['summary'])
    proposal, human = human_scope()
    require(ref(PARENT_REF['path']) == PARENT_REF, 'PARENT_MANIFEST_CHANGED')
    old_setup = read({'path': str(OWNER / 'campaign_setup/SETUP.json'),
        'sha256': '26a4f9313a37fc16683868dfccea4a7751c6be4d579d23870130c0f0d6072e6f'})
    config = read(old_setup['runner_config'])
    contract = fixed.protected_campaign_contract(config, {'base_authorization': base})
    require(old_setup['campaign_id'] == CAMPAIGN and old_setup['contract_identity'] == contract['content_hash'],
        'ORIGINAL_SETUP_CONFLICT')
    return fixed, campaign, base, proposal, human


def owner_approvals():
    from chanlun_trader.research_factory.campaign_scope_v1 import OwnerApprovalStoreV1
    token = object()
    return OwnerApprovalStoreV1(OWNER, owner_capability=token), token


def stop_summary():
    base_context()
    from chanlun_trader.research_factory.report_repair_stop_v2 import ReportRepairStopV2
    return ReportRepairStopV2.summary(ROOT, PARENT_REF,
        reason='USER_APPROVED_FINITE_REPORT_RENDER_CONTINUATION_RECOVERY',
        controller_pid=28132, reader_pid=11232, human_confirmation_ref=human_scope()[1],
        proposal_ref=human_scope()[0])


def request_stop():
    base_context()
    from chanlun_trader.research_factory.report_repair_stop_v2 import ReportRepairStopV2
    target = Path(PARENT_REF['path']).parent / 'STOP_REQUEST_V2.json'
    if target.exists():
        existing = read(ref(target))
        summary, approval = existing['summary'], existing['approval_ref']
        proposal, human = human_scope()
        require(summary['parent_manifest_ref'] == PARENT_REF
            and summary['proposal_ref'] == proposal and summary['human_confirmation_ref'] == human,
            'EXISTING_STOP_SCOPE_CONFLICT')
    else:
        summary = stop_summary()
        approvals, token = owner_approvals()
        approval = approvals.approve(summary, approver='USER_APPROVED_FINITE_REPORT_RECOVERY_BOUNDARY_STOP',
            capability=token)
    reference = ReportRepairStopV2.request(ROOT, summary, approval_ref=approval)
    from chanlun_trader.research_factory.exploration_governance import immutable
    immutable(OUTPUT / 'STOP_SETUP.json', {'schema_version': VERSION, 'summary': summary,
        'approval_ref': approval, 'stop_ref': reference, 'account_replay': False})
    return {'status': 'STOP_REQUESTED_NO_KILL_NO_NEW_DISPATCH', 'stop_ref': reference}


def settle_stop():
    base_context()
    from chanlun_trader.research_factory.report_repair_stop_v2 import ReportRepairStopV2
    setup = read(ref(OUTPUT / 'STOP_SETUP.json'))
    terminal = ReportRepairStopV2.settle(ROOT, setup['stop_ref'])
    from chanlun_trader.research_factory.exploration_governance import immutable
    immutable(OUTPUT / 'STOP_TERMINAL_REF.json', terminal)
    return {'status': 'ORIGINAL_REPORT_REPAIR_SETTLED_NO_ACCOUNT_REPLAY', 'parent_terminal_ref': terminal}


def preparation():
    fixed, campaign, base, proposal, human = base_context()
    from chanlun_trader.research_factory.campaign_scope_v1 import RESOURCES, grant_summary
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.report_recovery_protocol_v2 import ReportRecoveryV2
    from chanlun_trader.research_factory.report_recovery_capability_bridge_v2 import ReportRecoveryCapabilityBridgeV2
    terminal = read(ref(OUTPUT / 'STOP_TERMINAL_REF.json'))
    job_path = TASK_ROOT / 'account/JOB.json'
    job = read(ref(job_path))
    replacements = []
    for relative in ('scripts/run_strategy_account_v1.py',
        'src/chanlun_trader/research_factory/universe_account_inputs_v1.py',
        'src/chanlun_trader/research_factory/universe_benchmark_v1.py'):
        path = ROOT / relative
        old, current = job['source_hashes'][str(path)], ref(path)['sha256']
        require(old != current, 'RECOVERY_FIX_NOT_DEPLOYED')
        replacements.append({'path': str(path), 'old_sha256': old, 'new_sha256': current,
            'archive_ref': ref(job_path.parent / 'source-archive' / (old + '_' + path.name))})
    recovery_id = ReportRecoveryV2.recovery_identity(CAMPAIGN, PARENT_REF)
    context = {'parent_terminal_ref': terminal, 'recovery_id': recovery_id,
        'proposal_ref': proposal, 'human_confirmation_ref': human}
    bridge = ReportRecoveryCapabilityBridgeV2.summary(ROOT, base,
        recovery_context=context, source_replacements=replacements)
    delta = dict.fromkeys(RESOURCES, 0); delta['verification_jobs'] = 1
    grant = {'grant_id': GRANT_ID, 'base_authorization_hash': stable_hash(base),
        'resource_limits_delta': delta, 'stage_limits_delta': {'EXPLORATION': deepcopy(delta)},
        'max_batches_delta': 0, 'max_total_predictive_trials_delta': 0, 'expires_at': base['expires_at']}
    runtime = [ref(ROOT / relative) for relative in (
        'src/chanlun_trader/research_factory/report_recovery_protocol_v2.py',
        'src/chanlun_trader/research_factory/report_repair_stop_v2.py',
        'scripts/run_report_repair_stop_v2.py', 'scripts/run_report_recovery_v2.py',
        'src/chanlun_trader/research_factory/report_repair_protocol_v1.py',
        'src/chanlun_trader/research_factory/report_repair_capability_bridge_v1.py',
        'src/chanlun_trader/research_factory/report_recovery_capability_bridge_v2.py',
        'src/chanlun_trader/research_factory/business_validation_protocol_v1.py',
        'src/chanlun_trader/research_factory/campaign_scope_v1.py',
        'reports/continuous_universe_real_acceptance_20261010/session_report_recovery_setup_v2.py',
        'reports/continuous_universe_real_acceptance_20261010/session_next_batch_setup_v3.py')]
    intent = {'schema_version': VERSION, 'proposal_ref': proposal, 'human_confirmation_ref': human,
        'parent_manifest_ref': PARENT_REF, 'parent_terminal_ref': terminal, 'recovery_id': recovery_id,
        'capability_bridge': bridge, 'source_replacements': [{k: v for k, v in row.items()
            if k != 'archive_ref'} for row in replacements], 'runtime_sources': runtime,
        'grant': grant, 'grant_summary': grant_summary(base, grant),
        'replay_account': False, 'replay_verification': False}
    return fixed, campaign, base, intent


def create():
    fixed, campaign, base, intent = preparation()
    from chanlun_trader.research_factory.common import stable_hash
    from chanlun_trader.research_factory.exploration_governance import immutable
    from chanlun_trader.research_factory.report_recovery_capability_bridge_v2 import ReportRecoveryCapabilityBridgeV2
    from chanlun_trader.research_factory.report_recovery_protocol_v2 import ReportRecoveryV2
    approvals, token = owner_approvals()
    bridge_approval = approvals.approve(intent['capability_bridge'],
        approver='USER_APPROVED_EXACT_REPORT_RECOVERY_SOURCE_BRIDGE', capability=token)
    bridge_ref = ReportRecoveryCapabilityBridgeV2.install(ROOT, base, intent['capability_bridge'],
        approval_ref=bridge_approval)
    view = campaign.peek_status()
    require(set(view['grants']) <= {OLD_GRANT_ID, GRANT_ID} and OLD_GRANT_ID in view['grants'],
        'UNEXPECTED_GRANT')
    summary = ReportRecoveryV2.summary(ROOT, parent_terminal_ref=intent['parent_terminal_ref'],
        grant=intent['grant'], source_replacements=intent['source_replacements'],
        runtime_source_refs=intent['runtime_sources'], capability_bridge_ref=bridge_ref)
    grant_approval = approvals.approve(intent['grant_summary'],
        approver='USER_APPROVED_ONE_ADDITIONAL_REPORT_RECOVERY_INCREMENT', capability=token)
    approval = approvals.approve(summary,
        approver='USER_APPROVED_ONE_VERSIONED_REPORT_RECOVERY', capability=token)
    manifest = ReportRecoveryV2.freeze(ROOT, summary, approval_ref=approval,
        grant_approval_ref=grant_approval)
    setup = {'schema_version': VERSION, 'intent': intent, 'bridge_ref': bridge_ref,
        'grant_approval_ref': grant_approval, 'recovery_approval_ref': approval, 'manifest': manifest,
        'runner_argv': [str(ROOT / '.venv/Scripts/python.exe'), '-B', '-X', 'utf8',
            str(ROOT / 'scripts/run_report_recovery_v2.py'), 'run', '--manifest', manifest['path'],
            '--manifest-sha256', manifest['sha256']], 'account_jobs_replayed': 0, 'runner_started': False}
    setup['identity'] = stable_hash(setup)
    immutable(OUTPUT / 'SETUP.json', setup)
    fixed.verify_owner_boundary()
    return {'status': 'REPORT_RECOVERY_SETUP_READY_NO_DISPATCH', 'setup': ref(OUTPUT / 'SETUP.json'),
        'manifest': manifest, 'runner_argv': setup['runner_argv']}


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('action', choices=('plan-stop', 'request-stop', 'settle-stop', 'plan', 'create'))
    args = cli.parse_args(argv)
    action = {'plan-stop': stop_summary, 'request-stop': request_stop, 'settle-stop': settle_stop,
              'plan': lambda: preparation()[3], 'create': create}[args.action]
    print(json.dumps(action(), ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
