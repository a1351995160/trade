"""父子派发间推进时钟，保留真实来源、额度和账本核验；不启动经济worker。"""
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory import etf_account_governance_v1 as governance
from chanlun_trader.research_factory import research_campaign_v1 as campaigns
from chanlun_trader.research_factory import account_purpose_time_extension_v1 as extension
from chanlun_trader.research_factory import account_version_continuation_v1 as versions
from chanlun_trader.research_factory import account_version_capability_bridge_v1 as bridges
from chanlun_trader.research_factory import failed_account_recovery_v1 as recovery
from chanlun_trader.research_factory.budget import BudgetExhaustedError
from test_account_purpose_time_extension_v1 import extension_case, control, write
from test_account_purpose_time_budget_v1 import installed_case, new_resource
from test_failed_account_calendar_deadline_owner_v1 import calendar_owner_case
from test_universe_campaign_segment_mirror_v1 import account_campaign
from test_universe_execution_profile_v1 import governance as standalone_governance


def moving_clock(monkeypatch, expires, *, remaining=600):
    clock = {'now': datetime.fromisoformat(expires) - timedelta(seconds=remaining)}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = clock['now']
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

    for module in (governance, campaigns, extension, versions):
        monkeypatch.setattr(module, 'datetime', Clock)
    return clock, Clock


def advance_before_parent(monkeypatch, clock, seconds):
    original = campaigns.ResearchCampaignV1.start_execution_segment

    def delayed(self, *args, **kwargs):
        clock['now'] += timedelta(seconds=seconds)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(campaigns.ResearchCampaignV1, 'start_execution_segment', delayed)


def event_rows(campaign):
    path = campaign.budget_path.with_name('run_budget_events.jsonl')
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def test_campaign_parent_fresh_deadline_bound_is_mirrored_after_clock_advance(installed_case, monkeypatch):
    case = installed_case
    gov, campaign = case['gov'], case['campaign']
    gov.reconcile_segment_mirrors('ARTIFICIAL_BASE')
    receipt = json.loads(gov.receipt_path.read_text(encoding='utf-8'))
    clock, _ = moving_clock(monkeypatch, receipt['expires_at'])
    advance_before_parent(monkeypatch, clock, 10)
    before_rows = event_rows(campaign)

    dispatch = gov.start_segment('ARTIFICIAL_BASE')

    parent = campaign.peek_status()['operations']['BASE']['active_segment']
    assert parent['upper_bound_seconds'] == dispatch['upper_bound_seconds'] == 590
    assert parent['dispatched_at'] == dispatch['dispatched_at'] == clock['now'].isoformat()
    assert parent['profile_hash'] == dispatch['profile_hash']
    assert parent['time_extension_proof'] == dispatch['time_extension_proof'] == case['context'].proof()
    dispatched = [row for row in event_rows(campaign)[len(before_rows):]
                  if row['event_type'] == 'CAMPAIGN_EXECUTION_SEGMENT_DISPATCHED']
    assert len(dispatched) == 1 and dispatched[0]['payload'] == parent

    # 人工证据明确来自测试夹具；验证一次收费及镜像，不启动经济worker。
    evidence = new_resource(case, dispatch, 12.)
    charge = gov.end_segment('ARTIFICIAL_BASE', 35, seconds=12., evidence_identity=evidence['sha256'])
    before_reconcile = event_rows(campaign)
    state = gov.reconcile_segment_mirrors('ARTIFICIAL_BASE')
    assert state['segments'][-1]['charge'] == charge and state['pending'] is None
    assert event_rows(campaign) == before_reconcile
    charged = [row['payload'] for row in before_reconcile[len(before_rows):]
               if row['event_type'] == 'CAMPAIGN_EXECUTION_SEGMENT_CHARGED']
    assert len(charged) == 1 and charged[0]['segment_number'] == 35
    assert all(charged[0][key] == charge[key] for key in ('seconds', 'basis', 'measured_seconds',
        'evidence_identity', 'outcome', 'charged_at', 'time_extension_proof'))


