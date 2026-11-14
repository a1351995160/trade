"""同根已提交日的治理换源契约；人工账户原件，不启动 worker 或读取真实价格。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory import governance_source_revision_v1 as revision
from chanlun_trader.research_factory import failed_account_recovery_v1 as recovery_module
from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.universe_execution_artifacts_v1 import DayArtifacts
from test_account_purpose_time_extension_v1 import extension_case, control, pin, write
from test_failed_account_recovery_capability_v1 import recovery_case, append_checkpoint_days
from test_failed_account_recovery_governance_v1 import ready


def charged(case, name, outcome):
    version = AccountVersionContinuationV1.from_job(case['job'])
    dispatch = case['gov'].start_segment(name)
    version.record_dispatch(name, dispatch)
    reference = write(case['account'] / (name + '_SEGMENT_' + str(dispatch['segment_number']).zfill(6) + '_RESOURCE.json'), {
        'fixture_only': 'ARTIFICIAL_CONTRACT_NOT_WORKER_MEASUREMENT',
        'dispatch_id': dispatch['dispatch_id'], 'dispatch_identity': dispatch['dispatch_identity'],
        'elapsed_wall_seconds': .2, 'time_extension_proof': case['extension'].proof(),
        'failed_account_recovery_proof': case['recovery'].proof(), 'account_version_continuation': version.proof(name)})
    case['gov'].end_segment(name, dispatch['segment_number'], seconds=.2, evidence_identity=reference['sha256'],
        outcome=outcome, dispatch_identity=dispatch['dispatch_identity'])
    return dispatch


def local_benchmark(case, checkout, output, *, revised):
    script = Path(__file__).resolve().parents[1] / 'benchmarks/compare_checkpoint_revision_read_v1.py'
    args = [sys.executable, '-X', 'utf8', '-B', str(script), '--artificial-root', str(case['root']),
        '--output', str(output), '--label', 'CANDIDATE_WITH_REVISION' if revised else 'DFEC_PREDECESSOR']
    if revised:
        args.append('--expect-revision')
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(checkout / 'src'), str(checkout))),
        'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'}
    completed = subprocess.run(args, env=env, cwd=checkout, capture_output=True, text=True, encoding='utf-8', timeout=180)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(output.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def revised_case(_research_short_tmp_root, request):
    # 复用原CI工件定位的会话短根；每个模块独占，保留全部夹具操作／断言／预算。
    fixture_identity = request.module.__name__ + getattr(request, 'param', '')
    folder = _research_short_tmp_root / ('g' + hashlib.sha256(fixture_identity.encode()).hexdigest()[:6])
    folder.mkdir(exist_ok=False)
    with pytest.MonkeyPatch.context() as patch:
        case = extension_case.__wrapped__(folder, patch, SimpleNamespace(param={}))
        case = recovery_case.__wrapped__(case)
        # 原通用夹具只列最小恢复运行闭包。本用例在首次 Owner 批准前，
        # 显式把四个待替换读取器的原字节纳入人工 CI 和恢复来源，不放宽产品白名单。
        spec = case['recovery_spec']
        runtime = {row['path']: row for row in spec['runtime_source_refs']}
        for relative in sorted(revision.READER_PATHS):
            reference = pin(case['root'] / relative)
            runtime.setdefault(reference['path'], reference)
        spec['runtime_source_refs'] = sorted(runtime.values(), key=lambda row: row['path'])
        prior_ci = json.loads(Path(spec['ci_ref']['path']).read_text(encoding='utf-8'))
        prior_ci['runtime_sources'] = spec['runtime_source_refs']
        spec['ci_ref'] = write(case['root'] / 'reports' / 'revision_predecessor_ci.json', prior_ci)
        ready(case)
        append_checkpoint_days(case, 504)
        charged(case, 'ARTIFICIAL_BASE', 'COMPLETED')
        seconds = case['gov'].segment_status('ARTIFICIAL_BASE')['charged_seconds']
        case['gov'].settle_recovery('ARTIFICIAL_BASE', completed=True, seconds=seconds, result_hash='artificial_result')
        name = 'ARTIFICIAL_STRESS'
        case['gov'].start(name)
        template = json.loads(Path(case['job']['items']['ARTIFICIAL_BASE']['backend_options']['checkpoint_path']).read_text(encoding='utf-8'))
        store = DayArtifacts(case['account'] / (name + '_DAYS'), identity=stable_hash('revision-artificial-stress'))
        day = case['job']['plans'][name]['backend']['window']['calendar'][60]
        store.commit(day, {'date': day, 'fixture_only': 'ARTIFICIAL_CONTRACT_NOT_ECONOMIC_EXECUTION'})
        template.update(execution_identity=store.identity, artifacts=store.manifest(), last_day=day)
        template['state_identity'] = stable_hash({k: v for k, v in template.items() if k != 'state_identity'})
        write(Path(case['job']['items'][name]['backend_options']['checkpoint_path']), template)
        charged(case, name, 'PAUSED')
        control(case, 'PAUSE')
        context = AccountVersionContinuationV1.from_job(case['job'])
        folder = case['root'] / 'reports' / 'revision-stage'
        folder.mkdir()
        rows = []
        checkout = Path(__file__).resolve().parents[2]
        for relative in sorted(revision.READER_PATHS):
            source = case['root'] / relative
            archive = folder / ('old_' + source.name); archive.write_bytes(source.read_bytes())
            candidate = folder / ('candidate_' + source.name); candidate.write_bytes((checkout / relative).read_bytes())
            rows.append({'path': str(source), 'old_sha256': pin(source)['sha256'], 'new_sha256': pin(candidate)['sha256'],
                'archive_ref': pin(archive), 'candidate_ref': pin(candidate)})
        module = folder / 'module.py'; module.write_bytes((checkout / revision.MODULE_PATH).read_bytes())
        added = {'path': str(case['root'] / revision.MODULE_PATH), 'sha256': pin(module)['sha256']}
        evidence = write(folder / 'verification.json', {'status': 'PASSED', 'source_replacements': rows,
            'added_runtime_ref': added, 'fixture_only': 'ARTIFICIAL_VALIDATION_NOT_PRODUCTION_GATE'})
        human = write(folder / 'human.json', {'schema_version': 'HUMAN_GOVERNANCE_SOURCE_REVISION_AUTHORIZATION_V1',
            'root': str(case['root']), 'task_ref': case['task_ref'], 'recovery_ref': context.recovery.scope_reference,
            'allow_checkpoint_continuation': True, 'allow_budget_or_deadline_change': False})
        summary = revision.GovernanceSourceRevisionV1.prepare(context, replacements=rows, added_runtime_ref=added,
            verification_ref=evidence, human_authorization_ref=human)
        approval = case['approvals'].approve(summary, approver='artificial_owner', capability=case['token'])
        # 可选本机只读计时；不替代下面必跑的协议、fresh 与原件保持断言。
        baseline_checkout = os.environ.get('GOVERNANCE_REVISION_BASELINE_CHECKOUT')
        if baseline_checkout:
            local_benchmark(case, Path(baseline_checkout), case['root'].parent / 'BASELINE_DFEC_READ.json', revised=False)
        preserved = {p: p.read_bytes() for p in case['account'].rglob('*') if p.is_file() and '.lock' not in p.name}
        reference = revision.GovernanceSourceRevisionV1.install(context, summary, approval, added_runtime_candidate_ref=pin(module))
        if baseline_checkout:
            local_benchmark(case, Path(__file__).resolve().parents[2], case['root'].parent / 'CANDIDATE_REVISION_READ.json', revised=True)
        case.update(revision_summary=summary, revision_ref=reference, revision_approval=approval,
            preserved=preserved, revision_module_candidate=pin(module), revision_source=Path(rows[0]['path']))
        yield case


def test_install_preserves_all_committed_days_old_fees_plans_and_budget(revised_case):
    case = revised_case
    assert all(path.read_bytes() == data for path, data in case['preserved'].items())
    context = AccountVersionContinuationV1.from_job(case['job'])
    revised = context.recovery.governance_revision
    assert revised.reference == case['revision_ref']
    assert len(revised.mapping) == 4
    for row in revised.mapping.values():
        assert pin(Path(row['path']))['sha256'] == row['candidate_ref']['sha256']
    assert pin(case['root'] / revision.MODULE_PATH)['sha256'] == case['revision_module_candidate']['sha256']
    assert revised.summary['accounts']['ARTIFICIAL_BASE']['days'] == 504
    assert revised.summary['accounts']['ARTIFICIAL_STRESS']['days'] == 1
    assert context.active_plan('ARTIFICIAL_BASE') == case['recovery'].successor_plan('ARTIFICIAL_BASE')
    assert context.active_plan('ARTIFICIAL_STRESS') == case['recovery'].successor_plan('ARTIFICIAL_STRESS')
    assert context.recovery.proof() == case['recovery'].proof()
    assert revised.segment_proof('ARTIFICIAL_STRESS', 1) is None
    assert revised.segment_proof('ARTIFICIAL_STRESS', 2)['first_segment_number'] == 2
    with pytest.raises(PermissionError, match='COMPLETED_ACCOUNT_NO_DISPATCH'):
        revised.segment_proof('ARTIFICIAL_BASE', 52)
    assert not (case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000052_DISPATCH.json').exists()


def test_reader_missing_from_original_runtime_inventory_is_rejected(revised_case):
    case = revised_case
    recovery = deepcopy(case['recovery'].summary_value)
    target = str(case['root'] / 'src/chanlun_trader/research_factory/campaign_scope_v1.py')
    recovery['runtime_sources'] = [row for row in recovery['runtime_sources'] if row['path'] != target]
    with pytest.raises(PermissionError, match='SOURCE_ALLOWLIST_CONFLICT'):
        revision.GovernanceSourceRevisionV1._summary_binding(case['root'], case['revision_summary'],
            case['recovery'].scope_reference, recovery, case['job'], case['recovery'].execution_expires_at)


def read_revision(case, *, for_dispatch=False):
    original = case['recovery']
    return revision.GovernanceSourceRevisionV1.read(case['root'], original.scope_reference,
        original.summary_value, case['job'], original.execution_expires_at, for_dispatch=for_dispatch)


@pytest.mark.parametrize('target', ('owner', 'source', 'archive', 'module', 'human', 'validation', 'ledger', 'checkpoint', 'marker', 'slot'))
def test_every_new_call_rejects_changed_boundary(revised_case, target):
    case = revised_case; summary = case['revision_summary']
    path = (case['approvals'].directory / (case['revision_approval']['approval_id'] + '.json') if target == 'owner'
        else case['revision_source'] if target == 'source'
        else Path(summary['source_replacements'][0]['archive_ref']['path']) if target == 'archive'
        else Path(summary['added_runtime_ref']['path']) if target == 'module'
        else Path(summary['human_authorization_ref']['path']) if target == 'human'
        else Path(summary['verification_ref']['path']) if target == 'validation'
        else Path(summary['ledger']['path']) if target == 'ledger'
        else revision._legacy_marker(case['root'], case['recovery'].summary_value) if target == 'marker'
        else revision._paths(case['root'], case['recovery'].summary_value)[1] if target == 'slot'
        else Path(case['job']['items']['ARTIFICIAL_STRESS']['backend_options']['checkpoint_path']))
    raw = path.read_bytes()
    try:
        if target == 'checkpoint':
            value = json.loads(raw); value['artifacts']['days'] = []
            value['artifacts']['manifest_identity'] = stable_hash({k: v for k, v in value['artifacts'].items() if k != 'manifest_identity'})
            value['state_identity'] = stable_hash({k: v for k, v in value.items() if k != 'state_identity'})
            path.write_text(json.dumps(value), encoding='utf-8')
        elif target == 'ledger':
            path.write_bytes(raw.replace(b'CAMPAIGN', b'CHANGED', 1))
        elif target in ('marker', 'slot'):
            path.write_text('{}', encoding='utf-8')
        else:
            path.write_bytes(raw + b'\n') if target != 'owner' else path.unlink()
        with pytest.raises((PermissionError, ValueError)):
            read_revision(case)
    finally:
        path.write_bytes(raw)


@pytest.mark.parametrize('field,value', (('grant_delta', 1), ('new_account_delta', 1), ('maximum_revisions', 2),
    ('expires_at', '2099-01-01T00:00:00+00:00'), ('global_wall_seconds', 999999)))
def test_owner_signature_does_not_widen_protocol(revised_case, field, value):
    case = revised_case
    summary = deepcopy(case['revision_summary']); summary[field] = value
    summary['identity'] = stable_hash({k: v for k, v in summary.items() if k != 'identity'})
    with pytest.raises(PermissionError, match='SUMMARY_BINDING_CONFLICT'):
        revision.GovernanceSourceRevisionV1._summary_binding(case['root'], summary, case['recovery'].scope_reference,
            case['recovery'].summary_value, case['job'], case['recovery'].execution_expires_at)


@pytest.mark.parametrize('field,value', (('segment_count', 20), ('charged_seconds', 0), ('completed', True)))
def test_cutover_count_and_fees_must_match_the_frozen_parent_prefix(revised_case, field, value):
    case = revised_case
    summary = deepcopy(case['revision_summary'])
    summary['accounts']['ARTIFICIAL_STRESS'][field] = value
    context = revision.GovernanceSourceRevisionV1(case['root'], case['revision_ref'], summary)
    with pytest.raises(PermissionError, match='FEE_BOUNDARY_CONFLICT'):
        context.validate_boundary(case['job'])


def test_original_unique_manifest_and_slot_rules_reject_rollback(revised_case):
    case = revised_case
    folder = case['account'].parent / 'failed-account-recoveries'
    manifests = list(folder.glob('*/MANIFEST.json'))
    slots = list(revision._legacy_marker(case['root'], case['recovery'].summary_value).parent.glob('*.json'))
    assert len(manifests) == len(slots) == 2
    # 原版本读取器的发现契约必须失败；不能把新槽当作旧协议再切换一次。
    assert len(manifests) != 1 and len(slots) != 1


def test_new_owner_revoked_during_outer_read_is_rejected_at_return(revised_case, monkeypatch):
    case = revised_case
    owner = case['approvals'].directory / (case['revision_approval']['approval_id'] + '.json')
    raw = owner.read_bytes()
    original_sources = recovery_module._sources
    def revoke_after_sources(*args, **kwargs):
        result = original_sources(*args, **kwargs)
        owner.unlink()
        return result
    try:
        monkeypatch.setattr(recovery_module, '_sources', revoke_after_sources)
        with pytest.raises(PermissionError, match='OWNER_APPROVAL_NOT_REGISTERED'):
            recovery_module.FailedAccountRecoveryV1.read(case['root'], case['recovery'].scope_reference)
    finally:
        owner.write_bytes(raw)


def test_new_dispatch_expiry_after_outer_sources_is_rejected(revised_case, monkeypatch):
    case = revised_case
    original_sources = recovery_module._sources
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(timezone.utc) + timedelta(days=20)
    def expire_after_sources(*args, **kwargs):
        result = original_sources(*args, **kwargs)
        monkeypatch.setattr(revision, 'datetime', Later)
        return result
    monkeypatch.setattr(recovery_module, '_sources', expire_after_sources)
    with pytest.raises(PermissionError, match='EXPIRED'):
        recovery_module.FailedAccountRecoveryV1.read(case['root'], case['recovery'].scope_reference, for_dispatch=True)


def test_source_writer_checks_opened_object_before_truncating(tmp_path, monkeypatch):
    target = tmp_path / 'target.py'; other = tmp_path / 'other.py'
    target.write_bytes(b'original target'); other.write_bytes(b'other original')
    original_open = os.open
    monkeypatch.setattr(os, 'open', lambda path, flags, mode=0o777: original_open(other, flags, mode))
    with pytest.raises(PermissionError, match='INSTALL_OBJECT_CHANGED'):
        revision._install_bytes(target, b'new', hashlib.sha256(b'new').hexdigest(), tmp_path)
    assert target.read_bytes() == b'original target' and other.read_bytes() == b'other original'


def test_source_writer_keeps_exact_new_bytes_and_rejects_wrong_hash(tmp_path):
    target = tmp_path / 'target.py'; target.write_bytes(b'long old contents')
    with pytest.raises(PermissionError, match='INSTALL_BYTES_CHANGED'):
        revision._install_bytes(target, b'new', '0' * 64, tmp_path)
    assert target.read_bytes() == b'long old contents'
    revision._install_bytes(target, b'new', hashlib.sha256(b'new').hexdigest(), tmp_path)
    assert target.read_bytes() == b'new'


def test_no_second_install_or_dispatch_when_expired(revised_case, monkeypatch):
    case = revised_case; context = AccountVersionContinuationV1.from_job(case['job'])
    with pytest.raises(PermissionError, match='ALREADY_INSTALLED'):
        revision.GovernanceSourceRevisionV1.install(context, case['revision_summary'], case['revision_approval'],
            added_runtime_candidate_ref=case['revision_module_candidate'])
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(timezone.utc) + timedelta(days=20)
    with monkeypatch.context() as patch:
        patch.setattr(revision, 'datetime', Later)
        with pytest.raises(PermissionError, match='EXPIRED'):
            revision.GovernanceSourceRevisionV1.read(case['root'], context.recovery.scope_reference,
                context.recovery.summary_value, case['job'], context.recovery.execution_expires_at, for_dispatch=True)


def test_next_original_segment_binds_revision_and_preserves_prior_version_records(revised_case):
    case = revised_case
    control(case, 'RESUME')
    context = AccountVersionContinuationV1.from_job(case['job'])
    dispatch = case['gov'].start_segment('ARTIFICIAL_STRESS')
    assert dispatch['segment_number'] == 2
    record = context.record_dispatch('ARTIFICIAL_STRESS', dispatch)
    assert record['governance_source_revision']['manifest_ref'] == case['revision_ref']
    assert context.validate_dispatch('ARTIFICIAL_STRESS', 2) == record
    path = context._dispatch_path('ARTIFICIAL_STRESS', 2)
    raw = path.read_bytes(); bad = deepcopy(record); bad.pop('governance_source_revision')
    try:
        path.write_text(json.dumps(bad), encoding='utf-8')
        with pytest.raises(PermissionError, match='DISPATCH_VERSION_CONFLICT'):
            context.validate_dispatch('ARTIFICIAL_STRESS', 2)
    finally:
        path.write_bytes(raw)
    assert all(path.read_bytes() == data for path, data in case['preserved'].items())
    reference = write(case['account'] / 'ARTIFICIAL_STRESS_SEGMENT_000002_RESOURCE.json', {
        'fixture_only': 'ARTIFICIAL_CONTRACT_NOT_WORKER_MEASUREMENT',
        'dispatch_id': dispatch['dispatch_id'], 'dispatch_identity': dispatch['dispatch_identity'],
        'elapsed_wall_seconds': .3, 'time_extension_proof': case['extension'].proof(),
        'failed_account_recovery_proof': case['recovery'].proof(), 'account_version_continuation': context.proof('ARTIFICIAL_STRESS')})
    case['gov'].end_segment('ARTIFICIAL_STRESS', 2, seconds=.3, evidence_identity=reference['sha256'],
        outcome='PAUSED', dispatch_identity=dispatch['dispatch_identity'])
    state = case['gov'].segment_status('ARTIFICIAL_STRESS')
    assert state['pending'] is None and state['segments'][-1]['charge']['evidence_identity'] == reference['sha256']
    operation = case['campaign']._peek_execution_status()['operations']['STRESS']
    context._parent_prefix(state, operation, 0)
    assert len(operation['segments']) == 2
    assert not (case['account'] / 'ARTIFICIAL_STRESS_SEGMENT_000003_DISPATCH.json').exists()


def test_expired_predecessor_can_only_pause_with_fresh_approval_and_settled_history(revised_case, monkeypatch):
    from chanlun_trader.research_factory import governance_source_continuation_v2 as second
    from chanlun_trader.research_factory import account_version_continuation_v1 as versions
    from chanlun_trader.research_factory import account_purpose_time_extension_v1 as extension
    from chanlun_trader.research_factory import research_campaign_v1 as campaigns
    from chanlun_trader.research_factory import etf_account_governance_v1 as governance
    from chanlun_trader.research_factory.budget import BudgetExhaustedError
    from scripts import run_strategy_account_v1 as runner
    case = revised_case
    context = AccountVersionContinuationV1.from_job(case['job'])
    control(case, 'RESUME')
    control_before = runner.control_state(case['job'])
    assert not control_before['paused']
    counts = sum(len(case['gov'].segment_status(name)['segments']) for name in case['job']['plans'])
    days = sum(len(json.loads(Path(case['job']['items'][name]['backend_options']['checkpoint_path']).read_text(
        encoding='utf-8'))['artifacts']['days']) for name in case['job']['plans'])
    value = {'schema_version':'HUMAN_ARTIFICIAL_SECOND_GOVERNANCE_CONTINUATION_AUTHORIZATION_V1',
        'root':str(case['root']),'job_path':str(case['account']/'JOB.json'),'predecessor_revision_ref':case['revision_ref'],
        'artificial_only':True,'allow_protocol_implementation_and_validation':True,'additional_governance_revisions':1,
        'completed_days_to_preserve':days,'settled_segments_to_preserve':counts,'execution_window_seconds':259200,
        'execution_window_starts':'ACTUAL_SCOPED_OWNER_ISSUANCE','each_account_seconds':72000,'global_seconds':200000,
        'allow_budget_increase':False,'allow_new_task_candidate_purpose_grant_or_root':False,
        'allow_economic_source_or_input_change':False,'automatic_deadline_extension':False,
        'real_account_actions_allowed':False,'all_fresh_assertions_required':True}
    human = write(case['root']/'reports/expired_pause_human.json', value)
    old_deadline = datetime.fromisoformat(context.recovery.execution_expires_at)
    class Expired(datetime):
        @classmethod
        def now(cls, tz=None):
            when = old_deadline+timedelta(seconds=1)
            return when.astimezone(tz) if tz else when.replace(tzinfo=None)
    for module in (second, revision, recovery_module, versions, extension, campaigns, governance, runner):
        monkeypatch.setattr(module, 'datetime', Expired)
    before = {p:p.read_bytes() for p in case['account'].rglob('*') if p.is_file() and '.lock' not in p.name}
    ledger = case['campaign'].budget_path.with_name('run_budget_events.jsonl');ledger_before=ledger.read_bytes()
    # 原PAUSE所用active鉴权因到期拒绝；新增入口只暂停已结清历史。
    # 通用治理夹具不冒充完整策略执行器的benchmark／loader装配。
    with pytest.raises((PermissionError,BudgetExhaustedError), match='EXPIRED'):
        case['gov'].active()
    assert runner.control_state(case['job']) == control_before
    bad = deepcopy(value);bad['real_account_actions_allowed']=True
    bad_ref = write(case['root']/'reports/invalid_pause_human.json', bad)
    with pytest.raises(PermissionError, match='HUMAN_SCOPE_CONFLICT'):
        second.GovernanceSourceContinuationV2.pause_expired_predecessor(context,human_authorization_ref=bad_ref)
    assert runner.control_state(case['job']) == control_before
    owner = case['approvals'].directory/(case['revision_approval']['approval_id']+'.json')
    owner_bytes = owner.read_bytes()
    try:
        owner.unlink()
        with pytest.raises(PermissionError, match='OWNER_APPROVAL_NOT_REGISTERED'):
            second.GovernanceSourceContinuationV2.pause_expired_predecessor(context,human_authorization_ref=human)
        assert runner.control_state(case['job']) == control_before
    finally:
        owner.write_bytes(owner_bytes)
    source = case['revision_source'];source_bytes=source.read_bytes()
    try:
        source.write_bytes(source_bytes+b'\n')
        with pytest.raises(PermissionError):
            second.GovernanceSourceContinuationV2.pause_expired_predecessor(context,human_authorization_ref=human)
        assert runner.control_state(case['job']) == control_before
    finally:
        source.write_bytes(source_bytes)
    paused = second.GovernanceSourceContinuationV2.pause_expired_predecessor(context,human_authorization_ref=human)
    assert paused == runner.control_state(case['job']) and paused['paused']
    assert len(paused['events']) == len(control_before['events'])+1
    assert second.GovernanceSourceContinuationV2.pause_expired_predecessor(context,human_authorization_ref=human) == paused
    assert ledger.read_bytes() == ledger_before and all(p.read_bytes()==data for p,data in before.items())
    changed = {p for p in case['account'].rglob('*') if p.is_file() and '.lock' not in p.name}-set(before)
    assert changed == {case['account']/('CONTROL_'+str(len(paused['events'])).zfill(6)+'.json')}
    assert not (case['account']/'ARTIFICIAL_STRESS_SEGMENT_000003_DISPATCH.json').exists()
