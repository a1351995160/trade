"""只停止既有报告修复的后续派发；旧段在原账本结清，不杀进程。"""
from copy import deepcopy
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import json
import hashlib
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

from .campaign_scope_v1 import OwnerApprovalStoreV1, RESOURCES
from .common import stable_hash
from .exploration_governance import immutable
from .mutation_boundary import ObjectiveMutationLock
from .report_repair_protocol_v1 import (
    ReportRepairV1, _identity, _parent_mirror, _path, _read, _reference, _require,
)
from .research_campaign_v1 import ResearchCampaignV1
from .secure_file_reference_v1 import read_file_bytes
from .universe_execution_profile_v1 import segment_charge


SUMMARY_VERSION = 'OWNER_REPORT_REPAIR_STOP_SUMMARY_V2'
REQUEST_VERSION = 'REPORT_REPAIR_STOP_REQUEST_V2'
TERMINAL_VERSION = 'REPORT_REPAIR_STOP_TERMINAL_V2'
STOP_SOURCE_PATHS = (
    'src/chanlun_trader/research_factory/report_repair_stop_v2.py',
    'scripts/run_report_repair_stop_v2.py',
)
_TERMINAL_PROOFS = ContextVar('report_recovery_terminal_proofs', default=None)


def _terminal_proof_scope(function):
    """只覆盖一次纯验证；返回前重新核原件、Owner及原用途事件，不跨派发复用。"""
    @wraps(function)
    def validated(*args, **kwargs):
        if _TERMINAL_PROOFS.get() is not None:
            return function(*args, **kwargs)
        proofs = {}
        token = _TERMINAL_PROOFS.set(proofs)
        try:
            result = function(*args, **kwargs)
        finally:
            _TERMINAL_PROOFS.reset(token)
        for value, reader in proofs.values():
            fresh = reader()
            _require(fresh == value, 'STOP_TERMINAL_CHANGED_DURING_VALIDATION')
        return result
    return validated


def _within_terminal_proof(key, reader):
    """受限纯验证的依赖；每项出口完整重核，普通调用不会缓存。"""
    proofs = _TERMINAL_PROOFS.get()
    if proofs is None:
        return reader()
    if key not in proofs:
        _require(len(proofs) < 4, 'STOP_VALIDATION_SCOPE_LIMIT')
        proofs[key] = (reader(), reader)
    return deepcopy(proofs[key][0])


def _control(job_path, root):
    head, rows = None, []
    for path in sorted(job_path.parent.glob('CONTROL_*.json')):
        value = _read(_reference(path, root), root)
        _require(value.get('sequence') == len(rows) + 1 and value.get('previous_head') == head
                 and value.get('job_sha256') == _reference(job_path, root)['sha256']
                 and value.get('event') in ('PAUSE', 'RESUME') and _identity(value, 'event_id'),
                 'STOP_CONTROL_CHAIN_CONFLICT')
        rows.append(value)
        head = value['event_id']
    return rows


def _publish(path, value, root):
    """调用方持固定控制锁；读方只会看见完整 JSON，不会读到写入中前缀。"""
    path = _path(path, root)
    if path.exists():
        _require(_read(_reference(path, root), root) == value, 'STOP_IMMUTABLE_CONFLICT')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.stop-' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        # 所有正式 CONTROL 写者共享 CONTROL.lock，发布前再次拒绝冲突。
        _require(not path.exists(), 'STOP_IMMUTABLE_CONFLICT')
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _source_refs(context):
    summary = context.summary
    job = _read(summary['original']['job'], context.root)
    replacement = {item['path']: item['new_sha256'] for item in summary['source_replacements']}
    refs = {path: {'path': path, 'sha256': replacement.get(path, sha)}
            for path, sha in job['source_hashes'].items()}
    refs.update({item['path']: item for item in summary['runtime_sources']})
    # JOB source_hashes 还包括大行情/INPUT原件，禁止为了停派把这些重新整读或复制。
    code = [deepcopy(refs[key]) for key in sorted(refs) if Path(key).suffix == '.py']
    data = [deepcopy(refs[key]) for key in sorted(refs) if Path(key).suffix != '.py']
    return code, data


