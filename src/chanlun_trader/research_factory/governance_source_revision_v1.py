"""段结清后的单次治理读取器接续；不更换任务、经济计划、额度或期限。"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat

from chanlun_trader.account_startup_boundary_v1 import startup_checkpoint
from .campaign_scope_v1 import OwnerApprovalStoreV1, _write_once
from .account_purpose_time_extension_v1 import file_sha256, read_file_bytes, read_pinned_json
from .common import stable_hash
from .mutation_boundary import ObjectiveMutationLock
from .secure_file_reference_v1 import checked_file_path, validated_reference_path


VERSION = 'OWNER_GOVERNANCE_SOURCE_REVISION_V1'
MODULE_PATH = 'src/chanlun_trader/research_factory/governance_source_revision_v1.py'
READER_PATHS = frozenset('src/chanlun_trader/research_factory/' + name for name in (
    'campaign_scope_v1.py', 'account_purpose_time_extension_v1.py',
    'failed_account_recovery_v1.py', 'account_version_continuation_v1.py'))


def _require(condition, reason):
    if not condition:
        raise PermissionError('GOVERNANCE_SOURCE_REVISION_' + reason)


def _ref(path, root):
    return {'path': str(Path(path)), 'sha256': file_sha256(path, root=root,
        error_code='GOVERNANCE_SOURCE_REVISION_REFERENCE_CHANGED')}


def _read(reference, root):
    return read_pinned_json(reference, root=root, error_code='GOVERNANCE_SOURCE_REVISION_REFERENCE_CHANGED')


def _identity(value):
    return isinstance(value, dict) and value.get('identity') == _json_hash(
        {key: item for key, item in value.items() if key != 'identity'})


def _json_hash(value):
    # 已解析 JSON 树仍逐次完整编码/哈希；安装前也能由原模块装配此协议。
    startup_checkpoint('GOVERNANCE_SOURCE_REVISION_JSON_HASH')
    result = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), default=str).encode('utf-8')).hexdigest()
    startup_checkpoint('GOVERNANCE_SOURCE_REVISION_JSON_HASH_COMPLETE')
    return result


def _install_bytes(path, data, digest, root):
    """打开后验证同一普通文件，核验完成前不截断目标；失败不继续派发。"""
    _require(hashlib.sha256(data).hexdigest() == digest, 'INSTALL_BYTES_CHANGED')
    target = validated_reference_path(path, root=root, error_code='GOVERNANCE_SOURCE_REVISION_PATH_INVALID')
    before = target.lstat() if target.exists() else None
    flags = os.O_WRONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    if before is None:
        flags |= os.O_CREAT | os.O_EXCL
    descriptor = os.open(str(target), flags, 0o600)
    try:
        opened = os.fstat(descriptor)
        checked_file_path(target, root=root, error_code='GOVERNANCE_SOURCE_REVISION_PATH_INVALID')
        after = target.lstat()
        _require(stat.S_ISREG(opened.st_mode) and (opened.st_dev, opened.st_ino) == (after.st_dev, after.st_ino)
            and (before is None or (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino)),
            'INSTALL_OBJECT_CHANGED')
        with os.fdopen(descriptor, 'wb') as stream:
            descriptor = None
            stream.write(data)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor is not None:
            os.close(descriptor)
    _require(_ref(target, root)['sha256'] == digest, 'INSTALL_WRITE_CHANGED')


def _paths(root, recovery):
    # 保留名称使旧读取器按原“恰一 manifest”规则拒绝新来源，不能回退旧许可派发。
    folder = Path(recovery['original']['task']['path']).parent / 'failed-account-recoveries' / 'governance-source-revision'
    key = stable_hash([VERSION, str(root), recovery['task_id'], recovery['recovery_id']])
    slot = Path(recovery['approval_store']) / 'governance-source-revision-bindings' / (key + '.json')
    return folder, slot


def _legacy_marker(root, recovery):
    return Path(root) / 'reports' / 'research_campaigns' / stable_hash(recovery['authorization_id']) / (
        'failed-account-recovery-slots/GOVERNANCE_SOURCE_REVISION.json')


def _checkpoint_prefix(root, job, name, boundary, *, exact=False, verify_days=False):
    """fresh 核 CP 身份和原日链；安装、派发时还逐文件核原日字节。"""
    from .account_purpose_time_extension_v1 import _checkpoint
    archived = _read(boundary['checkpoint_archive'], root)
    path = Path(job['items'][name]['backend_options']['checkpoint_path'])
    current = _read(_ref(path, root), root)
    old_days = archived['artifacts']['days']
    _require(_identity_state(archived) and _identity_state(current)
        and len(old_days) == boundary['days'] and archived['last_day'] == boundary['last_day']
        and current['execution_identity'] == archived['execution_identity']
        and current['artifacts']['days'][:len(old_days)] == old_days,
        'CHECKPOINT_PREFIX_CHANGED')
    if exact or boundary['completed']:
        _require(current == archived, 'CHECKPOINT_BOUNDARY_CHANGED')
    if verify_days:
        _checkpoint(root, job, name)


def _identity_state(value):
    return (value.get('state_identity') == _json_hash({k: v for k, v in value.items() if k != 'state_identity'})
        and value.get('artifacts', {}).get('manifest_identity') == _json_hash(
            {k: v for k, v in value.get('artifacts', {}).items() if k != 'manifest_identity'}))


class GovernanceSourceRevisionV1:
    """规范单槽与原 Owner 同时绑定；每次 read 都重新验证，不缓存许可。"""

    def __init__(self, root, reference, summary):
        self.root = Path(root)
        self.reference = deepcopy(reference)
        startup_checkpoint('GOVERNANCE_SOURCE_REVISION_JSON_COPY')
        self.summary = json.loads(json.dumps(summary, ensure_ascii=False))
        startup_checkpoint('GOVERNANCE_SOURCE_REVISION_JSON_COPY_COMPLETE')
        self.mapping = {row['path']: row for row in summary['source_replacements']}

    @classmethod
    def read(cls, root, recovery_reference, recovery, job, expires_at, *, for_dispatch=False):
        root = Path(root)
        folder, slot = _paths(root, recovery)
        manifest_path = folder / 'MANIFEST.json'
        marker = _legacy_marker(root, recovery)
        for path, boundary in ((manifest_path, root), (slot, Path(recovery['approval_store'])), (marker, root)):
            validated_reference_path(path, root=boundary, error_code='GOVERNANCE_SOURCE_REVISION_PATH_INVALID')
        if not slot.exists() and not manifest_path.exists() and not marker.exists():
            return None
        binding = _read(_ref(slot, Path(recovery['approval_store'])), Path(recovery['approval_store']))
        reference = _ref(manifest_path, root)
        _require(binding == {'schema_version': VERSION, 'manifest_ref': reference}, 'SLOT_CONFLICT')
        _require(_read(_ref(marker, root), root) == binding, 'LEGACY_REJECTION_MARKER_CONFLICT')
        manifest = _read(reference, root)
        _require(set(manifest) == {'schema_version', 'summary', 'approval_ref', 'identity'}
            and manifest['schema_version'] == VERSION and _identity(manifest), 'MANIFEST_INVALID')
        summary = manifest['summary']
        cls._summary_binding(root, summary, recovery_reference, recovery, job, expires_at)
        owner = OwnerApprovalStoreV1(recovery['approval_store'])
        owner.require_json(manifest['approval_ref'], summary)
        context = cls(root, reference, summary)
        context.validate_sources()
        context.validate_boundary(job, for_dispatch=for_dispatch)
        context.validate_return(for_dispatch=for_dispatch)
        startup_checkpoint('GOVERNANCE_SOURCE_REVISION_RETURN')
        return context

    def validate_return(self, *, for_dispatch=False):
        """最外层原件复核完成后，再核新批准、规范槽及绝对期限。"""
        manifest = _read(self.reference, self.root)
        _require(manifest['summary'] == self.summary and _identity(manifest), 'RETURN_MANIFEST_CHANGED')
        recovery = _read(self.summary['recovery_ref'], self.root)['summary']
        _, slot = _paths(self.root, recovery)
        expected = {'schema_version': VERSION, 'manifest_ref': self.reference}
        _require(_read(_ref(slot, Path(recovery['approval_store'])), Path(recovery['approval_store'])) == expected
            and _read(_ref(_legacy_marker(self.root, recovery), self.root), self.root) == expected, 'RETURN_SLOT_CHANGED')
        OwnerApprovalStoreV1(self.summary['approval_store']).require_json(manifest['approval_ref'], manifest['summary'])
        if for_dispatch:
            _require(datetime.now(timezone.utc) < datetime.fromisoformat(self.summary['expires_at']), 'EXPIRED')
        startup_checkpoint('GOVERNANCE_SOURCE_REVISION_OWNER_RETURN')

    @staticmethod
    def _summary_binding(root, summary, reference, recovery, job, expires_at):
        _require(_identity(summary) and summary.get('schema_version') == VERSION
            and summary.get('root') == str(root) and summary.get('recovery_ref') == reference
            and summary.get('job_ref') == recovery['original']['job']
            and summary.get('task_ref') == recovery['original']['task']
            and summary.get('approval_store') == recovery['approval_store']
            and summary.get('expires_at') == expires_at
            and summary.get('resource_profile') == job['resources']
            and summary.get('global_wall_seconds') == recovery['global_wall_seconds']
            and summary.get('maximum_revisions') == 1
            and summary.get('grant_delta') == summary.get('new_task_delta') == summary.get('new_account_delta') == 0,
            'SUMMARY_BINDING_CONFLICT')
        _require(_read(summary['job_ref'], root) == job
            and summary['economic_sources'] == recovery['economic_sources']
            and summary.get('operation_ids') == {name: row['operation_id'] for name, row in recovery['accounts'].items()},
            'ORIGINAL_CONFLICT')
        expected_ledger = root / 'reports' / 'research_campaigns' / stable_hash(recovery['authorization_id']) / 'run_budget_events.jsonl'
        _require(summary['ledger']['path'] == str(expected_ledger)
            and type(summary['ledger']['line_count']) is int and summary['ledger']['line_count'] > 0
            and type(summary['control']['count']) is int and summary['control']['count'] > 0, 'BOUNDARY_PATH_CONFLICT')
        human = _read(summary['human_authorization_ref'], root / 'reports')
        _require(human.get('schema_version') == 'HUMAN_GOVERNANCE_SOURCE_REVISION_AUTHORIZATION_V1'
            and human.get('root') == str(root) and human.get('task_ref') == summary['task_ref']
            and human.get('recovery_ref') == reference and human.get('allow_checkpoint_continuation') is True
            and human.get('allow_budget_or_deadline_change') is False, 'HUMAN_AUTHORIZATION_CONFLICT')
        evidence = _read(summary['verification_ref'], root / 'reports')
        _require(evidence.get('status') == 'PASSED' and evidence.get('source_replacements') == summary['source_replacements']
            and evidence.get('added_runtime_ref') == summary['added_runtime_ref'], 'EXACT_VALIDATION_REQUIRED')
        # 只允许本轮四个读取器；它们不得进入任何已冻结经济/运行计划的依赖集合。
        from .account_purpose_time_extension_v1 import _source_inventory
        forbidden = set(_source_inventory(recovery['successor_plans'])) | {r['path'] for r in recovery['economic_sources']}
        inventory = {r['path']: r['sha256'] for r in recovery['runtime_sources']}
        rows = summary['source_replacements']
        _require(isinstance(rows, list) and rows and len({r['path'] for r in rows}) == len(rows), 'REPLACEMENTS_INVALID')
        for row in rows:
            path = validated_reference_path(row['path'], root=root, error_code='GOVERNANCE_SOURCE_REVISION_PATH_INVALID')
            _require(set(row) == {'path', 'old_sha256', 'new_sha256', 'archive_ref', 'candidate_ref'}
                and path.relative_to(root).as_posix() in READER_PATHS and row['path'] not in forbidden
                and inventory.get(row['path']) == row['old_sha256'] and row['old_sha256'] != row['new_sha256']
                and row['archive_ref']['sha256'] == row['old_sha256']
                and row['candidate_ref']['sha256'] == row['new_sha256'], 'SOURCE_ALLOWLIST_CONFLICT')
        _require(summary['added_runtime_ref']['path'] == str(root / MODULE_PATH), 'ADDED_RUNTIME_CONFLICT')
        _require(set(summary['accounts']) == set(job['plans']), 'ACCOUNT_SCOPE_CONFLICT')
        _require(sum(not r['completed'] for r in summary['accounts'].values()) == 1, 'ONE_RUNNING_ACCOUNT_REQUIRED')
        for name, row in summary['accounts'].items():
            _require(type(row['segment_count']) is int and row['segment_count'] > 0
                and row['next_segment_number'] == row['segment_count'] + 1
                and type(row['completed']) is bool and 0 < row['days'] <= 504
                and row['completed'] is (row['days'] == 504), 'ACCOUNT_BOUNDARY_INVALID')

    def expected_digest(self, path, predecessor_digest):
        row = self.mapping.get(str(path))
        if row is None:
            return predecessor_digest
        _require(row['old_sha256'] == predecessor_digest, 'SOURCE_CHAIN_CONFLICT')
        _require(_ref(row['archive_ref']['path'], self.root / 'reports') == row['archive_ref'], 'ARCHIVE_CHANGED')
        return row['new_sha256']

    def current_runtime_refs(self, historical_refs):
        return [{'path': row['path'], 'sha256': self.expected_digest(row['path'], row['sha256'])}
            for row in historical_refs]

    def validate_sources(self):
        for row in self.mapping.values():
            startup_checkpoint('GOVERNANCE_SOURCE_REVISION_SOURCE')
            _require(_ref(row['path'], self.root)['sha256'] == self.expected_digest(row['path'], row['old_sha256']),
                'SOURCE_CHANGED')
            _require(_ref(row['candidate_ref']['path'], self.root / 'reports') == row['candidate_ref'], 'CANDIDATE_CHANGED')
        _require(_ref(self.summary['added_runtime_ref']['path'], self.root) == self.summary['added_runtime_ref'],
            'RUNTIME_CHANGED')

    def predecessor_capabilities(self, current):
        from .account_version_capability_bridge_v1 import _project_snapshot
        return _project_snapshot(current, list(self.mapping.values()))

    def validate_boundary(self, job, *, for_dispatch=False, exact=False):
        from .account_version_continuation_v1 import _control
        control = _control(job)
        frozen = self.summary['control']
        _require(len(control['events']) >= frozen['count']
            and control['events'][frozen['count'] - 1]['event_id'] == frozen['head']
            and control['events'][frozen['count'] - 1]['event'] == 'PAUSE', 'CONTROL_PREFIX_CHANGED')
        if exact:
            _require(control['paused'] and control['head'] == frozen['head'], 'PAUSED_BOUNDARY_REQUIRED')
        ledger = self.summary['ledger']
        raw = read_file_bytes(ledger['path'], root=self.root, error_code='GOVERNANCE_SOURCE_REVISION_LEDGER_CHANGED')
        lines = raw.splitlines(keepends=True)
        _require(len(lines) >= ledger['line_count'] and hashlib.sha256(b''.join(lines[:ledger['line_count']])).hexdigest()
            == ledger['prefix_sha256'], 'LEDGER_PREFIX_CHANGED')
        if exact:
            _require(len(lines) == ledger['line_count'], 'LEDGER_BOUNDARY_CHANGED')
        frozen_events = [json.loads(line) for line in lines[:ledger['line_count']]]
        for line in lines[ledger['line_count']:]:
            value = json.loads(line)
            _require(value.get('event_type') not in ('CAMPAIGN_REVOKED', 'CAMPAIGN_GRANT_ADDED',
                'CAMPAIGN_OPERATION_UNKNOWN', 'CAMPAIGN_FAILED_ACCOUNT_RECOVERY_UNKNOWN'), 'REVOKED_OR_UNKNOWN')
        for name, boundary in self.summary['accounts'].items():
            operation_id = self.summary['operation_ids'][name]
            dispatches = [row['payload'] for row in frozen_events if row['event_type'] == 'CAMPAIGN_EXECUTION_SEGMENT_DISPATCHED'
                and row['payload'].get('operation_id') == operation_id]
            charges = [row['payload'] for row in frozen_events if row['event_type'] == 'CAMPAIGN_EXECUTION_SEGMENT_CHARGED'
                and row['payload'].get('operation_id') == operation_id]
            numbers = list(range(1, boundary['segment_count'] + 1))
            _require([row['segment_number'] for row in dispatches] == [row['segment_number'] for row in charges] == numbers
                and abs(sum(row['seconds'] for row in charges) - boundary['charged_seconds']) < 1e-7
                and (charges[-1]['outcome'] == 'COMPLETED') is boundary['completed'], 'FEE_BOUNDARY_CONFLICT')
            _checkpoint_prefix(self.root, job, name, boundary, exact=exact, verify_days=for_dispatch or exact)
            if for_dispatch or exact:
                for reference in boundary['immutable_refs']:
                    _require(_ref(reference['path'], self.root) == reference, 'ORIGINAL_EVIDENCE_CHANGED')

    def segment_proof(self, name, number):
        boundary = self.summary['accounts'][name]
        if number <= boundary['segment_count']:
            return None
        _require(not boundary['completed'], 'COMPLETED_ACCOUNT_NO_DISPATCH')
        return {'manifest_ref': deepcopy(self.reference), 'revision_id': self.summary['identity'],
            'purpose': name, 'first_segment_number': boundary['next_segment_number']}

    @classmethod
    def prepare(cls, context, *, replacements, added_runtime_ref, verification_ref, human_authorization_ref):
        """使用尚未换源的完整 fresh 原上下文捕获边界；只生成批准摘要。"""
        from .account_version_continuation_v1 import AccountVersionContinuationV1, _control
        from .account_purpose_time_extension_v1 import _checkpoint
        from .run_budget import effective_execution_status
        _require(type(context) is AccountVersionContinuationV1, 'CONTEXT_REQUIRED')
        context.validate()
        _require(context.recovery_installed and getattr(context.recovery, 'governance_revision', None) is None,
            'INSTALLED_PREDECESSOR_REQUIRED')
        root, recovery = context.root, context.recovery.summary_value
        folder, slot = _paths(root, recovery)
        _require(not slot.exists() and not (folder / 'MANIFEST.json').exists(), 'ALREADY_INSTALLED')
        control = _control(context.job)
        _require(control['paused'] and control['events'], 'PAUSED_BOUNDARY_REQUIRED')
        view = context.campaign.peek_status()
        _require(not view.get('revoked') and not any(effective_execution_status(op) == 'UNKNOWN'
            or op.get('active_segment') is not None for op in view['operations'].values()), 'ACTIVE_OR_UNKNOWN')
        accounts = {}
        for name in context.job['plans']:
            state = context.governance.segment_status(name)
            operation = view['operations'][recovery['accounts'][name]['operation_id']]
            completed = effective_execution_status(operation) == 'COMPLETED'
            _require(state['pending'] is None and all(row['charge'] is not None for row in state['segments'])
                and effective_execution_status(operation) in ('RUNNING', 'COMPLETED'), 'UNSETTLED_BOUNDARY')
            context._parent_prefix(state, operation, 0)
            cp = _checkpoint(root, context.job, name)
            _require(cp is not None, 'CHECKPOINT_REQUIRED')
            archived = folder / 'BOUNDARY' / (name + '_CHECKPOINT.json')
            _write_once(archived, _read(cp['reference'], root))
            references = [_ref(p, root) for p in sorted(Path(context.job['root']).glob(name + '_SEGMENT_*')) if p.is_file()]
            if completed:
                references += [_ref(p, root) for p in sorted(Path(context.job['root']).glob(name + '_*.json'))
                    if '_SEGMENT_' not in p.name and not p.name.endswith('_CHECKPOINT.json')]
            accounts[name] = {'segment_count': len(state['segments']), 'next_segment_number': len(state['segments']) + 1,
                'days': cp['processed_days'], 'last_day': cp['last_day'], 'completed': completed,
                'charged_seconds': state['charged_seconds'], 'checkpoint_archive': _ref(archived, root),
                'immutable_refs': references}
        ledger_path = context.campaign.budget_path.with_name('run_budget_events.jsonl')
        raw = read_file_bytes(ledger_path, root=root, error_code='GOVERNANCE_SOURCE_REVISION_LEDGER_CHANGED')
        value = {'schema_version': VERSION, 'root': str(root), 'recovery_ref': context.recovery.scope_reference,
            'job_ref': recovery['original']['job'], 'task_ref': recovery['original']['task'],
            'approval_store': recovery['approval_store'], 'expires_at': context.recovery.execution_expires_at,
            'resource_profile': context.job['resources'], 'global_wall_seconds': recovery['global_wall_seconds'],
            'economic_sources': recovery['economic_sources'], 'maximum_revisions': 1,
            'operation_ids': {name: row['operation_id'] for name, row in recovery['accounts'].items()},
            'grant_delta': 0, 'new_task_delta': 0, 'new_account_delta': 0, 'accounts': accounts,
            'control': {'head': control['head'], 'count': len(control['events'])},
            'ledger': {'path': str(ledger_path), 'line_count': len(raw.splitlines()), 'prefix_sha256': hashlib.sha256(raw).hexdigest()},
            'source_replacements': deepcopy(replacements), 'added_runtime_ref': deepcopy(added_runtime_ref),
            'verification_ref': deepcopy(verification_ref), 'human_authorization_ref': deepcopy(human_authorization_ref)}
        value['identity'] = stable_hash(value)
        cls._summary_binding(root, value, context.recovery.scope_reference, recovery, context.job, context.recovery.execution_expires_at)
        return value

    @classmethod
    def install(cls, context, summary, approval_ref, *, added_runtime_candidate_ref):
        """独占结清边界换源；失败不派发，完成后必须由全新进程读取验证。"""
        from .secure_file_reference_v1 import read_file_bytes
        root = context.root
        folder, slot = _paths(root, context.recovery.summary_value)
        _require(not slot.exists() and not (folder / 'MANIFEST.json').exists(), 'ALREADY_INSTALLED')
        with ObjectiveMutationLock.for_resource(Path(context.job['root']) / 'LONG_EXECUTION.lock'):
            with ObjectiveMutationLock.for_resource(Path(context.job['root']) / 'CONTROL.lock'):
                owner = OwnerApprovalStoreV1(summary['approval_store'])
                owner.require(approval_ref, summary)
                _require(datetime.now(timezone.utc) < datetime.fromisoformat(summary['expires_at']), 'EXPIRED')
                fresh = cls.prepare(context, replacements=summary['source_replacements'],
                    added_runtime_ref=summary['added_runtime_ref'], verification_ref=summary['verification_ref'],
                    human_authorization_ref=summary['human_authorization_ref'])
                _require(fresh == summary, 'INSTALL_BOUNDARY_CHANGED')
                _require(datetime.now(timezone.utc) < datetime.fromisoformat(summary['expires_at']), 'EXPIRED')
                source_bytes = []
                for row in summary['source_replacements']:
                    _require(_ref(row['path'], root)['sha256'] == row['old_sha256']
                        and _ref(row['archive_ref']['path'], root / 'reports') == row['archive_ref']
                        and _ref(row['candidate_ref']['path'], root / 'reports') == row['candidate_ref'], 'INSTALL_SOURCE_CHANGED')
                    source_bytes.append((row['path'], read_file_bytes(row['candidate_ref']['path'], root=root / 'reports',
                        error_code='GOVERNANCE_SOURCE_REVISION_CANDIDATE_CHANGED'), row['new_sha256']))
                _require(_ref(added_runtime_candidate_ref['path'], root / 'reports') == added_runtime_candidate_ref
                    and added_runtime_candidate_ref['sha256'] == summary['added_runtime_ref']['sha256']
                    and not Path(summary['added_runtime_ref']['path']).exists(), 'RUNTIME_INSTALL_CONFLICT')
                source_bytes.append((summary['added_runtime_ref']['path'], read_file_bytes(added_runtime_candidate_ref['path'],
                    root=root / 'reports', error_code='GOVERNANCE_SOURCE_REVISION_CANDIDATE_CHANGED'), added_runtime_candidate_ref['sha256']))
                owner.require(approval_ref, summary)
                _require(datetime.now(timezone.utc) < datetime.fromisoformat(summary['expires_at']), 'EXPIRED')
                for path, data, digest in source_bytes:
                    _install_bytes(path, data, digest, root)
                owner.require(approval_ref, summary)
                _require(datetime.now(timezone.utc) < datetime.fromisoformat(summary['expires_at']), 'EXPIRED')
                record = {'schema_version': VERSION, 'summary': summary, 'approval_ref': approval_ref}
                record['identity'] = stable_hash(record)
                _write_once(folder / 'MANIFEST.json', record)
                reference = _ref(folder / 'MANIFEST.json', root)
                _write_once(slot, {'schema_version': VERSION, 'manifest_ref': reference})
                _write_once(_legacy_marker(root, context.recovery.summary_value), {'schema_version': VERSION, 'manifest_ref': reference})
                return reference
