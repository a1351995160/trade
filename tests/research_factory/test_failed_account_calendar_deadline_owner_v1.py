"""原日历期限接续只进入完整Owner摘要；历史期限、资源及费用保持。"""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chanlun_trader.research_factory import failed_account_recovery_v1 as recovery
from scripts import setup_failed_account_recovery_v1 as setup
from test_account_purpose_time_extension_v1 import extension_case, write
from test_failed_account_recovery_capability_v1 import recovery_case


OLD_EXPIRY = '2026-10-17T19:43:08.066473+08:00'
NEW_EXPIRY = '2026-10-20T19:43:08+08:00'


def human_authorization(authorization_id, objective_id, previous, expires):
    return {
        'schema_version': 'HUMAN_REAL_ACCOUNT_CALENDAR_DEADLINE_EXTENSION_AUTHORIZATION_V1',
        'campaign': authorization_id, 'objective': objective_id,
        'original_expires_at': previous, 'authorized_new_expires_at': expires,
        'each_account_seconds': 72000, 'global_seconds': 288002,
        'original_base_days': 277, 'original_stress_days': 0,
        'canonical_original_charged_seconds': recovery.CANONICAL_SECONDS,
        'parent_original_used_integer_seconds': 42340,
        'base_remaining_integer_seconds': 29660, 'stress_remaining_seconds': 72000,
        'preserve_original_FAILED_and_all_fees': True,
        'new_task_candidate_purpose_grant_resource_or_owner_root_allowed': False,
        'rerun_committed_days_allowed': False, 'all_original_production_gates_required': True,
    }


@pytest.fixture
def calendar_request(tmp_path):
    human = human_authorization('original_campaign', 'original_objective', OLD_EXPIRY, NEW_EXPIRY)
    reference = write(tmp_path / 'reports' / 'human_calendar_authorization.json', human)
    value = {'previous_expires_at': OLD_EXPIRY, 'expires_at': NEW_EXPIRY,
             'human_authorization_ref': reference}
    expected = {'authorization_id': 'original_campaign', 'objective_id': 'original_objective',
                'previous_expires_at': OLD_EXPIRY, 'global_wall_seconds': 288002}
    return tmp_path, value, human, expected


def test_calendar_human_reference_is_fresh_and_content_pinned(calendar_request):
    root, value, _, expected = calendar_request
    assert recovery._validate_calendar_deadline_extension(root, value, **expected) == value
    Path(value['human_authorization_ref']['path']).write_bytes(b'{}\n')
    with pytest.raises((ValueError, PermissionError), match='REFERENCE_CHANGED'):
        recovery._validate_calendar_deadline_extension(root, value, **expected)


@pytest.mark.parametrize('field,wrong', [
    ('schema_version', 'OTHER_AUTHORIZATION'), ('campaign', 'another_campaign'),
    ('objective', 'another_objective'), ('original_expires_at', NEW_EXPIRY),
    ('authorized_new_expires_at', OLD_EXPIRY), ('each_account_seconds', 72001),
    ('global_seconds', 288003), ('original_base_days', 276), ('original_stress_days', 1),
    ('original_stress_days', False),
    ('canonical_original_charged_seconds', recovery.CANONICAL_SECONDS + 1),
    ('parent_original_used_integer_seconds', 42339), ('base_remaining_integer_seconds', 29661),
    ('stress_remaining_seconds', 72001), ('preserve_original_FAILED_and_all_fees', False),
    ('preserve_original_FAILED_and_all_fees', 1),
    ('new_task_candidate_purpose_grant_resource_or_owner_root_allowed', True),
    ('rerun_committed_days_allowed', True), ('all_original_production_gates_required', False),
])
def test_calendar_human_scope_cannot_change_original_identity_or_resources(calendar_request, field, wrong):
    root, value, human, expected = calendar_request
    human[field] = wrong
    value['human_authorization_ref'] = write(root / 'reports' / 'changed_human_authorization.json', human)
    with pytest.raises(PermissionError, match='CALENDAR'):
        recovery._validate_calendar_deadline_extension(root, value, **expected)


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'naive_old', 'naive_new', 'equal', 'earlier', 'unbounded'])
def test_calendar_request_requires_exact_finite_later_aware_deadline(calendar_request, mutation):
    root, value, _, expected = calendar_request
    if mutation == 'missing':
        value.pop('human_authorization_ref')
    elif mutation == 'extra':
        value['grant_delta'] = 1
    elif mutation == 'naive_old':
        value['previous_expires_at'] = '2026-10-17T19:43:08.066473'
    elif mutation == 'naive_new':
        value['expires_at'] = '2026-10-20T19:43:08'
    elif mutation == 'equal':
        value['expires_at'] = OLD_EXPIRY
    elif mutation == 'earlier':
        value['expires_at'] = '2026-10-16T19:43:08+08:00'
    else:
        value['expires_at'] = 'Infinity'
    with pytest.raises(PermissionError, match='CALENDAR'):
        recovery._validate_calendar_deadline_extension(root, value, **expected)