def _process_created_at(pid):
    """查询真实进程身份；不存在返回None，权限不足不能当成已经退出。"""
    _require(type(pid) is int and pid > 0, 'STOP_PROCESS_IDENTITY_UNAVAILABLE')
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4))
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            _require(ctypes.get_last_error() == 87, 'STOP_PROCESS_IDENTITY_UNAVAILABLE')
            return None
        try:
            created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
            exit_code = wintypes.DWORD()
            _require(kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                        ctypes.byref(system), ctypes.byref(user))
                     and kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)),
                     'STOP_PROCESS_IDENTITY_UNAVAILABLE')
            if exit_code.value != 259:
                return None
            ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
            # FILETIME是1601年起的100ns单位，整数运算避免本地时区/浮点舍入。
            seconds, remainder = divmod(ticks - 116444736000000000, 10000000)
            return datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=remainder // 10)
        finally:
            kernel.CloseHandle(handle)
    if sys.platform.startswith('linux'):
        _require(Path('/proc/self/stat').is_file(), 'STOP_PROCESS_IDENTITY_UNAVAILABLE')
        try:
            # stat的comm允许空格/括号；starttime是右括号后第20字段。
            record = (Path('/proc') / str(pid) / 'stat').read_text(encoding='utf-8')
        except FileNotFoundError:
            return None
        except OSError as error:
            raise PermissionError('STOP_PROCESS_IDENTITY_UNAVAILABLE') from error
        try:
            fields = record.rsplit(')', 1)[1].split()
            if fields[0] == 'Z':
                return None
            boot = next(int(line.split()[1]) for line in Path('/proc/stat').read_text().splitlines()
                        if line.startswith('btime '))
            # btime向下取整，所得创建时刻为保守下界；不会把原活进程误判为复用。
            seconds = boot + int(fields[19]) / os.sysconf('SC_CLK_TCK')
            return datetime.fromtimestamp(seconds, timezone.utc)
        except (OSError, ValueError, IndexError, StopIteration) as error:
            raise PermissionError('STOP_PROCESS_IDENTITY_UNAVAILABLE') from error
    raise PermissionError('STOP_PROCESS_IDENTITY_UNAVAILABLE')


def _original_process_active(pid, existed_at):
    created = _process_created_at(pid)
    if created is None:
        return False
    try:
        bound = datetime.fromisoformat(existed_at)
        _require(bound.tzinfo is not None, 'STOP_PROCESS_IDENTITY_UNAVAILABLE')
    except (TypeError, ValueError) as error:
        raise PermissionError('STOP_PROCESS_IDENTITY_UNAVAILABLE') from error
    # 仅能证明当前进程在原记录之后新建时才排除PID复用；相等仍拒绝。
    return created <= bound.astimezone(timezone.utc)


def _request(root, reference):
    value = _read(reference, root)
    _require(set(value) == {'schema_version', 'summary', 'approval_ref', 'source_archive', 'identity'}
             and value.get('schema_version') == REQUEST_VERSION and _identity(value), 'STOP_REQUEST_INVALID')
    summary = value['summary']
    _require(summary.get('schema_version') == SUMMARY_VERSION, 'STOP_SUMMARY_INVALID')
    approvals = OwnerApprovalStoreV1(summary['approval_store'])
    approvals.require(value['approval_ref'], summary)
    parent = _read(summary['parent_manifest_ref'], root)
    _require(_identity(parent) and parent.get('schema_version') == 'REPORT_REPAIR_MANIFEST_V1'
             and parent['summary']['repair_id'] == summary['repair_id'], 'STOP_PARENT_CONFLICT')
    approvals.require(parent['approval_ref'], parent['summary'])
    for key in ('proposal_ref', 'human_confirmation_ref'):
        _require(_reference(summary[key]['path'], root / 'reports') == summary[key], 'STOP_APPROVAL_SOURCE_CHANGED')
    output = Path(parent['summary']['original']['task']['path']).parent / 'report-repairs' / summary['repair_id']
    _require(Path(reference['path']) == output / 'STOP_REQUEST_V2.json', 'STOP_REQUEST_PATH_CONFLICT')
    _require([{'path': item['path'], 'sha256': item['sha256']} for item in value['source_archive']]
             == summary['parent_sources'], 'STOP_ARCHIVE_CONFLICT')
    for item in value['source_archive']:
        archive = output / 'stop-source-archive' / (item['sha256'] + '_' + Path(item['path']).name)
        _require(set(item) == {'path', 'sha256', 'archive_ref'}
                 and item['archive_ref'] == {'path': str(archive), 'sha256': item['sha256']}
                 and item['archive_ref'] == _reference(archive, root), 'STOP_SOURCE_ARCHIVE_CHANGED')
    for reference_value in summary['original_data_refs']:
        _require(_reference(reference_value['path'], root) == reference_value, 'STOP_ORIGINAL_DATA_CHANGED')
    return value, parent, output


