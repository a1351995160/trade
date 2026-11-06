"""同根第二次治理接续与有限期限；全部数据为人工夹具，不启动经济worker。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from chanlun_trader.research_factory import governance_source_continuation_v2 as second
from chanlun_trader.research_factory import governance_source_revision_v1 as first
from chanlun_trader.research_factory import etf_account_governance_v1 as governance
from chanlun_trader.research_factory import failed_account_recovery_v1 as recovery_module
from chanlun_trader.research_factory import account_version_continuation_v1 as versions
from chanlun_trader.research_factory import account_purpose_time_extension_v1 as extension
from chanlun_trader.research_factory import research_campaign_v1 as campaigns
from chanlun_trader.research_factory.account_version_continuation_v1 import AccountVersionContinuationV1
from chanlun_trader.research_factory.budget import BudgetLedgerMismatchError
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.run_budget import effective_execution_expires_at
from test_account_purpose_time_extension_v1 import pin, write, control
from test_governance_source_revision_v1 import revised_case, charged


@pytest.fixture(scope='module')
def continued_case(revised_case):
    case = revised_case
    context = AccountVersionContinuationV1.from_job(case['job'])
    root = case['root']
    folder = root/'reports/second-stage'
    folder.mkdir()
    source = root/'src/chanlun_trader/research_factory/governance_source_revision_v1.py'
    archive = folder/'old_first_module.py'; archive.write_bytes(source.read_bytes())
    candidate = folder/'new_first_module.py'; candidate.write_bytes(source.read_bytes()+b'\n# artificial second governance version\n')
    rows = [{'path':str(source),'old_sha256':pin(source)['sha256'],'new_sha256':pin(candidate)['sha256'],
        'archive_ref':pin(archive),'candidate_ref':pin(candidate)}]
    module = folder/'second_module.py'
    module.write_bytes((Path(__file__).resolve().parents[2]/second.MODULE_PATH).read_bytes())
    added = {'path':str(root/second.MODULE_PATH),'sha256':pin(module)['sha256']}
    verification = write(folder/'verification.json', {'status':'PASSED','source_replacements':rows,
        'added_runtime_ref':added,'fixture_only':'ARTIFICIAL_PROTOCOL_EVIDENCE_NOT_PRODUCTION_GATE'})
    human = write(folder/'human.json', {'schema_version':'HUMAN_ARTIFICIAL_SECOND_GOVERNANCE_CONTINUATION_AUTHORIZATION_V1',
        'root':str(root),'job_path':str(case['account']/'JOB.json'),'predecessor_revision_ref':case['revision_ref'],
        'artificial_only':True,'allow_protocol_implementation_and_validation':True,'additional_governance_revisions':1,
        'completed_days_to_preserve':505,'settled_segments_to_preserve':52,'execution_window_seconds':259200,
        'execution_window_starts':'ACTUAL_SCOPED_OWNER_ISSUANCE','each_account_seconds':72000,'global_seconds':200000,
        'allow_budget_increase':False,'allow_new_task_candidate_purpose_grant_or_root':False,
        'allow_economic_source_or_input_change':False,'automatic_deadline_extension':False,
        'real_account_actions_allowed':False,'all_fresh_assertions_required':True})
    # 只在独立的人工签发时刻计算72小时，不把“批准实施”时间当作执行窗口开始。
    issued = datetime.now(timezone.utc)
    summary = second.GovernanceSourceContinuationV2.prepare(context,replacements=rows,added_runtime_ref=added,
        verification_ref=verification,human_authorization_ref=human,issued_at=issued.isoformat())
    approval = case['approvals'].approve(summary,approver='artificial_owner',capability=case['token'])
    before_view = deepcopy(case['campaign'].peek_status())
    ledger = case['campaign'].budget_path.with_name('run_budget_events.jsonl')
    before_ledger = ledger.read_bytes()
    immutable = {p:p.read_bytes() for p in case['account'].rglob('*') if p.is_file() and '.lock' not in p.name}
    old_manifest = Path(case['revision_ref']['path']).read_bytes()
    reference = second.GovernanceSourceContinuationV2.install(context,summary,approval,
        added_runtime_candidate_ref=pin(module))
    assert ledger.read_bytes() == before_ledger
    pending = AccountVersionContinuationV1.from_job(case['job'])
    assert pending.recovery.execution_expires_at == summary['expires_at']
    with pytest.raises(PermissionError,match='PARENT_ACTIVATION_REQUIRED'):
        pending.recovery.governance_revision.validate_return(for_dispatch=True)
    installed = case['campaign'].install_governance_source_continuation(reference)
    assert all(p.read_bytes()==data for p,data in immutable.items())
    assert Path(case['revision_ref']['path']).read_bytes()==old_manifest
    case.update(second_summary=summary,second_ref=reference,second_approval=approval,
        second_before_view=before_view,second_ledger_before=before_ledger,second_installed=installed,
        second_immutable=immutable,second_old_manifest=old_manifest)
    return case


def test_second_revision_preserves_days_fees_budget_and_old_proof(continued_case):
    case = continued_case
    value = AccountVersionContinuationV1.from_job(case['job'])
    revision = value.recovery.governance_revision
    assert type(revision) is second.GovernanceSourceContinuationV2
    assert revision.predecessor.reference == case['revision_ref']
    assert revision.summary['maximum_revisions']==2 and revision.predecessor.summary['maximum_revisions']==1
    assert revision.segment_proof('ARTIFICIAL_STRESS',1)==revision.predecessor.segment_proof('ARTIFICIAL_STRESS',1)
    assert revision.segment_proof('ARTIFICIAL_STRESS',2)['manifest_ref']==case['second_ref']
    assert revision.segment_proof('ARTIFICIAL_BASE',51)==revision.predecessor.segment_proof('ARTIFICIAL_BASE',51)
    current=case['campaign'].peek_status()
    for key in ('used','reserved','remaining','grants','trial_usage','authorization'):
        assert current[key]==case['second_before_view'][key]
    assert all(effective_execution_expires_at(current['authorization'],current['operations'][op])==case['second_summary']['expires_at']
        for op in case['second_summary']['operation_ids'].values())
    assert all(p.read_bytes()==data for p,data in case['second_immutable'].items())
    assert Path(case['revision_ref']['path']).read_bytes()==case['second_old_manifest']


def test_second_revision_activation_is_not_repeatable(continued_case):
    case=continued_case
    ledger=case['campaign'].budget_path.with_name('run_budget_events.jsonl')
    before=ledger.read_bytes()
    with pytest.raises(PermissionError,match='PARENT_BOUNDARY_CONFLICT'):
        case['campaign'].install_governance_source_continuation(case['second_ref'])
    assert ledger.read_bytes()==before


@pytest.mark.parametrize('field,value', [('maximum_revisions',3),('grant_delta',1),
    ('global_wall_seconds',288003),('resource_profile',{}),('predecessor_revision_ref',{}),('job_ref',{})])
def test_owner_approval_cannot_widen_second_protocol(continued_case,field,value):
    case=continued_case
    context=AccountVersionContinuationV1.from_job(case['job'])
    actual=context.recovery.governance_revision
    bad=deepcopy(case['second_summary']);bad[field]=value
    bad['identity']=stable_hash({k:v for k,v in bad.items() if k!='identity'})
    approval=case['approvals'].approve(bad,approver='artificial_owner',capability=case['token'])
    case['approvals'].require(approval,bad)
    with pytest.raises((PermissionError,ValueError),match='SUMMARY_BINDING|SOURCE_ALLOWLIST|FINITE_WINDOW'):
        second.GovernanceSourceContinuationV2._binding(case['root'],bad,context.recovery.scope_reference,
            context.recovery.summary_value,case['job'],actual.predecessor.summary['expires_at'],actual.predecessor)


@pytest.mark.parametrize('target',['owner','predecessor_owner','source','archive','module','human','verification','slot','marker','checkpoint'])
def test_new_public_read_rejects_changed_second_boundary(continued_case,target):
    case=continued_case;summary=case['second_summary']
    recovery=AccountVersionContinuationV1.from_job(case['job']).recovery
    folder,slot,marker=second._paths(case['root'],recovery.summary_value)
    row=summary['source_replacements'][0]
    path=(case['approvals'].directory/(case['second_approval']['approval_id']+'.json') if target=='owner'
        else case['approvals'].directory/(case['revision_approval']['approval_id']+'.json') if target=='predecessor_owner'
        else Path(row['path']) if target=='source'
        else Path(row['archive_ref']['path']) if target=='archive'
        else Path(summary['added_runtime_ref']['path']) if target=='module'
        else Path(summary['human_authorization_ref']['path']) if target=='human'
        else Path(summary['verification_ref']['path']) if target=='verification'
        else slot if target=='slot' else marker if target=='marker'
        else Path(summary['accounts']['ARTIFICIAL_STRESS']['checkpoint_archive']['path']))
    before=path.read_bytes()
    ledger=case['campaign'].budget_path.with_name('run_budget_events.jsonl');old_ledger=ledger.read_bytes()
    try:
        if target in ('owner','predecessor_owner'):
            path.unlink()
        elif target in ('slot','marker'):
            path.write_text('{}',encoding='utf-8')
        else:
            path.write_bytes(before+b'\n')
        with pytest.raises((PermissionError,ValueError,BudgetLedgerMismatchError)):
            AccountVersionContinuationV1.from_job(case['job'])
        assert ledger.read_bytes()==old_ledger
    finally:
        path.write_bytes(before)


def test_cannot_inject_derived_deadline_into_an_unrelated_event(continued_case):
    case=continued_case
    budget,_=case['campaign']._budget_with_view(readonly=True)
    ledger=case['campaign'].budget_path.with_name('run_budget_events.jsonl');before=ledger.read_bytes()
    with pytest.raises(BudgetLedgerMismatchError,match='DERIVED_FIELDS_FORBIDDEN'):
        budget.campaign_event('CAMPAIGN_STAGE_CHANGED',{'stage':'EXPLORATION','governance_continuation':{}})
    assert ledger.read_bytes()==before


def test_invalid_second_event_is_rejected_before_ledger_append(continued_case):
    case=continued_case
    budget,_=case['campaign']._budget_with_view(readonly=True)
    ledger=case['campaign'].budget_path.with_name('run_budget_events.jsonl');before=ledger.read_bytes()
    proof=second.resolve_continuation(case['root'],case['second_ref'],case['base']['authorization_id'],
        case['job']['objective_id']).proof()
    proof['continuation_id']='0'*64
    with pytest.raises(BudgetLedgerMismatchError,match='GOVERNANCE_CONTINUATION_PROOF_INVALID'):
        budget.campaign_event(second.EVENT,{'proof':proof,'installed_at':datetime.now(timezone.utc).isoformat()})
    assert ledger.read_bytes()==before


@pytest.mark.parametrize('offset',[-1,259200])
def test_second_authority_rejects_before_issuance_and_at_expiry(continued_case,monkeypatch,offset):
    case=continued_case
    revision=AccountVersionContinuationV1.from_job(case['job']).recovery.governance_revision
    when=datetime.fromisoformat(case['second_summary']['issued_at'])+timedelta(seconds=offset)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return when.astimezone(tz) if tz else when.replace(tzinfo=None)
    monkeypatch.setattr(second,'datetime',Clock)
    with pytest.raises(PermissionError,match='EXPIRED'):
        revision.validate_return(for_dispatch=True)


def test_predecessor_deadline_can_expire_without_restarting_or_new_allocation(continued_case,monkeypatch):
    case=continued_case
    before=case['campaign'].budget_path.with_name('run_budget_events.jsonl').read_bytes()
    when=datetime.fromisoformat(case['second_summary']['previous_expires_at'])+timedelta(seconds=1)
    assert when<datetime.fromisoformat(case['second_summary']['expires_at'])
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return when.astimezone(tz) if tz else when.replace(tzinfo=None)
    for module in (second,first,governance,recovery_module,versions,extension,campaigns):
        monkeypatch.setattr(module,'datetime',Clock)
    control(case,'RESUME')
    try:
        context=AccountVersionContinuationV1.from_job(case['job'],for_dispatch=True)
        assert context.recovery.execution_expires_at==case['second_summary']['expires_at']
        assert not (case['account']/'ARTIFICIAL_STRESS_SEGMENT_000002_DISPATCH.json').exists()
        assert case['campaign'].budget_path.with_name('run_budget_events.jsonl').read_bytes()==before
    finally:
        control(case,'PAUSE')


def test_final_ordinary_segment_uses_second_proof_and_unique_parent_charge(continued_case):
    case=continued_case
    control(case,'RESUME')
    try:
        dispatch=charged(case,'ARTIFICIAL_STRESS','CONTINUE')
        sidecar=json.loads((case['account']/'ARTIFICIAL_STRESS_SEGMENT_000002_EXECUTION_VERSION.json').read_text(encoding='utf-8'))
        assert dispatch['segment_number']==2 and sidecar['governance_source_revision']['manifest_ref']==case['second_ref']
        ledger=[json.loads(line) for line in case['campaign'].budget_path.with_name('run_budget_events.jsonl').read_text(encoding='utf-8').splitlines()]
        fees=[row['payload'] for row in ledger if row['event_type']=='CAMPAIGN_EXECUTION_SEGMENT_CHARGED'
            and row['payload'].get('dispatch_identity')==dispatch['dispatch_identity']]
        assert len(fees)==1 and fees[0]['seconds']==.2
        assert not (case['account']/'ARTIFICIAL_STRESS_SEGMENT_000003_DISPATCH.json').exists()
    finally:
        control(case,'PAUSE')
