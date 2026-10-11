"""第二次有限报告恢复的组合根约束；不读取真实资料或批准记录。"""
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory.campaign_scope_v1 import RESOURCES
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.universe_execution_profile_v1 import SEGMENTED_PROFILE, execution_profile


def helper(name):
    path = Path(__file__).resolve().parents[2] / 'reports/continuous_universe_real_acceptance_20261010' / name
    spec = importlib.util.spec_from_file_location('synthetic_' + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require(value, reason):
    if not value:
        raise PermissionError(reason)


def view():
    units = dict.fromkeys(RESOURCES, 0)
    units.update(candidate_attempts=2, data_experiments=2, account_jobs=4,
                 verification_jobs=4, wall_seconds=288002)
    profiles = [execution_profile(SEGMENTED_PROFILE, 504, purpose) for purpose in (
        'RESEARCH_PREPARATION', 'RESEARCH_ACCOUNT', 'RESEARCH_VERIFICATION', 'RESEARCH_REPORT')]
    base = {'resource_limits': units, 'stages': ['EXPLORATION'], 'max_batches': 2,
        'max_total_predictive_trials': 6, 'max_trials_per_batch': 3,
        'max_candidates_per_batch': 1, 'max_hypotheses_per_batch': 1,
        'expires_at': '2035-01-01T00:00:00+00:00', 'execution_profiles': profiles,
        'scope_policy': {'summary': {'stage_limits': {'EXPLORATION': deepcopy(units)}}}}
    delta = dict.fromkeys(RESOURCES, 0); delta['verification_jobs'] = 1
    grants = {}
    for name in ('SESSION_B001_REPORT_REPAIR_20261011', 'SESSION_B001_REPORT_RECOVERY_20261011'):
        grants[name] = {'grant': {'grant_id': name, 'base_authorization_hash': stable_hash(base),
            'resource_limits_delta': deepcopy(delta), 'stage_limits_delta': {'EXPLORATION': deepcopy(delta)},
            'max_batches_delta': 0, 'max_total_predictive_trials_delta': 0, 'expires_at': base['expires_at']}}
    auth = deepcopy(base); auth['resource_limits']['verification_jobs'] += 2
    auth['scope_policy']['summary']['stage_limits']['EXPLORATION']['verification_jobs'] += 2
    free = dict.fromkeys(RESOURCES, 0)
    free.update(candidate_attempts=1, data_experiments=1, account_jobs=2,
        verification_jobs=2, wall_seconds=144001)
    return {'base_authorization': base, 'authorization': auth, 'grants': grants,
        'operations': {'first': {'status': 'FAILED', 'active_segment': None, 'batch_id': 'batch_001'}},
        'remaining': free, 'stage_remaining': {'EXPLORATION': deepcopy(free)},
        'trial_usage': {'remaining_capacity': 3}, 'batch_count': 1, 'paused': False, 'revoked': False,
        'stages': {'EXPLORATION': {'status': 'ACTIVE'}}}


def test_v3_next_batch_preserves_two_exact_finite_grants_and_original_budget():
    module = helper('session_next_batch_setup_v3.py')
    state = view(); before = deepcopy(state)
    needed = module._capacity(SimpleNamespace(_require=require, NEXT_BATCH='batch_002'), state)
    assert needed['wall_seconds'] == 144001
    assert needed['account_jobs'] == 2 and needed['verification_jobs'] == 2
    assert state == before


@pytest.mark.parametrize('change', ['missing_grant', 'third_grant', 'wall_delta', 'stage_delta',
    'total_delta', 'active_segment', 'unknown', 'already_used', 'short_wall', 'short_verify'])
def test_v3_next_batch_rejects_unapproved_or_unsettled_capacity(change):
    module = helper('session_next_batch_setup_v3.py')
    state = view()
    if change == 'missing_grant':
        state['grants'].pop('SESSION_B001_REPORT_RECOVERY_20261011')
    elif change == 'third_grant':
        state['grants']['OTHER_GRANT'] = deepcopy(next(iter(state['grants'].values())))
    elif change == 'wall_delta':
        state['grants']['SESSION_B001_REPORT_RECOVERY_20261011']['grant']['resource_limits_delta']['wall_seconds'] = 1
    elif change == 'stage_delta':
        state['authorization']['scope_policy']['summary']['stage_limits']['EXPLORATION']['account_jobs'] += 1
    elif change == 'total_delta':
        state['authorization']['resource_limits']['account_jobs'] += 1
    elif change == 'active_segment':
        state['operations']['first']['active_segment'] = {'number': 1}
    elif change == 'unknown':
        state['operations']['first']['status'] = 'UNKNOWN'
    elif change == 'already_used':
        state['operations']['second'] = {'batch_id': 'batch_002', 'status': 'COMPLETED'}
    elif change == 'short_wall':
        state['remaining']['wall_seconds'] = 144000
    else:
        state['stage_remaining']['EXPLORATION']['verification_jobs'] = 1
    with pytest.raises(PermissionError):
        module._capacity(SimpleNamespace(_require=require, NEXT_BATCH='batch_002'), state)


def human_value(module):
    return {'origin': 'CURRENT_USER_REPLY_TO_EXACT_FINITE_REPORT_RENDER_RECOVERY_PROPOSAL',
        'user_reply': '批准这一次有限恢复（推荐）', 'proposal': {'path': 'proposal', 'sha256': module.PROPOSAL_SHA},
        'parent_repair_id': module.PARENT_REPAIR, 'parent_manifest_sha256': module.PARENT_REF['sha256'],
        'campaign_id': module.CAMPAIGN, 'objective_id': module.OBJECTIVE, 'verification_jobs_delta': 1,
        'maximum_additional_report_recoveries': 1, 'effective_verification_jobs_from': 5,
        'effective_verification_jobs_to': 6, 'recovery_total_seconds': 28800,
        'worker_seconds': 900, 'memory_mib': 2048, 'campaign_total_wall_seconds': 288002,
        'expires_at': '2026-10-17T19:43:08.066473+08:00', 'account_replay_permitted': False,
        'strategy_threshold_changes_permitted': False}


@pytest.mark.parametrize('invalid', [None, 'proposal_sha', 'human_sha', 'reply', 'delta', 'accounts', 'memory'])
def test_setup_requires_exact_current_human_scope(monkeypatch, invalid):
    module = helper('session_report_recovery_setup_v2.py')
    value = human_value(module)
    def reference(path):
        if path.name.endswith('.md'):
            return {'path': 'proposal', 'sha256': '0' * 64 if invalid == 'proposal_sha' else module.PROPOSAL_SHA}
        return {'path': 'human', 'sha256': '0' * 64 if invalid == 'human_sha' else module.HUMAN_SHA}
    if invalid == 'reply': value['user_reply'] = '是的'
    elif invalid == 'delta': value['verification_jobs_delta'] = 2
    elif invalid == 'accounts': value['account_replay_permitted'] = True
    elif invalid == 'memory': value['memory_mib'] = 4096
    monkeypatch.setattr(module, 'ref', reference)
    monkeypatch.setattr(module, 'read', lambda _: value)
    if invalid is None:
        proposal, human = module.human_scope()
        assert proposal['sha256'] == module.PROPOSAL_SHA and human['sha256'] == module.HUMAN_SHA
    else:
        with pytest.raises(PermissionError): module.human_scope()


def test_stop_resume_reuses_owner_approved_request_after_setup_write_interruption(monkeypatch, tmp_path):
    from chanlun_trader.research_factory.report_repair_stop_v2 import ReportRepairStopV2
    module = helper('session_report_recovery_setup_v2.py')
    parent = tmp_path / 'parent' / 'MANIFEST.json'
    parent.parent.mkdir()
    request = parent.parent / 'STOP_REQUEST_V2.json'
    request.write_text('{}', encoding='utf8')
    parent_ref = {'path': str(parent), 'sha256': '1' * 64}
    proposal, human = {'path': 'proposal'}, {'path': 'human'}
    summary = {'parent_manifest_ref': parent_ref, 'proposal_ref': proposal,
               'human_confirmation_ref': human, 'control_event': {'recorded_at': 'fixed'}}
    approval = {'path': 'original-owner-approval'}
    reference = {'path': str(request), 'sha256': '2' * 64}
    calls = []
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'OUTPUT', tmp_path / 'owner')
    monkeypatch.setattr(module, 'PARENT_REF', parent_ref)
    monkeypatch.setattr(module, 'base_context', lambda: None)
    monkeypatch.setattr(module, 'human_scope', lambda: (proposal, human))
    monkeypatch.setattr(module, 'ref', lambda path: reference)
    monkeypatch.setattr(module, 'read', lambda _: {'summary': summary, 'approval_ref': approval})
    monkeypatch.setattr(module, 'stop_summary', lambda: pytest.fail('must not regenerate paused summary'))
    monkeypatch.setattr(module, 'owner_approvals', lambda: pytest.fail('must not create another approval'))
    monkeypatch.setattr(ReportRepairStopV2, 'request', lambda root, value, *, approval_ref:
        calls.append((root, value, approval_ref)) or reference)
    assert module.request_stop()['stop_ref'] == reference
    assert calls == [(tmp_path, summary, approval)]
    assert (module.OUTPUT / 'STOP_SETUP.json').exists()