def _ledger_terminal(root, base, record):
    """核同一账本原事件，不调用 scope/capability 解析，避免新能力桥递归。"""
    campaign = ResearchCampaignV1(root, base['authorization_id'])
    path = _path(campaign.budget_path.with_name('run_budget_events.jsonl'), root)
    raw = read_file_bytes(path, root=root, maximum_bytes=64 * 1024 * 1024,
                          error_code='REPORT_REPAIR_STOP_LEDGER_CHANGED')
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    ids, authorized, target = set(), [], []
    for event in events:
        payload, kind = event['payload'], event['event_type']
        identity = stable_hash({'run_id': base['authorization_id'], 'event_type': kind, 'payload': payload})
        _require(event.get('run_id') == base['authorization_id'] and event.get('event_id') == identity
                 and identity not in ids, 'STOP_LEDGER_IDENTITY_CONFLICT')
        ids.add(identity)
        if kind == 'CAMPAIGN_AUTHORIZED':
            authorized.append(payload)
        if payload.get('operation_id') == record['operation_id']:
            target.append((kind, payload))
    _require(authorized == [base], 'STOP_BASE_AUTHORIZATION_CONFLICT')
    before = record['operation_before']
    expected = []
    reserved = {key: value for key, value in before.items() if key not in (
        'status', 'started_at', 'segments', 'active_segment', 'active_wall_seconds', 'reason',
        'actual', 'outcome', 'evidence_identity', 'resource_overrun')}
    expected.append(('CAMPAIGN_OPERATION_RESERVED', reserved))
    expected.append(('CAMPAIGN_OPERATION_STARTED', {'operation_id': record['operation_id'],
                                                   'started_at': before['started_at']}))
    for segment in before['segments']:
        expected.append(('CAMPAIGN_EXECUTION_SEGMENT_DISPATCHED', {key: segment[key] for key in (
            'operation_id', 'segment_number', 'profile_hash', 'upper_bound_seconds', 'dispatched_at')}))
        expected.append(('CAMPAIGN_EXECUTION_SEGMENT_CHARGED', {key: segment[key] for key in (
            'operation_id', 'segment_number', 'measured_seconds', 'seconds', 'basis', 'evidence_identity',
            'outcome', 'charged_at')}))
    # UNKNOWN 不增加派发；同一原事件仍可通过已真实结算的段关闭。
    known = [(kind, payload) for kind, payload in target if kind != 'CAMPAIGN_OPERATION_UNKNOWN']
    terminal = ('CAMPAIGN_OPERATION_SETTLED', {'operation_id': record['operation_id'],
        'actual': record['actual'], 'outcome': 'FAILED', 'evidence_identity':
        before['evidence_identity'] if record['terminal_kind'] != 'CONTROLLED_STOP' else record['identity']})
    if record['terminal_kind'] != 'CONTROLLED_STOP' and before.get('resource_overrun'):
        terminal = ('CAMPAIGN_OPERATION_RESOURCE_OVERRUN_SETTLED', {**terminal[1],
                                                                    'resource_overrun': before['resource_overrun']})
    _require(known == expected + [terminal], 'STOP_TERMINAL_LEDGER_CONFLICT')


def read_report_repair_terminal(root, reference, *, base_authorization=None):
    """只读核对已结清原件；能力桥可用，不读取行情或调用新版能力解析。"""
    fixed_root = Path(root).resolve(strict=True)
    fixed_reference = deepcopy(reference)
    key = ('terminal', str(fixed_root), reference['path'], reference['sha256'])
    value = _within_terminal_proof(key, lambda: _read_report_repair_terminal(fixed_root, fixed_reference))
    _require(base_authorization is None or stable_hash(base_authorization) == value['base_authorization_identity'],
             'STOP_BASE_AUTHORIZATION_CONFLICT')
    return value