def test_calendar_human_reference_must_stay_under_reports(calendar_request):
    root, value, human, expected = calendar_request
    value['human_authorization_ref'] = write(root / 'unscoped_human_authorization.json', human)
    with pytest.raises((ValueError, PermissionError)):
        recovery._validate_calendar_deadline_extension(root, value, **expected)


def test_calendar_authorization_cannot_change_parent_global_limit(calendar_request):
    root, value, _, expected = calendar_request
    expected['global_wall_seconds'] += 1
    with pytest.raises(PermissionError, match='CALENDAR_ORIGINAL_LIMITS'):
        recovery._validate_calendar_deadline_extension(root, value, **expected)


@pytest.fixture(scope='module')
def calendar_owner_case(tmp_path_factory):
    """仅本模块共享一次完整合法装配；各公开read仍做真实fresh校验。"""
    with pytest.MonkeyPatch.context() as patch:
        case = extension_case.__wrapped__(tmp_path_factory.mktemp('calendar-owner'), patch,
            SimpleNamespace(param={'resource_wall': 288002}))
        case = recovery_case.__wrapped__(case)
        legacy = recovery.FailedAccountRecoveryV1.summary(case['root'], **case['recovery_spec'])
        previous = legacy['expires_at']
        expires = (datetime.fromisoformat(previous) + timedelta(hours=72)).isoformat()
        human = human_authorization(legacy['authorization_id'], legacy['objective_id'], previous, expires)
        human_ref = write(case['root'] / 'reports' / 'human_calendar_authorization.json', human)
        request = {'previous_expires_at': previous, 'expires_at': expires, 'human_authorization_ref': human_ref}
        spec = {**case['recovery_spec'], 'calendar_deadline_extension': request}
        unchanged = [case['campaign'].authorization_path, case['campaign'].budget_path,
            case['campaign'].budget_path.with_name('run_budget_events.jsonl'), case['gov'].budget_path,
            Path(case['task_ref']['path']), case['account'] / 'JOB.json',
            case['account'] / 'ARTIFICIAL_BASE_SETTLEMENT.json',
            Path(case['job']['items']['ARTIFICIAL_BASE']['backend_options']['checkpoint_path']),
            *case['account'].glob('ARTIFICIAL_BASE_SEGMENT_*_CHARGE.json')]
        before = {path: path.read_bytes() for path in unchanged}
        summary = recovery.FailedAccountRecoveryV1.summary(case['root'], **spec)
        with pytest.raises(PermissionError, match='OWNER_APPROVAL'):
            recovery.FailedAccountRecoveryV1.freeze(case['root'], summary, approval_ref={})
        assert not (Path(case['task_ref']['path']).parent / 'failed-account-recoveries').exists()
        approval = case['approvals'].approve(summary, approver='artificial_owner', capability=case['token'])
        reference = recovery.FailedAccountRecoveryV1.freeze(case['root'], summary, approval_ref=approval)
        case.update(calendar_spec=spec, calendar_summary=summary, calendar_reference=reference,
                    calendar_approval=approval, legacy_summary=legacy, calendar_unchanged=before)
        yield case


def test_calendar_owner_freeze_preserves_history_and_binds_public_plan(calendar_owner_case, monkeypatch):
    case = calendar_owner_case; summary = case['calendar_summary']; legacy = case['legacy_summary']
    assert {k: v for k, v in summary.items() if k not in ('calendar_deadline_extension', 'summary_identity')} == {
        k: v for k, v in legacy.items() if k != 'summary_identity'}
    monkeypatch.setattr(setup, 'ROOT', case['root'])
    assert setup.plan(case['calendar_spec']) == summary
    assert recovery.FailedAccountRecoveryV1.freeze(case['root'], summary,
        approval_ref=case['calendar_approval']) == case['calendar_reference']
    context = recovery.FailedAccountRecoveryV1.read(case['root'], case['calendar_reference'], for_install=True)
    assert context.execution_expires_at == summary['calendar_deadline_extension']['expires_at']
    assert context.summary_value['expires_at'] == legacy['expires_at']
    resolved = recovery.resolve_failed_account_recovery_manifest(case['calendar_reference'], root=case['root'],
        authorization_id=summary['authorization_id'], objective_id=summary['objective_id'])
    assert resolved['execution_expires_at'] == context.execution_expires_at
    assert resolved['calendar_deadline_extension'] == summary['calendar_deadline_extension']
    assert resolved['expires_at'] == legacy['expires_at']
    assert all(path.read_bytes() == raw for path, raw in case['calendar_unchanged'].items())


