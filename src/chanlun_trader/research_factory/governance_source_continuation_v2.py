"""人工同根的一次追加治理接续；保留V1原件，并独立批准有限执行期限。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from .account_purpose_time_extension_v1 import file_sha256, read_file_bytes, read_pinned_json
from .campaign_scope_v1 import OwnerApprovalStoreV1, _write_once
from .common import stable_hash
from .mutation_boundary import ObjectiveMutationLock
from .secure_file_reference_v1 import checked_directory_path, validated_reference_path


VERSION = 'OWNER_GOVERNANCE_SOURCE_CONTINUATION_V2'
DIRECTORY = 'governance-source-continuation-v2'
MODULE_PATH = 'src/chanlun_trader/research_factory/governance_source_continuation_v2.py'
WINDOW_SECONDS = 259200
EVENT = 'CAMPAIGN_GOVERNANCE_SOURCE_CONTINUATION_INSTALLED'
ALLOWED_PATHS = frozenset('src/chanlun_trader/research_factory/' + name for name in (
    'governance_source_revision_v1.py', 'failed_account_recovery_v1.py',
    'account_version_continuation_v1.py', 'etf_account_governance_v1.py',
    'run_budget.py', 'research_campaign_v1.py', 'failed_account_calendar_scope_v1.py', 'campaign_scope_v1.py'))


def _require(value, reason):
    if not value:
        raise PermissionError('GOVERNANCE_CONTINUATION_' + reason)


def _ref(path, root):
    return {'path':str(path), 'sha256':file_sha256(path, root=root,
        error_code='GOVERNANCE_CONTINUATION_REFERENCE_CHANGED')}


def _read(reference, root):
    return read_pinned_json(reference, root=root, error_code='GOVERNANCE_CONTINUATION_REFERENCE_CHANGED')


def _paths(root, recovery):
    folder = Path(recovery['original']['task']['path']).parent/'failed-account-recoveries'/DIRECTORY
    key = stable_hash([VERSION, str(root), recovery['task_id'], recovery['recovery_id']])
    slot = Path(recovery['approval_store'])/'governance-source-continuation-bindings'/(key+'.json')
    marker = Path(root)/'reports/research_campaigns'/stable_hash(recovery['authorization_id'])/(
        'failed-account-recovery-slots/GOVERNANCE_SOURCE_CONTINUATION_V2.json')
    return folder, slot, marker


def _identity(value):
    return isinstance(value, dict) and value.get('identity') == stable_hash({k:v for k,v in value.items() if k != 'identity'})


def _human_authorization(root, reference, job_path, predecessor_reference, global_wall_seconds):
    human = _read(reference, root/'reports')
    _require(human.get('schema_version') == 'HUMAN_ARTIFICIAL_SECOND_GOVERNANCE_CONTINUATION_AUTHORIZATION_V1'
        and human.get('root') == str(root) and human.get('job_path') == job_path
        and human.get('predecessor_revision_ref') == predecessor_reference
        and human.get('artificial_only') is True and human.get('allow_protocol_implementation_and_validation') is True
        and type(human.get('additional_governance_revisions')) is int and human['additional_governance_revisions'] == 1
        and human.get('execution_window_seconds') == WINDOW_SECONDS
        and human.get('execution_window_starts') == 'ACTUAL_SCOPED_OWNER_ISSUANCE'
        and human.get('each_account_seconds') == 72000 and human.get('global_seconds') == global_wall_seconds
        and human.get('all_fresh_assertions_required') is True
        and all(human.get(k) is False for k in ('allow_budget_increase','allow_new_task_candidate_purpose_grant_or_root',
            'allow_economic_source_or_input_change','automatic_deadline_extension','real_account_actions_allowed')),
        'HUMAN_SCOPE_CONFLICT')
    return human


def _first(root, recovery_reference, recovery, job, expires_at):
    """认证V1原槽和Owner；当前源码由V2串接核验，不能要求退回旧字节。"""
    from . import governance_source_revision_v1 as first
    folder, slot = first._paths(root, recovery)
    reference = _ref(folder/'MANIFEST.json', root)
    expected = {'schema_version':first.VERSION, 'manifest_ref':reference}
    _require(_read(_ref(slot, Path(recovery['approval_store'])), Path(recovery['approval_store'])) == expected
        and _read(_ref(first._legacy_marker(root, recovery), root), root) == expected, 'PREDECESSOR_SLOT_CHANGED')
    manifest = _read(reference, root)
    _require(set(manifest) == {'schema_version','summary','approval_ref','identity'}
        and manifest['schema_version'] == first.VERSION and _identity(manifest), 'PREDECESSOR_INVALID')
    summary = manifest['summary']
    first.GovernanceSourceRevisionV1._summary_binding(root, summary, recovery_reference, recovery, job, expires_at)
    OwnerApprovalStoreV1(recovery['approval_store']).require_json(manifest['approval_ref'], summary)
    context = first.GovernanceSourceRevisionV1(root, reference, summary)
    context.validate_return(for_dispatch=False)
    return context


class GovernanceSourceContinuationV2:
    """只接受V1作为前驱；规范新槽不允许第三次换源或再次展期。"""

    def __init__(self, root, reference, summary, predecessor):
        self.root, self.reference = Path(root), deepcopy(reference)
        self.summary, self.predecessor = deepcopy(summary), predecessor
        self.mapping = {row['path']:row for row in summary['source_replacements']}
        self.execution_expires_at = summary['expires_at']
        self.repair = None

    @classmethod
    def read(cls, root, recovery_reference, recovery, job, expires_at, *, for_dispatch=False):
        root = checked_directory_path(root,error_code='GOVERNANCE_CONTINUATION_ROOT_INVALID')
        folder, slot, marker = _paths(root, recovery)
        reference = _ref(folder/'MANIFEST.json', root)
        expected = {'schema_version':VERSION, 'manifest_ref':reference}
        _require(_read(_ref(slot, Path(recovery['approval_store'])), Path(recovery['approval_store'])) == expected
            and _read(_ref(marker, root), root) == expected, 'SLOT_CHANGED')
        manifest = _read(reference, root)
        _require(set(manifest) == {'schema_version','summary','approval_ref','identity'}
            and manifest['schema_version'] == VERSION and _identity(manifest), 'MANIFEST_INVALID')
        predecessor = _first(root, recovery_reference, recovery, job, expires_at)
        summary = manifest['summary']
        cls._binding(root, summary, recovery_reference, recovery, job, expires_at, predecessor)
        OwnerApprovalStoreV1(recovery['approval_store']).require_json(manifest['approval_ref'], summary)
        context = cls(root, reference, summary, predecessor)
        context.job = deepcopy(job)
        from .artificial_stress_repair_v1 import read_installed_repair
        context.repair = read_installed_repair(context, recovery)
        context.validate_sources()
        context.validate_boundary(job, for_dispatch=for_dispatch)
        context.validate_return(for_dispatch=for_dispatch)
        return context

    @staticmethod
    def _binding(root, summary, recovery_reference, recovery, job, expires_at, predecessor):
        fields = {'schema_version','root','recovery_ref','predecessor_revision_ref','job_ref','task_ref','approval_store',
            'issued_at','expires_at','previous_expires_at','maximum_revisions','resource_profile','global_wall_seconds',
            'economic_sources','operation_ids','grant_delta','new_task_delta','new_account_delta','accounts','control',
            'ledger','source_replacements','added_runtime_ref','verification_ref','human_authorization_ref','identity'}
        _require(set(summary) == fields and _identity(summary) and summary.get('schema_version') == VERSION
            and summary.get('root') == str(root) and summary.get('recovery_ref') == recovery_reference
            and summary.get('predecessor_revision_ref') == predecessor.reference
            and summary.get('job_ref') == recovery['original']['job'] and _read(summary['job_ref'], root) == job
            and summary.get('task_ref') == recovery['original']['task']
            and summary.get('approval_store') == recovery['approval_store']
            and summary.get('resource_profile') == job['resources']
            and summary.get('economic_sources') == recovery['economic_sources']
            and summary.get('global_wall_seconds') == recovery['global_wall_seconds']
            and summary.get('previous_expires_at') == expires_at
            and type(summary.get('maximum_revisions')) is int and summary['maximum_revisions'] == 2
            and all(type(summary.get(k)) is int and summary[k] == 0 for k in ('grant_delta','new_task_delta','new_account_delta')),
            'SUMMARY_BINDING_CONFLICT')
        issued, deadline = (datetime.fromisoformat(summary[k]) for k in ('issued_at','expires_at'))
        _require(issued.tzinfo is not None and deadline.tzinfo is not None
            and deadline-issued == timedelta(seconds=WINDOW_SECONDS)
            and deadline > datetime.fromisoformat(expires_at), 'FINITE_WINDOW_REQUIRED')
        human = _human_authorization(root, summary['human_authorization_ref'], summary['job_ref']['path'],
            predecessor.reference, summary['global_wall_seconds'])
        mapping = {name:row['operation_id'] for name,row in recovery['accounts'].items()}
        _require(summary.get('operation_ids') == mapping and set(summary.get('accounts',{})) == set(job['plans'])
            and sum(not row['completed'] for row in summary['accounts'].values()) == 1
            and human.get('completed_days_to_preserve') == sum(row['days'] for row in summary['accounts'].values())
            and human.get('settled_segments_to_preserve') == sum(row['segment_count'] for row in summary['accounts'].values()),
            'ACCOUNT_SCOPE_CONFLICT')
        for name,row in summary['accounts'].items():
            old = predecessor.summary['accounts'][name]
            _require(type(row['days']) is int and old['days'] <= row['days'] <= 504
                and type(row['completed']) is bool and row['completed'] is (row['days'] == 504)
                and type(row['segment_count']) is int and row['segment_count'] >= old['segment_count']
                and row['next_segment_number'] == row['segment_count']+1
                and all(item in row['immutable_refs'] for item in old['immutable_refs']), 'PREDECESSOR_BOUNDARY_CHANGED')
        rows = summary['source_replacements']
        inventory = {r['path']:predecessor.expected_digest(r['path'], r['sha256']) for r in recovery['runtime_sources']}
        inventory[predecessor.summary['added_runtime_ref']['path']] = predecessor.summary['added_runtime_ref']['sha256']
        from .account_purpose_time_extension_v1 import _source_inventory
        # 冻结计划中的运行治理哈希由明确的八文件链解释；backend/strategy经济依赖一律不换。
        forbidden = set(_source_inventory([[plan['backend'],plan['strategy']]
            for plan in recovery['successor_plans'].values()])) | {r['path'] for r in recovery['economic_sources']}
        paths = [Path(r['path']).relative_to(root).as_posix() for r in rows]
        _require(rows and len(paths) == len(set(paths)) and set(paths) <= ALLOWED_PATHS
            and 'src/chanlun_trader/research_factory/governance_source_revision_v1.py' in paths,
            'SOURCE_ALLOWLIST_CONFLICT')
        for row in rows:
            path = validated_reference_path(row['path'], root=root, error_code='GOVERNANCE_CONTINUATION_PATH_INVALID')
            _require(set(row) == {'path','old_sha256','new_sha256','archive_ref','candidate_ref'}
                and str(path) not in forbidden and inventory.get(str(path)) == row['old_sha256'] != row['new_sha256']
                and row['archive_ref']['sha256'] == row['old_sha256'] and row['candidate_ref']['sha256'] == row['new_sha256']
                and _ref(row['archive_ref']['path'], root/'reports') == row['archive_ref']
                and _ref(row['candidate_ref']['path'], root/'reports') == row['candidate_ref'], 'SOURCE_CHAIN_CONFLICT')
        _require(summary['added_runtime_ref']['path'] == str(root/MODULE_PATH), 'ADDED_RUNTIME_CONFLICT')
        evidence = _read(summary['verification_ref'], root/'reports')
        _require(evidence.get('status') == 'PASSED' and evidence.get('source_replacements') == rows
            and evidence.get('added_runtime_ref') == summary['added_runtime_ref'], 'EXACT_VALIDATION_REQUIRED')
        ledger = summary['ledger']
        _require(ledger['path'] == predecessor.summary['ledger']['path']
            and type(ledger['line_count']) is int and ledger['line_count'] >= predecessor.summary['ledger']['line_count']
            and type(summary['control']['count']) is int and summary['control']['count'] >= predecessor.summary['control']['count'],
            'BOUNDARY_PATH_CONFLICT')

    def expected_digest(self, path, predecessor_digest):
        previous = self.predecessor.expected_digest(path, predecessor_digest)
        row = self.mapping.get(str(path))
        if row is not None:
            _require(previous == row['old_sha256']
                and _ref(row['archive_ref']['path'], self.root/'reports') == row['archive_ref'], 'SOURCE_CHAIN_CHANGED')
            previous = row['new_sha256']
        return self._effective_digest(path, previous)

    def _effective_digest(self, path, digest):
        return self.repair.expected_digest(path, digest) if self.repair is not None else digest

    def current_runtime_refs(self, historical_refs):
        return [{'path':row['path'],'sha256':self.expected_digest(row['path'],row['sha256'])} for row in historical_refs]

    def runtime_additions(self):
        first = self.predecessor.summary['added_runtime_ref']
        second = self.summary['added_runtime_ref']
        refs = [{'path':first['path'],'sha256':self.expected_digest(first['path'],first['sha256'])},
                {'path':second['path'],'sha256':self._effective_digest(second['path'],second['sha256'])}]
        if self.repair is not None:
            refs.append(deepcopy(self.repair.summary['added_runtime_ref']))
        return refs

    def replacement_chain(self):
        chain = [self.predecessor.summary['source_replacements'], self.summary['source_replacements']]
        if self.repair is not None:
            chain.append(self.repair.summary['source_replacements'])
        return chain

    def predecessor_capabilities(self, current):
        from .account_version_capability_bridge_v1 import _project_snapshot
        if self.repair is not None:
            current = _project_snapshot(current, self.repair.summary['source_replacements'])
        return self.predecessor.predecessor_capabilities(_project_snapshot(current,self.summary['source_replacements']))

    def validate_sources(self):
        for row in self.predecessor.mapping.values():
            _require(_ref(row['archive_ref']['path'],self.root/'reports') == row['archive_ref']
                and _ref(row['candidate_ref']['path'],self.root/'reports') == row['candidate_ref'], 'PREDECESSOR_SOURCE_CHANGED')
            _require(_ref(row['path'],self.root)['sha256'] == self.expected_digest(row['path'],row['old_sha256']), 'SOURCE_CHANGED')
        for row in self.mapping.values():
            _require(_ref(row['path'],self.root)['sha256'] == self._effective_digest(row['path'],row['new_sha256'])
                and _ref(row['archive_ref']['path'],self.root/'reports') == row['archive_ref']
                and _ref(row['candidate_ref']['path'],self.root/'reports') == row['candidate_ref'], 'SOURCE_CHANGED')
        for reference in self.runtime_additions():
            _require(_ref(reference['path'],self.root) == reference, 'RUNTIME_CHANGED')
        if self.repair is not None:
            self.repair.validate_sources()

    def validate_boundary(self, job, *, for_dispatch=False, exact=False):
        from .governance_source_revision_v1 import GovernanceSourceRevisionV1
        # 前驱全部不可变引用必须包含在新边界中；派发时对该并集逐一fresh核字节。
        self.predecessor.validate_boundary(job,for_dispatch=False)
        GovernanceSourceRevisionV1.validate_boundary(self,job,for_dispatch=for_dispatch,exact=exact)
        if self.repair is not None:
            self.repair.validate_boundary()

    def proof(self):
        return {'manifest_ref':deepcopy(self.reference),'continuation_id':self.summary['identity']}

    def segment_proof(self,name,number):
        if (self.repair is not None and name == self.repair.purpose
                and number >= self.repair.boundary['next_segment_number']):
            return {'manifest_ref':deepcopy(self.repair.reference), 'revision_id':self.repair.summary['identity'],
                'purpose':name,'first_segment_number':self.repair.boundary['next_segment_number']}
        row = self.summary['accounts'][name]
        if number <= row['segment_count']:
            return self.predecessor.segment_proof(name,number)
        _require(not row['completed'],'COMPLETED_ACCOUNT_NO_DISPATCH')
        return {'manifest_ref':deepcopy(self.reference),'revision_id':self.summary['identity'],
            'purpose':name,'first_segment_number':row['next_segment_number']}

    def validate_return(self, *, for_dispatch=False):
        self.predecessor.validate_return(for_dispatch=False)
        manifest = _read(self.reference,self.root)
        _require(_identity(manifest) and manifest['summary'] == self.summary,'RETURN_MANIFEST_CHANGED')
        recovery = _read(self.summary['recovery_ref'],self.root)['summary']
        _,slot,marker = _paths(self.root,recovery)
        expected = {'schema_version':VERSION,'manifest_ref':self.reference}
        _require(_read(_ref(slot,Path(recovery['approval_store'])),Path(recovery['approval_store'])) == expected
            and _read(_ref(marker,self.root),self.root) == expected,'RETURN_SLOT_CHANGED')
        OwnerApprovalStoreV1(recovery['approval_store']).require_json(manifest['approval_ref'],self.summary)
        if for_dispatch:
            now = datetime.now(timezone.utc)
            _require(datetime.fromisoformat(self.summary['issued_at']) <= now < datetime.fromisoformat(self.execution_expires_at),'EXPIRED')
            events = [json.loads(line) for line in read_file_bytes(self.summary['ledger']['path'],root=self.root,
                error_code='GOVERNANCE_CONTINUATION_LEDGER_CHANGED').splitlines()]
            activation = [event for event in events if event['event_type'] == EVENT]
            _require(len(activation) == 1 and activation[0]['payload']['proof'] == self.proof()
                and activation[0]['event_id'] == stable_hash({k:activation[0][k] for k in ('run_id','event_type','payload')})
                and events.index(activation[0]) == self.summary['ledger']['line_count'], 'PARENT_ACTIVATION_REQUIRED')
            payload = activation[0]['payload']
            installed_at = datetime.fromisoformat(payload['installed_at'])
            _require(set(payload) == {'proof','installed_at'} and installed_at.tzinfo is not None
                and datetime.fromisoformat(self.summary['issued_at']) <= installed_at <= now
                and installed_at < datetime.fromisoformat(self.execution_expires_at),
                'PARENT_ACTIVATION_TIME_CONFLICT')
        if self.repair is not None:
            self.repair.validate_return(for_dispatch=for_dispatch)

    @classmethod
    def pause_expired_predecessor(cls, context, *, human_authorization_ref):
        """已到期、全部结清的原用途只允许PAUSE；不签发、展期、激活或派发。"""
        from .account_version_continuation_v1 import AccountVersionContinuationV1, _control
        from .governance_source_revision_v1 import GovernanceSourceRevisionV1
        from .account_purpose_time_extension_v1 import _checkpoint
        from .run_budget import effective_execution_status, effective_execution_total_seconds
        _require(type(context) is AccountVersionContinuationV1, 'CONTEXT_REQUIRED')
        root, job = context.root, deepcopy(context.job)
        context.validate(for_dispatch=False)
        _require(context.root == root and context.job == job, 'JOB_CHANGED')
        job = deepcopy(context.job)
        with ObjectiveMutationLock.for_resource(Path(job['root'])/'LONG_EXECUTION.lock'):
            with ObjectiveMutationLock.for_resource(Path(job['root'])/'CONTROL.lock'):
                context.validate(for_dispatch=False)
                _require(context.root == root and context.job == job, 'JOB_CHANGED')
                predecessor = getattr(context.recovery, 'governance_revision', None)
                _require(type(predecessor) is GovernanceSourceRevisionV1, 'SINGLE_PREDECESSOR_REQUIRED')
                recovery = context.recovery.summary_value
                human = _human_authorization(root, human_authorization_ref, recovery['original']['job']['path'],
                    predecessor.reference, recovery['global_wall_seconds'])
                _require(datetime.now(timezone.utc) >= datetime.fromisoformat(context.recovery.execution_expires_at),
                    'PREDECESSOR_NOT_EXPIRED')
                folder, slot, marker = _paths(root, recovery)
                _require(not any(path.exists() for path in (folder/'MANIFEST.json',slot,marker)), 'ALREADY_INSTALLED')
                revoked = validated_reference_path(Path(job['root'])/'REVOKED.json',root=root,
                    error_code='GOVERNANCE_CONTINUATION_REVOKED_PATH_INVALID')
                _require(not revoked.exists(), 'REVOKED')
                control = _control(job)
                view = context.campaign.peek_status()
                _require(not view.get('revoked') and not any(op.get('active_segment') is not None
                    or effective_execution_status(op) == 'UNKNOWN' for op in view['operations'].values()), 'ACTIVE_OR_UNKNOWN')
                days = segments = running = 0
                for name in job['plans']:
                    state = context.governance.segment_status(name)
                    operation = view['operations'][recovery['accounts'][name]['operation_id']]
                    status = effective_execution_status(operation)
                    _require(status in ('RUNNING','COMPLETED') and state['pending'] is None
                        and all(row['charge'] is not None for row in state['segments'])
                        and effective_execution_total_seconds(operation) == state['effective_total_seconds'] == 72000,
                        'UNSETTLED_BOUNDARY')
                    context._parent_prefix(state,operation,0)
                    checkpoint = _checkpoint(root,job,name)
                    _require(checkpoint is not None and (checkpoint['processed_days'] == 504) is (status == 'COMPLETED'),
                        'CHECKPOINT_REQUIRED')
                    days += checkpoint['processed_days']; segments += len(state['segments']); running += status == 'RUNNING'
                _require(running == 1 and days == human.get('completed_days_to_preserve')
                    and segments == human.get('settled_segments_to_preserve'), 'ACCOUNT_SCOPE_CONFLICT')
                predecessor.validate_boundary(job,for_dispatch=True)
                receipt = _read(recovery['original']['receipt'],root)
                _require(Path(recovery['original']['receipt']['path']) == context.governance.receipt_path,
                    'RECEIPT_PATH_CONFLICT')
                context.validate(for_dispatch=False)
                _require(context.root == root and context.job == job, 'JOB_CHANGED')
                predecessor.validate_return(for_dispatch=False)
                _require(_human_authorization(root,human_authorization_ref,recovery['original']['job']['path'],
                    predecessor.reference,recovery['global_wall_seconds']) == human and not revoked.exists()
                    and _control(job) == control, 'PAUSE_BOUNDARY_CHANGED')
                if control['paused']:
                    return control
                record = {'sequence':len(control['events'])+1,'previous_head':control['head'],'event':'PAUSE',
                    'job_sha256':_ref(Path(job['root'])/'JOB.json',root)['sha256'],'receipt_id':receipt['receipt_id'],
                    'recorded_at':datetime.now(timezone.utc).isoformat()}
                record['event_id'] = stable_hash(record)
                target = validated_reference_path(Path(job['root'])/('CONTROL_'+str(record['sequence']).zfill(6)+'.json'),
                    root=root,error_code='GOVERNANCE_CONTINUATION_CONTROL_PATH_INVALID')
                _require(not target.exists(), 'CONTROL_ALREADY_EXISTS')
                _write_once(target,record)
                return _control(job)

    @classmethod
    def prepare(cls,context,*,replacements,added_runtime_ref,verification_ref,human_authorization_ref,issued_at=None):
        from .account_version_continuation_v1 import AccountVersionContinuationV1,_control
        from .governance_source_revision_v1 import GovernanceSourceRevisionV1
        from .account_purpose_time_extension_v1 import _checkpoint
        from .run_budget import effective_execution_status
        _require(type(context) is AccountVersionContinuationV1,'CONTEXT_REQUIRED')
        context.validate()
        predecessor = getattr(context.recovery,'governance_revision',None)
        _require(type(predecessor) is GovernanceSourceRevisionV1,'SINGLE_PREDECESSOR_REQUIRED')
        root,recovery,job = context.root,context.recovery.summary_value,context.job
        folder,slot,marker = _paths(root,recovery)
        _require(not any(p.exists() for p in (folder/'MANIFEST.json',slot,marker)),'ALREADY_INSTALLED')
        control = _control(job)
        _require(control['paused'] and control['events'],'PAUSED_BOUNDARY_REQUIRED')
        view = context.campaign.peek_status()
        _require(not view.get('revoked') and not any(effective_execution_status(op) == 'UNKNOWN'
            or op.get('active_segment') is not None for op in view['operations'].values()),'ACTIVE_OR_UNKNOWN')
        accounts={}
        for name in job['plans']:
            state = context.governance.segment_status(name)
            operation = view['operations'][recovery['accounts'][name]['operation_id']]
            _require(state['pending'] is None and all(row['charge'] is not None for row in state['segments'])
                and effective_execution_status(operation) in ('RUNNING','COMPLETED'),'UNSETTLED_BOUNDARY')
            context._parent_prefix(state,operation,0)
            checkpoint = _checkpoint(root,job,name)
            _require(checkpoint is not None,'CHECKPOINT_REQUIRED')
            archive=folder/'BOUNDARY'/(name+'_CHECKPOINT.json')
            _write_once(archive,_read(checkpoint['reference'],root))
            refs=[_ref(p,root) for p in sorted(Path(job['root']).glob(name+'_SEGMENT_*')) if p.is_file()]
            completed=effective_execution_status(operation)=='COMPLETED'
            if completed:
                refs += [_ref(p,root) for p in sorted(Path(job['root']).glob(name+'_*.json'))
                    if '_SEGMENT_' not in p.name and not p.name.endswith('_CHECKPOINT.json')]
            accounts[name]={'days':checkpoint['processed_days'],'last_day':checkpoint['last_day'],'completed':completed,
                'segment_count':len(state['segments']),'next_segment_number':len(state['segments'])+1,
                'charged_seconds':state['charged_seconds'],'checkpoint_archive':_ref(archive,root),'immutable_refs':refs}
        issued=datetime.now(timezone.utc) if issued_at is None else datetime.fromisoformat(issued_at)
        _require(issued.tzinfo is not None and issued <= datetime.now(timezone.utc),'ISSUANCE_IN_FUTURE')
        ledger=context.campaign.budget_path.with_name('run_budget_events.jsonl')
        raw=read_file_bytes(ledger,root=root,error_code='GOVERNANCE_CONTINUATION_LEDGER_CHANGED')
        value={'schema_version':VERSION,'root':str(root),'recovery_ref':context.recovery.scope_reference,
            'predecessor_revision_ref':predecessor.reference,'job_ref':recovery['original']['job'],
            'task_ref':recovery['original']['task'],'approval_store':recovery['approval_store'],
            'issued_at':issued.isoformat(),'expires_at':(issued+timedelta(seconds=WINDOW_SECONDS)).isoformat(),
            'previous_expires_at':context.recovery.execution_expires_at,'maximum_revisions':2,
            'resource_profile':job['resources'],'global_wall_seconds':recovery['global_wall_seconds'],
            'economic_sources':recovery['economic_sources'],'operation_ids':{name:row['operation_id'] for name,row in recovery['accounts'].items()},
            'grant_delta':0,'new_task_delta':0,'new_account_delta':0,'accounts':accounts,
            'control':{'head':control['head'],'count':len(control['events'])},
            'ledger':{'path':str(ledger),'line_count':len(raw.splitlines()),'prefix_sha256':hashlib.sha256(raw).hexdigest()},
            'source_replacements':deepcopy(replacements),'added_runtime_ref':deepcopy(added_runtime_ref),
            'verification_ref':deepcopy(verification_ref),'human_authorization_ref':deepcopy(human_authorization_ref)}
        value['identity']=stable_hash(value)
        cls._binding(root,value,context.recovery.scope_reference,recovery,job,context.recovery.execution_expires_at,predecessor)
        return value

    @classmethod
    def install(cls,context,summary,approval_ref,*,added_runtime_candidate_ref):
        from .governance_source_revision_v1 import _install_bytes
        root,recovery=context.root,context.recovery.summary_value
        folder,slot,marker=_paths(root,recovery)
        _require(not any(p.exists() for p in (folder/'MANIFEST.json',slot,marker)),'ALREADY_INSTALLED')
        with ObjectiveMutationLock.for_resource(Path(context.job['root'])/'LONG_EXECUTION.lock'):
            with ObjectiveMutationLock.for_resource(Path(context.job['root'])/'CONTROL.lock'):
                owner=OwnerApprovalStoreV1(summary['approval_store'])
                owner.require(approval_ref,summary)
                fresh=cls.prepare(context,replacements=summary['source_replacements'],added_runtime_ref=summary['added_runtime_ref'],
                    verification_ref=summary['verification_ref'],human_authorization_ref=summary['human_authorization_ref'],
                    issued_at=summary['issued_at'])
                _require(fresh==summary,'INSTALL_BOUNDARY_CHANGED')
                _require(datetime.now(timezone.utc)<datetime.fromisoformat(summary['expires_at']),'EXPIRED')
                source_bytes=[]
                for row in summary['source_replacements']:
                    _require(_ref(row['path'],root)['sha256']==row['old_sha256'],'INSTALL_SOURCE_CHANGED')
                    source_bytes.append((row['path'],read_file_bytes(row['candidate_ref']['path'],root=root/'reports',
                        error_code='GOVERNANCE_CONTINUATION_CANDIDATE_CHANGED'),row['new_sha256']))
                _require(_ref(added_runtime_candidate_ref['path'],root/'reports')==added_runtime_candidate_ref
                    and added_runtime_candidate_ref['sha256']==summary['added_runtime_ref']['sha256']
                    and not Path(summary['added_runtime_ref']['path']).exists(),'RUNTIME_INSTALL_CONFLICT')
                source_bytes.append((summary['added_runtime_ref']['path'],read_file_bytes(added_runtime_candidate_ref['path'],root=root/'reports',
                    error_code='GOVERNANCE_CONTINUATION_CANDIDATE_CHANGED'),added_runtime_candidate_ref['sha256']))
                owner.require(approval_ref,summary)
                _require(datetime.now(timezone.utc)<datetime.fromisoformat(summary['expires_at']),'EXPIRED')
                for path,data,digest in source_bytes:
                    _install_bytes(path,data,digest,root)
                owner.require(approval_ref,summary)
                _require(datetime.now(timezone.utc)<datetime.fromisoformat(summary['expires_at']),'EXPIRED')
                manifest={'schema_version':VERSION,'summary':summary,'approval_ref':approval_ref}
                manifest['identity']=stable_hash(manifest)
                _write_once(folder/'MANIFEST.json',manifest)
                reference=_ref(folder/'MANIFEST.json',root)
                expected={'schema_version':VERSION,'manifest_ref':reference}
                _write_once(slot,expected); _write_once(marker,expected)
                return reference


def resolve_continuation(root,reference,authorization_id,objective_id):
    """父纯重放只读验证；不调用campaign或账户入口。"""
    root=checked_directory_path(root,error_code='GOVERNANCE_CONTINUATION_ROOT_INVALID')
    manifest=_read(reference,root)
    summary=manifest['summary']
    recovery_manifest=_read(summary['recovery_ref'],root)
    recovery=recovery_manifest['summary']
    root=checked_directory_path(recovery['root'],root=root,error_code='GOVERNANCE_CONTINUATION_ROOT_INVALID')
    _require(recovery['authorization_id']==authorization_id and recovery['objective_id']==objective_id,
        'AUTHORIZATION_OR_OBJECTIVE_CONFLICT')
    _require(_identity(recovery_manifest),'RECOVERY_IDENTITY_INVALID')
    OwnerApprovalStoreV1(recovery['approval_store']).require_json(recovery_manifest['approval_ref'],recovery)
    job=_read(recovery['original']['job'],root)
    previous=recovery.get('calendar_deadline_extension',{}).get('expires_at',recovery['expires_at'])
    context=GovernanceSourceContinuationV2.read(root,summary['recovery_ref'],recovery,job,previous)
    _require(context.reference==reference,'CANONICAL_REFERENCE_CONFLICT')
    return context


def validate_parent_boundary(context,authorization,operations,revoked):
    from .run_budget import effective_execution_status,effective_execution_total_seconds
    summary=context.summary
    _require(not revoked and authorization['resource_limits']['wall_seconds']==summary['global_wall_seconds'],
        'AUTHORIZATION_OR_LIMIT_CHANGED')
    _require(not any(op.get('active_segment') is not None or effective_execution_status(op)=='UNKNOWN'
        for op in operations.values()),'ACTIVE_OR_UNKNOWN')
    recovery=_read(summary['recovery_ref'],context.root)['summary']
    proof={'manifest_ref':summary['recovery_ref'],'recovery_id':recovery['recovery_id']}
    for name,row in summary['accounts'].items():
        operation=operations.get(summary['operation_ids'][name],{})
        _require(operation.get('kind')=='ACCOUNT' and operation.get('stage')=='EXPLORATION'
            and operation.get('failed_account_recovery_proof')==proof
            and operation.get('execution_expires_at',authorization['expires_at'])==summary['previous_expires_at']
            and operation.get('governance_continuation') is None
            and effective_execution_total_seconds(operation)==72000 and operation['execution_profile']==summary['resource_profile']
            and effective_execution_status(operation)==('COMPLETED' if row['completed'] else 'RUNNING')
            and len(operation.get('segments',[]))==row['segment_count']
            and abs(operation.get('active_wall_seconds',0)-row['charged_seconds'])<1e-7,
            'PARENT_BOUNDARY_CONFLICT')


def apply_parent_continuation(authorization,operations,item,revoked,*,event_index):
    from .budget import BudgetLedgerMismatchError
    try:
        _require(authorization is not None and set(item)=={'proof','installed_at'},'EVENT_INVALID')
        proof=item['proof']
        _require(isinstance(proof,dict) and set(proof)=={'manifest_ref','continuation_id'},'PROOF_INVALID')
        context=resolve_continuation(Path(authorization['root']),proof['manifest_ref'],
            authorization['authorization_id'],authorization['objective_id'])
        _require(context.proof()==proof,'PROOF_CONFLICT')
        _require(type(event_index) is int and event_index == context.summary['ledger']['line_count'],'EVENT_ORDER_CONFLICT')
        installed=datetime.fromisoformat(item['installed_at'])
        _require(installed.tzinfo is not None and datetime.fromisoformat(context.summary['issued_at'])
            <= installed < datetime.fromisoformat(context.execution_expires_at),'EVENT_TIME_CONFLICT')
        validate_parent_boundary(context,authorization,operations,revoked)
        # 完整前缀及不可变日／费用仍由协议read核验；事件只派生同一对用途的期限。
        for operation_id in context.summary['operation_ids'].values():
            operations[operation_id]['governance_continuation']={'proof':deepcopy(proof),
                'execution_expires_at':context.execution_expires_at,'installed_at':item['installed_at']}
    except (PermissionError,ValueError,TypeError,KeyError,OSError) as exc:
        raise BudgetLedgerMismatchError('CAMPAIGN_GOVERNANCE_CONTINUATION_PROOF_INVALID') from exc


def validate_parent_submission(authorization, item):
    """仅新事件提交前使用；历史重放不受今天的时刻影响。"""
    from .budget import BudgetLedgerMismatchError
    try:
        context = resolve_continuation(Path(authorization['root']), item['proof']['manifest_ref'],
            authorization['authorization_id'], authorization['objective_id'])
        _require(context.proof() == item['proof'], 'PROOF_CONFLICT')
        context.validate_return(for_dispatch=False)
        installed = datetime.fromisoformat(item['installed_at'])
        # 最后一次完整fresh返回后再采时；核验跨期或Owner撤销均不得追加事件。
        now = datetime.now(timezone.utc)
        _require(installed.tzinfo is not None and datetime.fromisoformat(context.summary['issued_at'])
            <= installed <= now < datetime.fromisoformat(context.execution_expires_at), 'SUBMISSION_EXPIRED_OR_FUTURE')
    except (PermissionError, ValueError, TypeError, KeyError, OSError) as exc:
        raise BudgetLedgerMismatchError('CAMPAIGN_GOVERNANCE_CONTINUATION_SUBMISSION_INVALID') from exc