def test_create_installs_exact_owner_bridge_before_live_campaign_capability_validation(monkeypatch, tmp_path):
    from chanlun_trader.research_factory.report_recovery_protocol_v2 import ReportRecoveryV2
    from chanlun_trader.research_factory.report_recovery_capability_bridge_v2 import ReportRecoveryCapabilityBridgeV2
    module = helper('session_report_recovery_setup_v2.py')
    events = []
    def peek():
        assert events == ['bridge-installed']
        events.append('campaign-peek')
        return {'grants': {module.OLD_GRANT_ID: {}}}
    intent = {'capability_bridge': {'schema_version': 'synthetic'}, 'parent_terminal_ref': {},
        'grant': {}, 'source_replacements': [], 'runtime_sources': [], 'grant_summary': {}}
    fixed = SimpleNamespace(verify_owner_boundary=lambda: None)
    monkeypatch.setattr(module, 'preparation', lambda: (fixed, SimpleNamespace(peek_status=peek), {}, intent))
    monkeypatch.setattr(module, 'owner_approvals', lambda: (
        SimpleNamespace(approve=lambda *args, **kwargs: {'path': 'approval'}), object()))
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'OUTPUT', tmp_path / 'owner')
    monkeypatch.setattr(module, 'ref', lambda path: {'path': str(path), 'sha256': '3' * 64})
    monkeypatch.setattr(ReportRecoveryCapabilityBridgeV2, 'install', lambda *args, **kwargs:
        events.append('bridge-installed') or {'path': 'bridge'})
    monkeypatch.setattr(ReportRecoveryV2, 'summary', lambda *args, **kwargs: {})
    monkeypatch.setattr(ReportRecoveryV2, 'freeze', lambda *args, **kwargs:
        {'path': str(tmp_path / 'MANIFEST.json'), 'sha256': '4' * 64})
    assert module.create()['status'] == 'REPORT_RECOVERY_SETUP_READY_NO_DISPATCH'
    assert events == ['bridge-installed', 'campaign-peek']