def test_calendar_owner_read_rechecks_human_original(calendar_owner_case):
    case = calendar_owner_case
    path = Path(case['calendar_summary']['calendar_deadline_extension']['human_authorization_ref']['path'])
    original = path.read_bytes()
    try:
        path.write_bytes(original + b'\n')
        with pytest.raises((ValueError, PermissionError), match='REFERENCE_CHANGED'):
            recovery.FailedAccountRecoveryV1.read(case['root'], case['calendar_reference'])
    finally:
        path.write_bytes(original)


def test_calendar_owner_read_recomputes_the_task_scope(calendar_owner_case, monkeypatch):
    from chanlun_trader.research_factory import failed_account_calendar_scope_v1 as calendar_scope

    case = calendar_owner_case
    monkeypatch.setattr(calendar_scope, 'calendar_task_scope', lambda *args: {'changed_task_scope': True})
    with pytest.raises(PermissionError, match='CALENDAR_TASK_SCOPE_CONFLICT'):
        recovery.FailedAccountRecoveryV1.read(case['root'], case['calendar_reference'])


def test_calendar_extension_is_bound_by_full_owner_approval(calendar_owner_case):
    case = calendar_owner_case; path = Path(case['calendar_reference']['path']); original = path.read_bytes()
    manifest = recovery._read(case['calendar_reference'], case['root'])
    summary = manifest['summary']
    summary['calendar_deadline_extension']['expires_at'] = (
        datetime.fromisoformat(summary['calendar_deadline_extension']['expires_at']) + timedelta(seconds=1)).isoformat()
    summary['summary_identity'] = recovery.stable_hash({k: v for k, v in summary.items() if k != 'summary_identity'})
    manifest['identity'] = recovery.stable_hash({k: v for k, v in manifest.items() if k != 'identity'})
    try:
        # 负例刻意篡改既有原件，不能由不可变写入器提前拦截。
        path.write_text(json.dumps(manifest, ensure_ascii=True) + '\n', encoding='utf-8')
        changed = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        with pytest.raises(PermissionError, match='OWNER_APPROVAL'):
            recovery.FailedAccountRecoveryV1.read(case['root'], changed)
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize('at_new_expiry', [False, True])
def test_calendar_install_uses_new_deadline_but_historical_read_keeps_old(calendar_owner_case, monkeypatch, at_new_expiry):
    case = calendar_owner_case; summary = case['calendar_summary']; extension = summary['calendar_deadline_extension']
    now = (datetime.fromisoformat(extension['expires_at']) if at_new_expiry
           else datetime.fromisoformat(summary['expires_at']) + timedelta(seconds=1))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)

    monkeypatch.setattr(recovery, 'datetime', Clock)
    if at_new_expiry:
        with pytest.raises(PermissionError, match='EXPIRED'):
            recovery.FailedAccountRecoveryV1.read(case['root'], case['calendar_reference'], for_install=True)
    else:
        assert recovery.FailedAccountRecoveryV1.read(case['root'], case['calendar_reference'],
            for_install=True).execution_expires_at == extension['expires_at']
    assert recovery.FailedAccountRecoveryV1.read(case['root'], case['calendar_reference']).summary_value['expires_at'] == summary['expires_at']


@pytest.mark.parametrize('extra', ['dispatch', 'execution_expires_at'])
def test_calendar_setup_still_rejects_other_execution_fields(extra):
    spec = dict.fromkeys(setup.SPEC_FIELDS)
    spec['calendar_deadline_extension'] = {'previous_expires_at': OLD_EXPIRY, 'expires_at': NEW_EXPIRY,
                                          'human_authorization_ref': {'path': 'unused', 'sha256': 'a' * 64}}
    spec[extra] = True
    with pytest.raises(ValueError, match='SPEC_FIELDS_INVALID'):
        setup.plan(spec)
