"""原人工 STRESS 在已签名换源后的单次准备失败接续。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

from .account_purpose_time_extension_v1 import read_file_bytes, _source_inventory
from .campaign_scope_v1 import OwnerApprovalStoreV1, _write_once
from .common import stable_hash
from .governance_source_continuation_v2 import EVENT as PREDECESSOR_EVENT
from .governance_source_continuation_v2 import MODULE_PATH as PREDECESSOR_MODULE, WINDOW_SECONDS
from .governance_source_continuation_v2 import _require as _require_predecessor
from .governance_source_continuation_v2 import _identity, _read, _ref, resolve_continuation
from .mutation_boundary import ObjectiveMutationLock
from .secure_file_reference_v1 import validated_reference_path

VERSION = 'OWNER_ARTIFICIAL_STRESS_REPAIR_V1'
HUMAN_VERSION = 'HUMAN_ARTIFICIAL_STRESS_REPAIR_AUTHORIZATION_V1'
RENEWAL_VERSION = 'HUMAN_ARTIFICIAL_STRESS_REPAIR_RENEWAL_V1'
EVENT = 'CAMPAIGN_ARTIFICIAL_STRESS_REPAIR_INSTALLED'
MODULE_PATH = 'src/chanlun_trader/research_factory/artificial_stress_repair_v1.py'
WINDOWS_PROCESS_SOURCE = 'src/chanlun_trader/research_daemon_state.py'
ALLOWED_PATHS = frozenset({
    PREDECESSOR_MODULE, 'scripts/run_strategy_account_v1.py',
    *('src/chanlun_trader/research_factory/' + name for name in (
        'failed_account_recovery_v1.py', 'etf_account_governance_v1.py',
        'research_campaign_v1.py', 'run_budget.py', 'failed_account_recovery_evidence_v1.py')),
})
FAILURE_FILES = ('DISPATCH', 'EXECUTION_VERSION', 'WORKER', 'STARTUP', 'FAILURE', 'RESOURCE', 'CHARGE')
CHARGE_FIELDS = ('measured_seconds', 'seconds', 'basis', 'evidence_identity', 'outcome', 'charged_at',
                 'time_extension_proof', 'failed_account_recovery_proof', 'dispatch_identity')


def _require(value, reason):
    if not value:
        raise PermissionError('ARTIFICIAL_STRESS_REPAIR_' + reason)


def _paths(predecessor):
    old = predecessor.summary
    owner = Path(old['approval_store'])
    folder = Path(old['task_ref']['path']).parent / 'failed-account-recoveries' / 'artificial-stress-repair-v1'
    key = stable_hash([VERSION, str(predecessor.root), old['identity']])
    # 保留旧入口的原恢复清单与账本槽唯一性。接续证明只由签名的 V2 路径读取。
    paths = (folder / 'REPAIR_MANIFEST.json', owner / 'artificial-stress-repair-bindings' / (key + '.json'),
             Path(old['ledger']['path']).parent / 'artificial-stress-repair-slots/ARTIFICIAL_STRESS_REPAIR_V1.json')
    for path, root in zip(paths, (predecessor.root, owner, predecessor.root)):
        validated_reference_path(path, root=root, error_code='ARTIFICIAL_STRESS_REPAIR_PATH_INVALID')
    return paths


def _human(predecessor, reference):
    reports = predecessor.root / 'reports'
    value = _read(reference, reports)
    renewal = value if value.get('schema_version') == RENEWAL_VERSION else None
    human = _read(renewal['original_human_authorization_ref'], reports) if renewal is not None else value
    old = predecessor.summary
    purpose = human.get('purpose')
    _require(isinstance(purpose, str), 'HUMAN_SCOPE_CONFLICT')
    prior = old['accounts'].get(purpose, {})
    deadline = datetime.fromisoformat(human['execution_expires_at'])
    _require(human.get('schema_version') == HUMAN_VERSION
        and human.get('approved') is True and human.get('artificial_only') is True
        and human.get('root') == str(predecessor.root) and human.get('job_ref') == old['job_ref']
        and human.get('predecessor_ref') == predecessor.reference
        and purpose.endswith('_STRESS') and prior.get('completed') is False
        and all(row['completed'] for name, row in old['accounts'].items() if name != purpose)
        and type(human.get('maximum_repairs')) is int and human['maximum_repairs'] == 1
        and human.get('each_account_seconds') == 72000 and human.get('global_wall_seconds') == old['global_wall_seconds']
        and deadline.tzinfo is not None
        and deadline == datetime.fromisoformat(old['expires_at'])
        and human.get('failed_segment_number') == prior['segment_count'] + 1
        and human.get('next_segment_number') == prior['segment_count'] + 2
        and human.get('next_account_day') == prior['days'] + 1
        and human.get('completed_days_to_preserve') == sum(row['days'] for row in old['accounts'].values())
        and human.get('settled_segments_to_preserve') == 1 + sum(row['segment_count'] for row in old['accounts'].values())
        and type(human.get('max_remaining_seconds')) is int and human['max_remaining_seconds'] > 0
        and human.get('requires_exact_source_and_protocol_gates') is True
        and human.get('all_fresh_assertions_required') is True
        and all(human.get(key) is False for key in (
            'allow_new_task_candidate_purpose_grant_or_root', 'allow_economic_source_or_input_change',
            'allow_budget_increase', 'allow_deadline_extension', 'automatic_retry', 'real_account_actions_allowed')),
        'HUMAN_SCOPE_CONFLICT')
    if renewal is not None:
        source = renewal['windows_process_source']
        recorded = datetime.fromisoformat(renewal['recorded_at'])
        _require(set(renewal) == {'schema_version', 'approved', 'human_message', 'recorded_at',
            'original_human_authorization_ref', 'execution_window_seconds', 'windows_process_source'}
            and renewal.get('approved') is True and renewal.get('execution_window_seconds') == WINDOW_SECONDS
            and len(old['accounts']) == 2
            and recorded.tzinfo is not None and recorded >= deadline
            and set(source) == {'path', 'old_sha256', 'new_sha256'}
            and source['path'] == WINDOWS_PROCESS_SOURCE, 'RENEWAL_SCOPE_CONFLICT')
    return human, renewal


def _ledger_lines(predecessor):
    path = predecessor.summary['ledger']['path']
    return read_file_bytes(path, root=predecessor.root,
        error_code='ARTIFICIAL_STRESS_REPAIR_LEDGER_CHANGED').splitlines(keepends=True)


def _sha_lines(lines):
    return hashlib.sha256(b''.join(lines)).hexdigest()


def validate_predecessor_activation(predecessor, now):
    """在旧模块尚未换入时也按原签发区间验证旧激活事件。"""
    summary = predecessor.summary
    data = read_file_bytes(summary['ledger']['path'], root=predecessor.root,
        error_code='GOVERNANCE_CONTINUATION_LEDGER_CHANGED')
    events = [json.loads(line) for line in data.splitlines()]
    index = summary['ledger']['line_count']
    activation = [event for event in events if event['event_type'] == PREDECESSOR_EVENT]
    _require_predecessor(len(activation) == 1 and activation[0]['payload']['proof'] == predecessor.proof()
        and activation[0]['event_id'] == stable_hash({key: activation[0][key] for key in ('run_id', 'event_type', 'payload')})
        and events.index(activation[0]) == index, 'PARENT_ACTIVATION_REQUIRED')
    payload = activation[0]['payload']
    installed = datetime.fromisoformat(payload['installed_at'])
    _require_predecessor(set(payload) == {'proof', 'installed_at'} and installed.tzinfo is not None
        and datetime.fromisoformat(summary['issued_at']) <= installed <= now
        and installed < datetime.fromisoformat(summary['expires_at']), 'PARENT_ACTIVATION_TIME_CONFLICT')


def _validate_predecessor(predecessor, renewal):
    predecessor.validate_return(for_dispatch=renewal is None)
    if renewal is not None:
        validate_predecessor_activation(predecessor, datetime.now(timezone.utc))


def _source_binding(predecessor, recovery, summary, renewal):
    root = predecessor.root
    rows = summary['source_replacements']
    inventory = {ref['path']: ref['sha256'] for ref in
                 predecessor.current_runtime_refs(recovery['runtime_sources']) + predecessor.runtime_additions()}
    allowed = ALLOWED_PATHS
    if renewal is not None:
        source = renewal['windows_process_source']
        process_path = str(root / WINDOWS_PROCESS_SOURCE)
        # 该文件在原运行目录清单中，由此次人工批准明确绑定原字节和新字节。
        inventory.setdefault(process_path, source['old_sha256'])
        allowed = allowed | {WINDOWS_PROCESS_SOURCE}
        _require(any(row['path'] == process_path and row['old_sha256'] == source['old_sha256']
            and row['new_sha256'] == source['new_sha256'] for row in rows), 'RENEWAL_SOURCE_CONFLICT')
    forbidden = set(_source_inventory([[plan['strategy'], plan['backend']]
        for plan in recovery['successor_plans'].values()])) | {ref['path'] for ref in recovery['economic_sources']}
    paths = [Path(row['path']).relative_to(root).as_posix() for row in rows]
    _require(len(set(paths)) == len(paths) and PREDECESSOR_MODULE in paths
        and set(paths) <= allowed, 'SOURCE_ALLOWLIST_CONFLICT')
    for row in rows:
        path = validated_reference_path(row['path'], root=root, error_code='ARTIFICIAL_STRESS_REPAIR_SOURCE_INVALID')
        _require(set(row) == {'path', 'old_sha256', 'new_sha256', 'archive_ref', 'candidate_ref'}
            and str(path) not in forbidden and inventory.get(str(path)) == row['old_sha256'] != row['new_sha256']
            and row['archive_ref']['sha256'] == row['old_sha256'] and row['candidate_ref']['sha256'] == row['new_sha256']
            and _ref(row['archive_ref']['path'], root / 'reports') == row['archive_ref']
            and _ref(row['candidate_ref']['path'], root / 'reports') == row['candidate_ref'], 'SOURCE_CHAIN_CONFLICT')
    _require(summary['added_runtime_ref']['path'] == str(root / MODULE_PATH), 'ADDED_SOURCE_CONFLICT')
    review = _read(summary['verification_ref'], root / 'reports')
    _require(review.get('status') == 'PASSED' and review.get('scope') == 'ARTIFICIAL_STRESS_REPAIR'
        and review.get('source_replacements') == rows and review.get('added_runtime_ref') == summary['added_runtime_ref']
        and review.get('human_authorization_ref') == summary['human_authorization_ref'], 'EXACT_VALIDATION_REQUIRED')


class ArtificialStressRepairV1:
    def __init__(self, predecessor, reference, summary):
        self.predecessor, self.reference, self.summary = predecessor, reference, summary
        self.root = predecessor.root
        self.purpose = summary['purpose']
        self.boundary = summary['boundary']
        self.mapping = {row['path']: row for row in summary['source_replacements']}

    def proof(self):
        return {'manifest_ref': deepcopy(self.reference), 'repair_id': self.summary['identity']}

    def expected_digest(self, path, preceding):
        row = self.mapping.get(str(path))
        if row is None:
            return preceding
        _require(preceding == row['old_sha256']
            and _ref(row['archive_ref']['path'], self.root / 'reports') == row['archive_ref'], 'SOURCE_CHAIN_CHANGED')
        return row['new_sha256']

    def validate_sources(self):
        for row in self.mapping.values():
            _require(_ref(row['path'], self.root)['sha256'] == row['new_sha256'], 'INSTALLED_SOURCE_CHANGED')
        added = self.summary['added_runtime_ref']
        _require(_ref(added['path'], self.root) == added, 'ADDED_SOURCE_CHANGED')

    def validate_boundary(self, *, exact=False):
        from .account_version_continuation_v1 import _control
        old, frozen = self.predecessor.summary, self.summary
        account = Path(self.predecessor.job['root'])
        control = _control(self.predecessor.job)
        anchor = frozen['control']
        _require(0 < anchor['count'] <= len(control['events'])
            and control['events'][anchor['count'] - 1]['event_id'] == anchor['head']
            and control['events'][anchor['count'] - 1]['event'] == 'PAUSE', 'PAUSE_PREFIX_CHANGED')
        lines = _ledger_lines(self.predecessor)
        ledger = frozen['ledger']
        count = old['ledger']['line_count'] + 5
        _require(ledger['line_count'] == count and len(lines) >= count and _sha_lines(lines[:count]) == ledger['prefix_sha256'],
            'LEDGER_PREFIX_CHANGED')
        events = [json.loads(line) for line in lines[count - 4:count]]
        _require([event['event_type'] for event in events] == [
            'CAMPAIGN_EXECUTION_SEGMENT_DISPATCHED', 'CAMPAIGN_EXECUTION_SEGMENT_CHARGED',
            'RUN_TRIAL_COMPLETED', 'CAMPAIGN_OPERATION_SETTLED']
            and events[1]['event_id'] == ledger['failed_charge_event_id'], 'FAILED_LEDGER_SEQUENCE_CONFLICT')
        self._validate_failure(account, [event['payload'] for event in events])
        if exact:
            _require(len(lines) == count and control['paused'] and control['head'] == anchor['head']
                and not (account / (self.purpose + '_RECOVERY_START.json')).exists()
                and not (account / (self.purpose + '_RECOVERY_SETTLEMENT.json')).exists()
                and not (account / (self.purpose + '_RESULT.json')).exists(), 'EXACT_BOUNDARY_CHANGED')
            for name, row in old['accounts'].items():
                reference = frozen['checkpoint_refs'][name]
                cp = self.predecessor.job['items'][name]['backend_options']['checkpoint_path']
                _require(reference['path'] == cp and _read(reference, self.root)
                    == _read(row['checkpoint_archive'], self.root), 'CHECKPOINT_CHANGED')

    def _validate_failure(self, account, payloads):
        boundary, name = self.boundary, self.purpose
        number = boundary['segment_count']
        prefix = name + '_SEGMENT_' + str(number).zfill(6)
        refs = self.summary['failure_refs']
        _require(set(refs) == set(FAILURE_FILES)
            and all(refs[kind]['path'] == str(account / (prefix + '_' + kind + '.json')) for kind in FAILURE_FILES),
            'FAILED_FILES_PATH_CONFLICT')
        dispatch, version, worker, startup, failure, resource, charge = (
            _read(refs[kind], self.root) for kind in FAILURE_FILES)
        _require(all(value.get('dispatch_id') == dispatch['dispatch_id']
            for value in (version, worker, startup, failure, resource, charge))
            and version['segment_number'] == number
            and version.get('governance_source_revision') == self.predecessor.segment_proof(name, number)
            and failure.get('exception_type') == 'PermissionError' and failure.get('message') == 'RUNTIME_SOURCE_CHANGED'
            and failure.get('automatic_retry') is False
            and type(resource.get('returncode')) is int and resource['returncode'] == 1 and resource.get('timed_out') is False
            and startup.get('validation_complete') is False
            and [(stage.get('stage'), stage.get('completed')) for stage in startup['stages']] == [
                ('JOB_READ', True), ('VALIDATE_SOURCES', True), ('GOVERNANCE_SCOPE', True), ('STRATEGY_PREPARE', False)]
            and not validated_reference_path(account / (prefix + '_INPUT_ACCESS.json'), root=self.root,
                error_code='ARTIFICIAL_STRESS_REPAIR_INPUT_PATH_INVALID').exists(), 'FAILURE_NOT_BEFORE_INPUT')
        _require(charge['outcome'] == 'FAILED' and charge['basis'] == 'MEASURED_ACTIVE_WALL_SECONDS'
            and charge['evidence_identity'] == refs['RESOURCE']['sha256']
            and charge['seconds'] == charge['measured_seconds'] == resource['elapsed_wall_seconds']
            and 0 < charge['seconds'] <= dispatch['upper_bound_seconds']
            and self.predecessor.summary['accounts'][name]['charged_seconds'] + charge['seconds'] == boundary['charged_seconds'],
            'FAILED_CHARGE_CONFLICT')
        parent_dispatch, parent_charge, trial, settled = payloads
        _require(all(row['operation_id'] == boundary['operation_id'] for row in (parent_dispatch, parent_charge, settled))
            and parent_dispatch['segment_number'] == parent_charge['segment_number'] == number
            and parent_dispatch['dispatch_identity'] == dispatch['dispatch_identity'] == charge['dispatch_identity']
            and all(parent_charge[key] == charge[key] for key in CHARGE_FIELDS)
            and trial['trial_id'] == boundary['operation_id']
            and settled['outcome'] == 'FAILED' and settled['actual'] == boundary['parent_actual']
            and settled['evidence_identity'] == charge['charge_id'], 'PARENT_FAILURE_CONFLICT')
        _require(boundary['start_ref']['path'] == str(account / (name + '_START.json'))
            and boundary['settlement_ref']['path'] == str(account / (name + '_SETTLEMENT.json')), 'FAILED_ACCOUNT_PATH_CONFLICT')
        start = _read(boundary['start_ref'], self.root)
        settlement = _read(boundary['settlement_ref'], self.root)
        _require(start.get('kind') == name and start.get('counted_before_account_calculation') is True
            and all(settlement.get(key) == value for key, value in start.items())
            and settlement.get('completed') is False and settlement.get('result_sha256') is None
            and settlement.get('wall_seconds') == boundary['charged_seconds']
            and start.get('time_extension_proof') == boundary['time_extension_proof'], 'FAILED_SETTLEMENT_CONFLICT')

    def validate_return(self, *, for_dispatch=False):
        manifest_path, slot, marker = _paths(self.predecessor)
        expected = {'schema_version': VERSION, 'manifest_ref': self.reference}
        owner = Path(self.predecessor.summary['approval_store'])
        _require(str(manifest_path) == self.reference['path']
            and _read(_ref(slot, owner), owner) == expected
            and _read(_ref(marker, self.root), self.root) == expected, 'BINDING_CHANGED')
        manifest = _read(self.reference, self.root)
        _require(_identity(manifest) and manifest['summary'] == self.summary, 'MANIFEST_CHANGED')
        OwnerApprovalStoreV1(owner).require_json(manifest['approval_ref'], self.summary)
        if for_dispatch:
            now = datetime.now(timezone.utc)
            issued = datetime.fromisoformat(self.summary['issued_at'])
            expiry = datetime.fromisoformat(self.summary['expires_at'])
            _require(issued <= now < expiry, 'EXPIRED')
            events = [json.loads(line) for line in _ledger_lines(self.predecessor)[self.summary['ledger']['line_count']:]]
            _require(events and events[0]['event_type'] == EVENT
                and sum(row['event_type'] == EVENT for row in events) == 1, 'PARENT_ACTIVATION_REQUIRED')
            event = events[0]
            payload = event['payload']
            _require(set(payload) == {'proof', 'installed_at'} and payload['proof'] == self.proof()
                and event['event_id'] == stable_hash({key: event[key] for key in ('run_id', 'event_type', 'payload')})
                and issued <= datetime.fromisoformat(payload['installed_at']) <= now
                and datetime.fromisoformat(payload['installed_at']) < expiry, 'PARENT_ACTIVATION_CONFLICT')


def _binding(predecessor, recovery, summary):
    fields = {'schema_version', 'root', 'predecessor_ref', 'job_ref', 'issued_at', 'expires_at', 'purpose',
              'boundary', 'control', 'ledger', 'checkpoint_refs', 'failure_refs', 'human_authorization_ref', 'verification_ref',
              'source_replacements', 'added_runtime_ref', 'identity'}
    _require(set(summary) == fields and _identity(summary) and summary['schema_version'] == VERSION
        and summary['root'] == str(predecessor.root) and summary['predecessor_ref'] == predecessor.reference
        and summary['job_ref'] == predecessor.summary['job_ref'], 'SUMMARY_CONFLICT')
    human, renewal = _human(predecessor, summary['human_authorization_ref'])
    issued = datetime.fromisoformat(summary['issued_at'])
    prior_expiry = datetime.fromisoformat(predecessor.summary['expires_at'])
    expires = datetime.fromisoformat(summary['expires_at'])
    _require(issued.tzinfo is not None and expires.tzinfo is not None
        and datetime.fromisoformat(predecessor.summary['issued_at']) <= issued < expires, 'ISSUANCE_TIME_CONFLICT')
    if renewal is None:
        _require(summary['expires_at'] == predecessor.summary['expires_at'], 'SUMMARY_CONFLICT')
    else:
        _require(prior_expiry <= issued and datetime.fromisoformat(renewal['recorded_at']) <= issued
            and expires - issued == timedelta(seconds=WINDOW_SECONDS), 'RENEWAL_WINDOW_CONFLICT')
    name = summary['purpose']
    boundary = summary['boundary']
    previous = recovery['accounts'][name]
    _require(name == human['purpose'] and name != recovery['original_failed_account']
        and previous['status_at_install'] == 'RESERVED' and boundary['status_at_install'] == 'FAILED'
        and boundary['operation_id'] == previous['operation_id']
        and boundary['time_extension_proof'] == previous['time_extension_proof']
        and boundary['segment_count'] == human['failed_segment_number']
        and boundary['next_segment_number'] == human['next_segment_number']
        and type(boundary['remaining_reserved_seconds']) is int
        and 0 < boundary['remaining_reserved_seconds'] <= human['max_remaining_seconds']
        and boundary['parent_actual']['account_jobs'] == 1
        and boundary['parent_actual']['wall_seconds'] == math.ceil(boundary['charged_seconds'])
        and boundary['remaining_reserved_seconds'] + boundary['parent_actual']['wall_seconds'] == 72000,
        'ACCOUNT_BOUNDARY_CONFLICT')
    checkpoints = summary['checkpoint_refs']
    _require(set(checkpoints) == set(predecessor.summary['accounts']) and all(
        row['path'] == predecessor.job['items'][name]['backend_options']['checkpoint_path']
        for name, row in checkpoints.items()), 'CHECKPOINT_PATH_CONFLICT')
    _source_binding(predecessor, recovery, summary, renewal)


def read_installed_repair(predecessor, recovery):
    paths = _paths(predecessor)
    if not any(path.exists() for path in paths):
        return None
    reference = _ref(paths[0], predecessor.root)
    manifest = _read(reference, predecessor.root)
    _require(set(manifest) == {'schema_version', 'summary', 'approval_ref', 'identity'}
        and manifest['schema_version'] == VERSION and _identity(manifest), 'MANIFEST_INVALID')
    _binding(predecessor, recovery, manifest['summary'])
    return ArtificialStressRepairV1(predecessor, reference, manifest['summary'])


def prepare(context, *, replacements, added_runtime_ref, verification_ref, human_authorization_ref, issued_at=None):
    context.validate(for_dispatch=False)
    predecessor = context.recovery.governance_revision
    _require(predecessor.summary['schema_version'] == 'OWNER_GOVERNANCE_SOURCE_CONTINUATION_V2'
        and not any(path.exists() for path in _paths(predecessor)), 'SINGLE_UNUSED_REPAIR_REQUIRED')
    human, renewal = _human(predecessor, human_authorization_ref)
    _validate_predecessor(predecessor, renewal)
    name = human['purpose']
    recovery = context.recovery.summary_value
    account = Path(context.job['root'])
    state = context.governance.segment_status(name)
    number = human['failed_segment_number']
    _require(state['pending'] is None and len(state['segments']) == number
        and state['segments'][-1]['charge']['outcome'] == 'FAILED', 'FAILED_SEGMENT_REQUIRED')
    view = context.campaign.peek_status()
    op = view['operations'][predecessor.summary['operation_ids'][name]]
    context._parent_prefix(state, op, 0)
    from .account_version_continuation_v1 import _control
    control = _control(context.job)
    lines = _ledger_lines(predecessor)
    prefix = name + '_SEGMENT_' + str(number).zfill(6)
    refs = {kind: _ref(account / (prefix + '_' + kind + '.json'), context.root) for kind in FAILURE_FILES}
    checkpoints = {name: _ref(item['backend_options']['checkpoint_path'], context.root)
        for name, item in context.job['items'].items()}
    boundary = {'operation_id': op['operation_id'], 'status_at_install': 'FAILED', 'segment_count': number,
        'next_segment_number': number + 1, 'charged_seconds': state['charged_seconds'], 'parent_actual': deepcopy(op['actual']),
        'remaining_reserved_seconds': 72000 - op['actual']['wall_seconds'],
        'time_extension_proof': state['time_extension_proof'],
        'start_ref': _ref(account / (name + '_START.json'), context.root),
        'settlement_ref': _ref(account / (name + '_SETTLEMENT.json'), context.root)}
    issued = datetime.now(timezone.utc) if issued_at is None else datetime.fromisoformat(issued_at)
    _require(issued.tzinfo is not None and issued <= datetime.now(timezone.utc), 'ISSUANCE_IN_FUTURE')
    expires_at = predecessor.summary['expires_at']
    if renewal is not None:
        expires_at = (issued + timedelta(seconds=WINDOW_SECONDS)).isoformat()
    summary = {'schema_version': VERSION, 'root': str(context.root), 'predecessor_ref': predecessor.reference,
        'job_ref': predecessor.summary['job_ref'], 'issued_at': issued.isoformat(), 'expires_at': expires_at,
        'purpose': name, 'boundary': boundary, 'control': {'head': control['head'], 'count': len(control['events'])},
        'ledger': {'line_count': len(lines), 'prefix_sha256': _sha_lines(lines),
                   'failed_charge_event_id': json.loads(lines[-3])['event_id']},
        'checkpoint_refs': checkpoints, 'failure_refs': refs, 'human_authorization_ref': deepcopy(human_authorization_ref),
        'verification_ref': deepcopy(verification_ref), 'source_replacements': deepcopy(replacements),
        'added_runtime_ref': deepcopy(added_runtime_ref)}
    summary['identity'] = stable_hash(summary)
    _binding(predecessor, recovery, summary)
    repair = ArtificialStressRepairV1(predecessor, None, summary)
    repair.validate_boundary(exact=True)
    validate_parent_boundary(predecessor, repair, view['authorization'], view['operations'], view.get('revoked', False))
    _require(datetime.now(timezone.utc) < datetime.fromisoformat(expires_at), 'EXPIRED')
    return summary


def install(context, summary, approval_ref, *, added_runtime_candidate_ref):
    from .account_version_continuation_v1 import AccountVersionContinuationV1
    from .governance_source_revision_v1 import _install_bytes
    _require(type(context) is AccountVersionContinuationV1, 'SIGNED_CONTEXT_REQUIRED')
    root, job = context.root, deepcopy(context.job)
    job_ref = summary['job_ref']
    account = Path(job['root'])
    _require(account == Path(job_ref['path']).parent and job == _read(job_ref, root), 'INSTALL_JOB_CHANGED')
    context.validate(for_dispatch=False)
    _require(context.root == root and context.job == job, 'INSTALL_JOB_CHANGED')
    with ObjectiveMutationLock.for_resource(account / 'LONG_EXECUTION.lock'):
        with ObjectiveMutationLock.for_resource(account / 'CONTROL.lock'):
            _require(context.root == root and context.job == job
                and _read(job_ref, root) == job, 'INSTALL_JOB_CHANGED')
            predecessor = context.recovery.governance_revision
            owner = OwnerApprovalStoreV1(predecessor.summary['approval_store'])
            owner.require_json(approval_ref, summary)
            fresh = prepare(context, replacements=summary['source_replacements'],
                added_runtime_ref=summary['added_runtime_ref'], verification_ref=summary['verification_ref'],
                human_authorization_ref=summary['human_authorization_ref'], issued_at=summary['issued_at'])
            _require(context.root == root and context.job == job, 'INSTALL_JOB_CHANGED')
            _require(fresh == summary, 'INSTALL_BOUNDARY_CHANGED')
            added = summary['added_runtime_ref']
            _require(_ref(added_runtime_candidate_ref['path'], root / 'reports') == added_runtime_candidate_ref
                and added_runtime_candidate_ref['sha256'] == added['sha256'] and not Path(added['path']).exists(), 'ADDED_SOURCE_CONFLICT')
            data = [(added['path'], read_file_bytes(added_runtime_candidate_ref['path'], root=root / 'reports',
                error_code='ARTIFICIAL_STRESS_REPAIR_CANDIDATE_CHANGED'), added['sha256'])]
            for row in summary['source_replacements']:
                _require(_ref(row['path'], root)['sha256'] == row['old_sha256'], 'INSTALL_SOURCE_CHANGED')
                data.append((row['path'], read_file_bytes(row['candidate_ref']['path'], root=root / 'reports',
                    error_code='ARTIFICIAL_STRESS_REPAIR_CANDIDATE_CHANGED'), row['new_sha256']))
            _require(context.root == root and context.job == job and _read(job_ref, root) == job, 'INSTALL_JOB_CHANGED')
            _, renewal = _human(predecessor, summary['human_authorization_ref'])
            _validate_predecessor(predecessor, renewal)
            owner.require_json(approval_ref, summary)
            _require(datetime.now(timezone.utc) < datetime.fromisoformat(summary['expires_at']), 'EXPIRED')
            for path, content, digest in data:
                _install_bytes(path, content, digest, root)
            owner.require_json(approval_ref, summary)
            _require(datetime.now(timezone.utc) < datetime.fromisoformat(summary['expires_at']), 'EXPIRED')
            path, slot, marker = _paths(predecessor)
            manifest = {'schema_version': VERSION, 'summary': summary, 'approval_ref': approval_ref}
            manifest['identity'] = stable_hash(manifest)
            _write_once(path, manifest)
            reference = _ref(path, root)
            binding = {'schema_version': VERSION, 'manifest_ref': reference}
            _write_once(slot, binding)
            _write_once(marker, binding)
            return reference


def resolve_repair(root, reference, authorization_id, objective_id):
    manifest = _read(reference, root)
    predecessor = resolve_continuation(root, manifest['summary']['predecessor_ref'], authorization_id, objective_id)
    repair = predecessor.repair
    _require(repair is not None and repair.reference == reference, 'CANONICAL_REPAIR_REQUIRED')
    return predecessor, repair


def validate_parent_boundary(predecessor, repair, authorization, operations, revoked):
    from .run_budget import effective_execution_status, effective_execution_total_seconds, effective_operation_allocation
    boundary = repair.boundary
    op = operations.get(boundary['operation_id'], {})
    reserve = boundary['remaining_reserved_seconds']
    expected = predecessor.summary
    _require(not revoked and authorization['resource_limits']['wall_seconds'] == expected['global_wall_seconds']
        and not any(item.get('active_segment') is not None or effective_execution_status(item) == 'UNKNOWN'
            for item in operations.values())
        and all(effective_execution_status(operations[op_id]) == 'COMPLETED'
            for name, op_id in expected['operation_ids'].items() if name != repair.purpose)
        and op.get('kind') == 'ACCOUNT' and op.get('stage') == 'EXPLORATION' and op.get('status') == 'FAILED'
        and op.get('outcome') == 'FAILED' and op.get('failed_account_recovery') is None
        and not op.get('resource_overrun') and op.get('actual') == boundary['parent_actual']
        and op.get('governance_continuation', {}).get('proof') == predecessor.proof()
        and op['governance_continuation']['execution_expires_at'] == expected['expires_at']
        and op['execution_profile'] == expected['resource_profile'] and effective_execution_total_seconds(op) == 72000
        and op['account_purpose_time_extension'] == boundary['time_extension_proof']
        and len(op.get('segments', [])) == boundary['segment_count'] and op['segments'][-1]['outcome'] == 'FAILED'
        and op['active_wall_seconds'] == boundary['charged_seconds'], 'PARENT_BOUNDARY_CONFLICT')
    _require(sum(effective_operation_allocation(item)['wall_seconds'] for item in operations.values()) + reserve
        <= authorization['resource_limits']['wall_seconds'], 'GLOBAL_RESERVATION_EXCEEDED')
    if authorization.get('scope_policy'):
        from .campaign_scope_v1 import stage_remaining
        _require(stage_remaining({'authorization': authorization, 'operations': operations})['EXPLORATION']['wall_seconds']
            >= reserve, 'STAGE_RESERVATION_EXCEEDED')
    return op


def apply_parent_repair(authorization, operations, item, revoked, *, event_index):
    from .budget import BudgetLedgerMismatchError
    try:
        _require(authorization is not None and set(item) == {'proof', 'installed_at'}, 'EVENT_INVALID')
        predecessor, repair = resolve_repair(Path(authorization['root']), item['proof']['manifest_ref'],
            authorization['authorization_id'], authorization['objective_id'])
        installed = datetime.fromisoformat(item['installed_at'])
        _require(item['proof'] == repair.proof() and event_index == repair.summary['ledger']['line_count']
            and installed.tzinfo is not None and datetime.fromisoformat(repair.summary['issued_at'])
                <= installed < datetime.fromisoformat(repair.summary['expires_at']), 'EVENT_BOUNDARY_CONFLICT')
        op = validate_parent_boundary(predecessor, repair, authorization, operations, revoked)
        expiry = repair.summary['expires_at']
        continuations = []
        if expiry != predecessor.summary['expires_at']:
            for operation_id in predecessor.summary['operation_ids'].values():
                continuation = operations[operation_id]['governance_continuation']
                _require(continuation['proof'] == predecessor.proof()
                    and continuation['execution_expires_at'] == predecessor.summary['expires_at']
                    and 'repair_expires_at' not in continuation, 'RENEWAL_PARENT_CONFLICT')
                continuations.append(continuation)
        boundary = repair.boundary
        op['failed_account_recovery'] = {
            'proof': deepcopy(op['failed_account_recovery_proof']), 'status': 'RUNNING', 'boundary': deepcopy(boundary),
            'base_actual': deepcopy(op['actual']), 'base_charged_seconds': boundary['charged_seconds'],
            'remaining_reserved_seconds': boundary['remaining_reserved_seconds'],
            'charge_ceiling_seconds': boundary['charged_seconds'] + boundary['remaining_reserved_seconds'],
            'repair_installed_at': item['installed_at'],
        }
        # BASE 已结清。双账户投影同一个时段，仅供 STRESS 接续和原任务的核验／报告使用。
        for continuation in continuations:
            continuation['repair_expires_at'] = expiry
    except (PermissionError, ValueError, TypeError, KeyError, OSError) as exc:
        raise BudgetLedgerMismatchError('CAMPAIGN_ARTIFICIAL_STRESS_REPAIR_PROOF_INVALID') from exc


def validate_parent_submission(authorization, item):
    from .budget import BudgetLedgerMismatchError
    try:
        predecessor, repair = resolve_repair(Path(authorization['root']), item['proof']['manifest_ref'],
            authorization['authorization_id'], authorization['objective_id'])
        _require(item['proof'] == repair.proof(), 'EVENT_PROOF_CONFLICT')
        repair.validate_boundary(exact=True)
        predecessor.validate_return(for_dispatch=False)
        repair.validate_return(for_dispatch=False)
        now = datetime.now(timezone.utc)
        _require(datetime.fromisoformat(repair.summary['issued_at']) <= datetime.fromisoformat(item['installed_at'])
            <= now < datetime.fromisoformat(repair.summary['expires_at']), 'SUBMISSION_EXPIRED')
    except (PermissionError, ValueError, TypeError, KeyError, OSError) as exc:
        raise BudgetLedgerMismatchError('CAMPAIGN_ARTIFICIAL_STRESS_REPAIR_SUBMISSION_INVALID') from exc