@pytest.mark.parametrize('changed', [False, True])
def test_stop_cli_request_reads_pinned_summary_before_owner_dispatch(monkeypatch, tmp_path, capsys, changed):
    import hashlib
    import json
    from scripts import run_report_repair_stop_v2 as cli
    from chanlun_trader.research_factory.report_repair_stop_v2 import ReportRepairStopV2
    summary = {'schema_version': 'SYNTHETIC_STOP_SUMMARY'}
    path = tmp_path / 'SUMMARY.json'
    path.write_text(json.dumps(summary), encoding='utf-8')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if changed:
        path.write_text('{}', encoding='utf-8')
    calls = []
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    monkeypatch.setattr(ReportRepairStopV2, 'request', lambda root, value, *, approval_ref:
        calls.append((root, value, approval_ref)) or {'path': 'synthetic-stop-request'})
    args = ['request', '--reference', str(path), '--sha256', digest,
            '--approval-id', 'SYNTHETIC_APPROVAL', '--summary-hash', '1' * 64]
    if changed:
        with pytest.raises(ValueError, match='REPORT_REPAIR_STOP_SOURCE_CHANGED'):
            cli.main(args)
        assert calls == []
    else:
        assert cli.main(args) == 0
        assert calls == [(tmp_path, summary, {
            'approval_id': 'SYNTHETIC_APPROVAL', 'summary_hash': '1' * 64})]
        assert json.loads(capsys.readouterr().out) == {'path': 'synthetic-stop-request'}