@pytest.mark.parametrize('advance,error_type,error', [
    (530, PermissionError, 'ACCOUNT_PURPOSE_TIME_EXTENSION_COOPERATIVE_WINDOW_EXHAUSTED'),
    (600, BudgetExhaustedError, 'CAMPAIGN_EXPIRED'),
])
def test_parent_rechecks_shrinking_window_before_either_dispatch(installed_case, monkeypatch, advance, error_type, error):
    case = installed_case
    gov, campaign = case['gov'], case['campaign']
    gov.reconcile_segment_mirrors('ARTIFICIAL_BASE')
    receipt = json.loads(gov.receipt_path.read_text(encoding='utf-8'))
    clock, _ = moving_clock(monkeypatch, receipt['expires_at'])
    advance_before_parent(monkeypatch, clock, advance)
    ledger = campaign.budget_path.with_name('run_budget_events.jsonl')
    before = ledger.read_bytes()
    with pytest.raises(error_type, match=error):
        gov.start_segment('ARTIFICIAL_BASE')
    assert ledger.read_bytes() == before
    assert not (case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000035_DISPATCH.json').exists()


@pytest.mark.parametrize('change', ['owner', 'source'])
def test_parent_keeps_fresh_owner_and_source_rejection(installed_case, monkeypatch, change):
    case = installed_case
    gov, campaign = case['gov'], case['campaign']
    gov.reconcile_segment_mirrors('ARTIFICIAL_BASE')
    original = campaigns.ResearchCampaignV1.start_execution_segment
    called = []

    def invalidate(self, *args, **kwargs):
        called.append(True)
        if change == 'owner':
            approval = case['approvals'].directory / (case['context'].approval_ref['approval_id'] + '.json')
            approval.unlink()
        else:
            path = Path(case['root']) / bridges.BUSINESS_PATH
            path.write_bytes(path.read_bytes() + b'\n# artificial revoked source\n')
        return original(self, *args, **kwargs)

    monkeypatch.setattr(campaigns.ResearchCampaignV1, 'start_execution_segment', invalidate)
    ledger = campaign.budget_path.with_name('run_budget_events.jsonl')
    before = ledger.read_bytes()
    with pytest.raises((PermissionError, ValueError), match='OWNER_APPROVAL|SOURCE|REFERENCE_CHANGED'):
        gov.start_segment('ARTIFICIAL_BASE')
    assert called == [True] and ledger.read_bytes() == before
    assert not (case['account'] / 'ARTIFICIAL_BASE_SEGMENT_000035_DISPATCH.json').exists()


def test_explicit_parent_excess_request_still_rejected(installed_case, monkeypatch):
    case = installed_case
    receipt = json.loads(case['gov'].receipt_path.read_text(encoding='utf-8'))
    moving_clock(monkeypatch, receipt['expires_at'])
    ledger = case['campaign'].budget_path.with_name('run_budget_events.jsonl')
    before = ledger.read_bytes()
    with pytest.raises(PermissionError, match='CAMPAIGN_EXECUTION_SEGMENT_BOUND_CONFLICT'):
        case['campaign'].start_execution_segment('BASE', segment_number=35, upper_bound_seconds=601)
    assert ledger.read_bytes() == before


def test_parent_only_shrunk_dispatch_is_repaired_without_redispatch(installed_case, monkeypatch):
    case = installed_case
    gov, campaign = case['gov'], case['campaign']
    gov.reconcile_segment_mirrors('ARTIFICIAL_BASE')
    receipt = json.loads(gov.receipt_path.read_text(encoding='utf-8'))
    clock, _ = moving_clock(monkeypatch, receipt['expires_at'])
    advance_before_parent(monkeypatch, clock, 10)
    original = governance.immutable

    def crash_after_parent(path, value):
        if path.name == 'ARTIFICIAL_BASE_SEGMENT_000035_DISPATCH.json':
            raise RuntimeError('ARTIFICIAL_CRASH_AFTER_SHRUNK_PARENT')
        return original(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(governance, 'immutable', crash_after_parent)
        with pytest.raises(RuntimeError, match='CRASH_AFTER_SHRUNK_PARENT'):
            gov.start_segment('ARTIFICIAL_BASE')
    ledger = campaign.budget_path.with_name('run_budget_events.jsonl')
    before = ledger.read_bytes()
    state = gov.reconcile_segment_mirrors('ARTIFICIAL_BASE')
    assert state['pending']['upper_bound_seconds'] == 590
    assert state['pending']['segment_number'] == 35 and ledger.read_bytes() == before
    assert gov.reconcile_segment_mirrors('ARTIFICIAL_BASE') == state
    assert ledger.read_bytes() == before


@pytest.mark.parametrize('origin', ['campaign', 'standalone'])
def test_ordinary_dispatch_retains_original_upper_and_limit(tmp_path, origin):
    if origin == 'campaign':
        gov, campaign = account_campaign(tmp_path)
    else:
        gov = standalone_governance(tmp_path)
    dispatch = gov.start_segment('FIXED')
    assert dispatch['upper_bound_seconds'] == 900
    assert 'time_extension_proof' not in dispatch
    if origin == 'campaign':
        parent = campaign.peek_status()['operations']['account']['active_segment']
        assert dispatch['upper_bound_seconds'] == parent['upper_bound_seconds']


def test_calendar_failed_recovery_mirrors_parent_bound_identity_and_unique_charge(calendar_owner_case, monkeypatch):
    case = calendar_owner_case
    gov, campaign = case['gov'], case['campaign']
    originals = {path:path.read_bytes() for path in case['account'].glob('ARTIFICIAL_BASE_SEGMENT_*_CHARGE.json')}
    assert len(originals) == 50
    campaign.install_failed_account_recovery(case['calendar_reference'])
    gov.reconcile_segment_mirrors('ARTIFICIAL_BASE')
    campaign.resume('artificial finite calendar deadline race regression')
    control(case, 'RESUME')
    gov.start_recovery('ARTIFICIAL_BASE')
    expires = case['calendar_summary']['calendar_deadline_extension']['expires_at']
    clock, Clock = moving_clock(monkeypatch, expires)
    monkeypatch.setattr(recovery, 'datetime', Clock)
    advance_before_parent(monkeypatch, clock, 10)
    context = versions.AccountVersionContinuationV1.from_job(case['job'], for_dispatch=True)
    before = event_rows(campaign)
    dispatch = gov.start_segment('ARTIFICIAL_BASE')
    context.record_dispatch('ARTIFICIAL_BASE', dispatch)
    parent = campaign.peek_status()['operations']['BASE']['active_segment']
    assert dispatch['segment_number'] == parent['segment_number'] == 51
    assert dispatch['upper_bound_seconds'] == parent['upper_bound_seconds'] == 590
    assert dispatch['dispatch_identity'] == parent['dispatch_identity']
    assert dispatch['failed_account_recovery_proof'] == parent['failed_account_recovery_proof'] == context.recovery.proof()
    evidence = write(case['account']/'ARTIFICIAL_BASE_SEGMENT_000051_RESOURCE.json', {
        'fixture_only':'ARTIFICIAL_DEADLINE_RACE_NOT_WORKER_MEASUREMENT',
        'dispatch_id':dispatch['dispatch_id'], 'dispatch_identity':dispatch['dispatch_identity'],
        'elapsed_wall_seconds':12., 'time_extension_proof':dispatch['time_extension_proof'],
        'failed_account_recovery_proof':dispatch['failed_account_recovery_proof'],
        'account_version_continuation':context.proof('ARTIFICIAL_BASE')})
    charge = gov.end_segment('ARTIFICIAL_BASE', 51, seconds=12., evidence_identity=evidence['sha256'],
        dispatch_identity=dispatch['dispatch_identity'])
    charges = [row['payload'] for row in event_rows(campaign)[len(before):]
               if row['event_type'] == 'CAMPAIGN_EXECUTION_SEGMENT_CHARGED']
    assert len(charges) == 1 and charges[0]['segment_number'] == 51
    assert all(charges[0][key] == charge[key] for key in ('measured_seconds','seconds','basis','evidence_identity',
        'outcome','charged_at','time_extension_proof','failed_account_recovery_proof','dispatch_identity'))
    assert all(path.read_bytes() == data for path,data in originals.items())
    assert not (case['account']/'ARTIFICIAL_BASE_SEGMENT_000052_DISPATCH.json').exists()