def _read_report_repair_terminal(root, reference, *, base_authorization=None):
    root = Path(root).resolve(strict=True)
    record = _read(reference, root)
    _require(record.get('schema_version') == TERMINAL_VERSION and record.get('status') == 'REPORT_REPAIR_STOPPED'
             and _identity(record), 'STOP_TERMINAL_INVALID')
    request, parent, output = _request(root, record['stop_request_ref'])
    summary, old = request['summary'], parent['summary']
    _require(Path(reference['path']) == output / 'STOP_TERMINAL_V2.json'
             and record['parent_manifest_ref'] == summary['parent_manifest_ref']
             and record['repair_id'] == summary['repair_id'] and record['task_id'] == old['task_id']
             and record['operation_id'] == summary['operation_id']
             and record['original_failed_operation_id'] == old['original_failure']['operation']['operation_id']
             and record['authorization_id'] == old['authorization_id']
             and record['source_archive'] == request['source_archive']
             and record['original_data_refs'] == summary['original_data_refs']
             and (record['terminal_kind'] == summary['terminal_kind'] or
                  (summary['terminal_kind'] == 'CONTROLLED_STOP' and record['terminal_kind'] == 'FAILED_AFTER_STOP')),
             'STOP_TERMINAL_PARENT_CONFLICT')
    campaign = ResearchCampaignV1(root, record['authorization_id'])
    base = _read(_reference(campaign.authorization_path, root), root)
    _require(base_authorization is None or base_authorization == base, 'STOP_BASE_AUTHORIZATION_CONFLICT')
    policy = base['scope_policy']
    OwnerApprovalStoreV1(policy['approval_store']).require(policy['approval_ref'], policy['summary'])
    _require(stable_hash(base) == record['base_authorization_identity'] == old['base_authorization_identity'],
             'STOP_BASE_AUTHORIZATION_CONFLICT')
    for reference_value in [old['original'][key] for key in ('task', 'job', 'preview', 'verification', 'results_index')]:
        _require(_reference(reference_value['path'], root) == reference_value, 'STOP_ORIGINAL_CHANGED')
    for reference_value in old['original']['inputs']:
        _require(_reference(reference_value['path'], root) == reference_value, 'STOP_ORIGINAL_CHANGED')
    for account in old['original']['accounts'].values():
        for key in ('result', 'settlement'):
            _require(_reference(account[key]['path'], root) == account[key], 'STOP_ORIGINAL_CHANGED')
    failure = old['original_failure']
    for reference_value in [failure[key] for key in (
            'attempt_end', 'scope', 'compute_start', 'dispatch', 'charge', 'resource')] + failure['candidate_evidence']:
        _require(_reference(reference_value['path'], root) == reference_value, 'STOP_ORIGINAL_FAILURE_CHANGED')
    start = _read(record['compute_start'], root)
    scope = _read(record['scope'], root)
    binding = record['compute_binding']
    _require(start['binding'] == binding and binding['compute_identity'] == stable_hash({
        key: value for key, value in binding.items() if key != 'compute_identity'})
        and scope.get('repair_ref') == record['parent_manifest_ref']
        and scope.get('job_sha256') == old['original']['job']['sha256'], 'STOP_COMPUTE_BINDING_CONFLICT')
    rows, charged, head = [], 0., None
    for number, refs in enumerate(record['segments'], 1):
        dispatch, charge = _read(refs['dispatch'], root), _read(refs['charge'], root)
        amount, basis = segment_charge(charge['measured_seconds'], dispatch['upper_bound_seconds'],
                                        charge['evidence_identity'], outcome=charge['outcome'])
        _require(dispatch['number'] == number and dispatch['previous_head'] == head
                 and dispatch['compute_identity'] == binding['compute_identity'] and _identity(dispatch, 'dispatch_id')
                 and charge['dispatch_id'] == dispatch['dispatch_id'] and _identity(charge, 'charge_id')
                 and charge['seconds'] == amount and charge['basis'] == basis, 'STOP_CHARGE_CHAIN_CONFLICT')
        for key in ('resource', 'status', 'worker', 'access'):
            if refs.get(key) is not None:
                _read(refs[key], root)
        if charge['basis'] != 'UNKNOWN_UPPER_BOUND':
            _require(refs.get('resource') is not None
                     and charge['evidence_identity'] == refs['resource']['sha256'], 'STOP_RESOURCE_CONFLICT')
        rows.append({'dispatch': dispatch, 'charge': charge})
        charged += amount
        head = charge['charge_id']
    state = {'segments': rows, 'charged_seconds': charged}
    meter = SimpleNamespace(binding=binding, profile=binding['profile'],
                            research_binding={'batch_id': old['batch_id']})
    _parent_mirror(meter, state, record['operation_before'])
    _require(rows and rows[-1]['charge']['outcome'] != 'COMPLETED'
             and record['operation_before']['status'] in (
                 ('RUNNING', 'UNKNOWN') if record['terminal_kind'] == 'CONTROLLED_STOP' else ('FAILED',))
             and record['operation_before'].get('active_segment') is None
             and record['actual'] == {**dict.fromkeys(RESOURCES, 0), 'verification_jobs': 1,
                                      'wall_seconds': max(1, math.ceil(charged))}, 'STOP_CONSUMPTION_CONFLICT')
    if record['terminal_kind'] != 'CONTROLLED_STOP':
        _require(rows[-1]['charge']['outcome'] == 'FAILED', 'STOP_FAILED_TERMINAL_CHANGED')
    control = _read(record['control_ref'], root) if record['control_ref'] else None
    _require(control == summary['control_event']
             and (record['terminal_kind'] == 'KNOWN_FAILED' or control['event'] == 'PAUSE'), 'STOP_CONTROL_CHANGED')
    _ledger_terminal(root, base, record)
    return record


class ReportRepairStopV2:
    """request 只停派；settle 只在真实边界追加关闭，不制造新计算段。"""

    @classmethod
    def summary(cls, root, parent_manifest_ref, reason, *, controller_pid,
                human_confirmation_ref, proposal_ref, reader_pid=None):
        root = Path(root).resolve(strict=True)
        context = ReportRepairV1(root, parent_manifest_ref).validate(for_dispatch=False)
        _require(isinstance(reason, str) and reason and type(controller_pid) is int and controller_pid > 0
                 and (reader_pid is None or type(reader_pid) is int and reader_pid > 0), 'STOP_IDENTITY_REQUIRED')
        view = context.campaign.peek_status()
        operation = view['operations'].get(context.campaign_operation['operation_id'], {})
        _require(operation.get('status') in ('RUNNING', 'UNKNOWN', 'FAILED'), 'STOP_PARENT_NOT_RUNNING')
        for reference in (human_confirmation_ref, proposal_ref):
            _require(_reference(reference['path'], root / 'reports') == reference, 'STOP_APPROVAL_SOURCE_CHANGED')
        controls = _control(context.job_path, root)
        known_failed = operation['status'] == 'FAILED'
        _require(known_failed or not controls or controls[-1]['event'] != 'PAUSE', 'STOP_ALREADY_PAUSED_WITHOUT_REQUEST')
        event = (controls[-1] if controls else None) if known_failed else {
            'sequence': len(controls) + 1, 'previous_head': controls[-1]['event_id'] if controls else None,
            'event': 'PAUSE', 'job_sha256': context.summary['original']['job']['sha256'],
            'receipt_id': stable_hash({'parent_manifest_ref': parent_manifest_ref, 'purpose': SUMMARY_VERSION}),
            'recorded_at': datetime.now(timezone.utc).isoformat()}
        if not known_failed:
            event['event_id'] = stable_hash(event)
        code, data = _source_refs(context)
        controller_sources = [_reference(root / path, root) for path in STOP_SOURCE_PATHS]
        code_map = {item['path']: item for item in code + controller_sources}
        return {'schema_version': SUMMARY_VERSION, 'root': os.path.normcase(str(root)),
            'parent_manifest_ref': deepcopy(parent_manifest_ref), 'repair_id': context.repair_id,
            'operation_id': context.campaign_operation['operation_id'],
            'authorization_id': context.summary['authorization_id'], 'approval_store': context.summary['approval_store'],
            'reason': reason, 'controller_pids': [controller_pid] + ([] if reader_pid is None else [reader_pid]),
            'proposal_ref': deepcopy(proposal_ref), 'human_confirmation_ref': deepcopy(human_confirmation_ref),
            'controller_sources': controller_sources,
            'parent_sources': [code_map[key] for key in sorted(code_map)], 'original_data_refs': data,
            'control_event': event, 'terminal_kind': 'KNOWN_FAILED' if known_failed else 'CONTROLLED_STOP',
            'terminal_operation': deepcopy(operation) if known_failed else None,
            'allowed_action': 'CAPTURE_KNOWN_FAILED_TERMINAL' if known_failed else 'STOP_AT_SETTLED_SEGMENT_BOUNDARY', 'kill_process': False,
            'replay_account': False, 'refund_consumption': False, 'new_dispatches': 0}

    @classmethod
    def request(cls, root, summary, *, approval_ref):
        root = Path(root).resolve(strict=True)
        context = ReportRepairV1(root, summary['parent_manifest_ref']).validate(for_dispatch=False)
        target = context.output_root / 'STOP_REQUEST_V2.json'
        with ObjectiveMutationLock.for_resource(context.job_path.parent / 'CONTROL.lock'):
            if target.exists():
                existing = _read(_reference(target, root), root)
                _require(existing['summary'] == summary and existing['approval_ref'] == approval_ref,
                         'STOP_IMMUTABLE_CONFLICT')
                request, _, _ = _request(root, _reference(target, root))
            else:
                current = cls.summary(root, summary['parent_manifest_ref'], summary['reason'],
                    controller_pid=summary['controller_pids'][0],
                    reader_pid=summary['controller_pids'][1] if len(summary['controller_pids']) == 2 else None,
                    human_confirmation_ref=summary['human_confirmation_ref'], proposal_ref=summary['proposal_ref'])
                if summary['terminal_kind'] == 'CONTROLLED_STOP':
                    current['control_event']['recorded_at'] = summary['control_event']['recorded_at']
                    current['control_event']['event_id'] = stable_hash({key: value for key, value in
                        current['control_event'].items() if key != 'event_id'})
                _require(current == summary, 'STOP_SUMMARY_CHANGED')
                OwnerApprovalStoreV1(summary['approval_store']).require(approval_ref, summary)
                archived = []
                for reference in summary['parent_sources']:
                    content = read_file_bytes(reference['path'], root=root,
                        error_code='REPORT_REPAIR_STOP_SOURCE_CHANGED')
                    _require(hashlib.sha256(content).hexdigest() == reference['sha256'], 'STOP_SOURCE_CHANGED')
                    path = context.output_root / 'stop-source-archive' / (reference['sha256'] + '_' + Path(reference['path']).name)
                    _path(path, root)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if not path.exists():
                        with path.open('xb') as stream:
                            stream.write(content)
                            stream.flush()
                            os.fsync(stream.fileno())
                    _require(_reference(path, root)['sha256'] == reference['sha256'], 'STOP_SOURCE_ARCHIVE_CHANGED')
                    archived.append({**reference, 'archive_ref': _reference(path, root)})
                request = {'schema_version': REQUEST_VERSION, 'summary': deepcopy(summary),
                    'approval_ref': deepcopy(approval_ref), 'source_archive': archived}
                request['identity'] = stable_hash(request)
                _publish(target, request, root)
            event = request['summary']['control_event']
            if summary['terminal_kind'] == 'KNOWN_FAILED':
                return _reference(target, root)
            controls = _control(context.job_path, root)
            _require(len(controls) in (event['sequence'] - 1, event['sequence'])
                     and (len(controls) < event['sequence'] or controls[-1] == event), 'STOP_CONTROL_CHANGED')
            _publish(context.job_path.parent / ('CONTROL_' + str(event['sequence']).zfill(6) + '.json'), event, root)
            return _reference(target, root)

    @classmethod
    def settle(cls, root, stop_request_ref):
        root = Path(root).resolve(strict=True)
        request, parent, output = _request(root, stop_request_ref)
        summary = request['summary']
        terminal = output / 'STOP_TERMINAL_V2.json'
        if terminal.exists():
            reference = _reference(terminal, root)
            try:
                read_report_repair_terminal(root, reference)
            except PermissionError:
                pass  # 仅可能是发布后、原账本结清前中断；下方同身份补结清。
            else:
                return reference
        context = ReportRepairV1(root, summary['parent_manifest_ref']).validate(for_dispatch=False)
        with ObjectiveMutationLock.for_resource(context.compute_root / 'EXECUTE.lock'):
            meter = context.meter(for_dispatch=False)
            state = meter.reconcile_segment_mirrors()
            _require(state['pending'] is None and state['segments'], 'STOP_SEGMENT_UNRESOLVED')
            refs = []
            for row in state['segments']:
                number = str(row['dispatch']['number']).zfill(6)
                entry = {key: _reference(context.compute_root / ('COMPUTE_SEGMENT_' + number + '_' + suffix + '.json'), root)
                         for key, suffix in (('dispatch', 'DISPATCH'), ('charge', 'CHARGE'))}
                for key, suffix in (('resource', 'RESOURCE'), ('status', 'STATUS'), ('worker', 'WORKER'), ('access', 'ACCESS')):
                    path = context.compute_root / ('SEGMENT_' + number + '_' + suffix + '.json')
                    entry[key] = _reference(path, root) if path.exists() else None
                    if key in ('worker', 'access') and entry[key] is not None:
                        value = _read(entry[key], root)
                        _require(value.get('dispatch_id') == row['dispatch']['dispatch_id'], 'STOP_PROCESS_BINDING_CONFLICT')
                        # 用原账本收费时间作保守上界，不信未由收费直接固定的更早访问时间。
                        _require(not _original_process_active(value['pid' if key == 'worker' else 'reader_pid'],
                                                             row['charge']['charged_at']),
                                 'STOP_PROCESS_STILL_ACTIVE')
                refs.append(entry)
            _require(all(_process_created_at(pid) is None for pid in summary['controller_pids']),
                     'STOP_PROCESS_STILL_ACTIVE')
            operation = context.campaign.peek_status()['operations'][summary['operation_id']]
            _require(operation['status'] in (('FAILED',) if summary['terminal_kind'] == 'KNOWN_FAILED' else ('RUNNING', 'UNKNOWN', 'FAILED'))
                     and operation.get('active_segment') is None
                     and state['segments'][-1]['charge']['outcome'] != 'COMPLETED', 'STOP_PARENT_NOT_RUNNING')
            if summary['terminal_kind'] == 'KNOWN_FAILED':
                _require(operation == summary['terminal_operation']
                         and state['segments'][-1]['charge']['outcome'] == 'FAILED', 'STOP_FAILED_TERMINAL_CHANGED')
            terminal_kind = summary['terminal_kind']
            if terminal_kind == 'CONTROLLED_STOP' and operation['status'] == 'FAILED':
                _require(state['segments'][-1]['charge']['outcome'] == 'FAILED', 'STOP_FAILED_TERMINAL_CHANGED')
                terminal_kind = 'FAILED_AFTER_STOP'
            _parent_mirror(meter, state, operation)
            controls = _control(context.job_path, root)
            _require((controls[-1] if controls else None) == summary['control_event'], 'STOP_CONTROL_CHANGED')
            record = {'schema_version': TERMINAL_VERSION, 'status': 'REPORT_REPAIR_STOPPED',
                'parent_manifest_ref': deepcopy(summary['parent_manifest_ref']), 'stop_request_ref': deepcopy(stop_request_ref),
                'authorization_id': summary['authorization_id'], 'base_authorization_identity': parent['summary']['base_authorization_identity'],
                'task_id': parent['summary']['task_id'], 'repair_id': summary['repair_id'],
                'original_failed_operation_id': parent['summary']['original_failure']['operation']['operation_id'],
                'operation_id': summary['operation_id'], 'operation_before': operation,
                'actual': {**dict.fromkeys(RESOURCES, 0), 'verification_jobs': 1,
                           'wall_seconds': max(1, math.ceil(state['charged_seconds']))},
                'compute_binding': meter.binding, 'scope': _reference(context.compute_root / 'SCOPE.json', root),
                'compute_start': _reference(context.compute_root / 'COMPUTE_START.json', root),
                'segments': refs, 'source_archive': request['source_archive'],
                'original_data_refs': summary['original_data_refs'], 'terminal_kind': terminal_kind,
                'control_ref': _reference(context.job_path.parent / ('CONTROL_' + str(summary['control_event']['sequence']).zfill(6) + '.json'), root)
                    if summary['control_event'] else None}
            record['identity'] = stable_hash(record)
            target = output / 'STOP_TERMINAL_V2.json'
            if target.exists():
                _require(_read(_reference(target, root), root) == record, 'STOP_TERMINAL_CONFLICT')
            else:
                immutable(target, record)
            if terminal_kind == 'CONTROLLED_STOP':
                context.campaign.settle_operation(summary['operation_id'], actual=record['actual'], outcome='FAILED',
                                                  evidence_identity=record['identity'])
            reference = _reference(target, root)
            read_report_repair_terminal(root, reference)
            return reference
