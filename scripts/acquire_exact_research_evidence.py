"""封闭读取集合的历史盲化取证；不导入执行、预算或授权服务。"""
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path('E:/llmwiki/chanlun-trading-system')
EVIDENCE = Path('E:/llmwiki/autonomous-strategy-research-v1/blocker-resolution-v2')
OUTPUT = EVIDENCE/'exact-evidence-v1'
FIXED = (
    'reports/CURRENT_EFFECTIVE_RESEARCH_HISTORY_V1.json',
    'reports/RUN_AUTONOMOUS_ALPHA_AFTER_SAMPLE_POLICY_V2/multiple_testing.json',
    'reports/research_orchestrator_v2/RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1/predictive_governance_decisions.jsonl',
    'reports/research_orchestrator_v2/RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1/structural_governance_decision_required.json',
)
IDS = set('schema_version version objective_id parent_objective_id programme_id batch_id candidate_id candidate_hash family_id trial_id factor_id authorization_id receipt_id reservation_id budget_id created_at expires_at revoked_at timestamp accessed_at event_id authorization_ref budget_ref policy_ref source_ref validation_policy_hash policy_hash'.split())
COUNTS = set('limit used reserved remaining max_total_trials trial_count event_count exposure_count validation_access_count train_access_count performance_access_count access_count factor_count total_trials total_factors total_accesses n_trials n_factors family_size'.split())
WINDOWS = set('start end start_date end_date train_start train_end validation_start validation_end research_start research_end execution_start execution_end'.split())
FLAGS = set('performance_accessed revoked settled history_complete complete authorized confirmed'.split())
TOKEN = re.compile(r'[A-Za-z0-9_:.+/@\\-]{1,400}\Z')
IDS.update('original_trial_id decision_family_id family_contract_hash history_snapshot_hash authorization_decision_id authorization_decision_hash governance_decision_id contract_id policy_id policy_version snapshot_hash registration_ref candidate_contract_ref report_ref structural_reconciliation_ref'.split())
COUNTS.update('snapshot_count hypothesis_count current_batch_hypothesis_count cumulative_legal_history_denominator decision_denominator legal_denominator trial_number'.split())


def project(payload):
    """递归发现元数据但仅返回固定字段；不输出未知键、自由文本或研究好坏分类。"""
    nodes = []
    def visit(value, parent=None):
        if isinstance(value, list):
            for item in value:
                visit(item, parent)
        elif isinstance(value, dict):
            kept = {}
            for key, item in value.items():
                if key in IDS and isinstance(item, str) and TOKEN.fullmatch(item):
                    kept[key] = item
                elif key in COUNTS and type(item) is int and item >= 0:
                    kept[key] = item
                elif key in FLAGS and type(item) is bool:
                    kept[key] = item
                elif key in WINDOWS and ((isinstance(item, str) and re.fullmatch(r'\d{4}-?\d{2}-?\d{2}', item)) or (type(item) is int and 19000101 <= item <= 22001231)):
                    kept[key] = item
                elif key in {'trial_ids', 'candidate_ids', 'factor_ids', 'member_trial_ids'} and isinstance(item, list):
                    kept[key] = [x for x in item if isinstance(x, str) and TOKEN.fullmatch(x)]
                elif key == 'authorization_status' and item in ('AUTHORIZED', 'CONFIRMED', 'REVOKED', 'EXPIRED', 'PENDING'):
                    kept[key] = item
            own = parent
            if kept:
                own = len(nodes)
                nodes.append({'node': own, 'parent': parent, 'fields': kept})
            for item in value.values():
                if isinstance(item, (dict, list)):
                    visit(item, own)
    visit(payload)
    return nodes


def build_set(history, requests, root):
    budget_by_batch = {f['path'].split('/')[1]: f['projection'].get('objective_id')
                       for f in history['files'] if f['path'].endswith('/search_budget_registry.json')}
    observed = set()
    for f in history['files']:
        if f['path'].endswith('/trial_registry.json'):
            batch = f['path'].split('/')[1]
            for event in f['projection'].get('events', []):
                if event.get('trial_id') and event.get('candidate_id'):
                    observed.add((budget_by_batch.get(batch), batch, event['candidate_id'], event['trial_id']))
    if len(observed) != 16 or len(requests['requests']) != 16:
        raise ValueError('OBSERVED_SET_NOT_16')
    entries = [{'path': str(root/p), 'identity': {}, 'source': 'USER_FIXED_PATH', 'purpose': 'AUTHORITY_AND_EXPOSURE'} for p in FIXED]
    for request in requests['requests']:
        identity = {key: request[key] for key in ('objective_id', 'batch_id', 'candidate_id', 'trial_id')}
        if tuple(identity.values()) not in observed or any(not re.fullmatch(r'[A-Za-z0-9_-]+', x) for x in identity.values()):
            raise ValueError('IDENTITY_NOT_IN_OBSERVED_SET')
        oid, batch, candidate, trial = identity.values()
        base = root/'reports/research_daemon'/oid/'predictive'
        suffix = int(trial.rsplit('_T', 1)[1])
        gate = base/batch/candidate
        if suffix > 1:
            gate = gate/trial
        expected = {'gate_expected_path': gate/'performance_access_gate.json',
                    'contract_expected_path': base/'trial_contracts'/(trial+'.json')}
        for field, path in expected.items():
            if os.path.normcase(os.path.abspath(request[field])) != os.path.normcase(str(path.absolute())):
                raise ValueError('REQUEST_PATH_NOT_DERIVED_FROM_IDENTITY')
            entries.append({'path': str(path), 'identity': identity, 'source': 'OBSERVED_16_TRIALS', 'purpose': field})
    if len({e['path'] for e in entries}) != len(entries):
        raise ValueError('DUPLICATE_READ_PATH')
    return entries


def read_entry(entry, root):
    path = Path(entry['path'])
    result = {'path': str(path), 'expected_identity': entry['identity'], 'access_authorized': True}
    try:
        if not path.is_relative_to(root) or path.resolve() != path.absolute():
            raise ValueError('REDIRECTED_PATH')
        before = path.stat()
        if not path.is_file():
            result['status'] = 'WRONG_FILE_TYPE'
            return result
        raw = path.read_bytes()
        result.update(physical_bytes_read=len(raw), sha256=hashlib.sha256(raw).hexdigest(), hash_type='ORIGINAL_FILE_BYTES')
        payload = [json.loads(line) for line in raw.decode('utf-8-sig').splitlines() if line.strip()] if path.suffix == '.jsonl' else json.loads(raw)
        if not isinstance(payload, (dict, list)):
            raise ValueError('INVALID_RECORD_CONTAINER')
        result['projection'] = project(payload)
        conflict = isinstance(payload, dict) and any(key in payload and payload[key] != value for key, value in entry['identity'].items())
        result['status'] = 'IDENTITY_CONFLICT' if conflict else 'READ_PROJECTED'
        after = path.stat()
        result['source_stat_unchanged'] = (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
        if not result['source_stat_unchanged']:
            result['status'] = 'SOURCE_CHANGED'
    except FileNotFoundError:
        result['status'] = 'MISSING'
    except PermissionError:
        result['status'] = 'ACCESS_DENIED'
    except (ValueError, UnicodeError) as exc:
        result.update(status='CORRUPT_OR_INVALID_PATH', exception_type=type(exc).__name__)
    except OSError as exc:
        result.update(status='IO_ERROR', exception_type=type(exc).__name__)
    return result


def main():
    OUTPUT.mkdir(exist_ok=False)
    history = json.loads((EVIDENCE/'HISTORY_BUDGET_BLIND_RECONCILIATION_FINAL.json').read_text(encoding='utf-8-sig'))
    requests = json.loads((EVIDENCE/'HISTORICAL_AUTHORITY_EXACT_REQUESTS.json').read_text(encoding='utf-8-sig'))
    entries = build_set(history, requests, ROOT)
    plan = {'authority': False, 'entries': entries, 'fields': sorted(IDS|COUNTS|WINDOWS|FLAGS),
            'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    canonical = json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()
    plan['read_set_sha256_without_hash_field'] = hashlib.sha256(canonical).hexdigest()
    (OUTPUT/'EXACT_READ_SET.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    results = [read_entry(entry, ROOT) for entry in entries]
    report = {'authority': False, 'files': results, 'policy_linked_budget_remaining': 0,
              'global_completeness': 'UNKNOWN', 'new_performance_trials': 0,
              'design_side_exact_performance_exposed': False,
              'controlled_parser_may_have_read_performance_bytes': any(r.get('physical_bytes_read', 0) for r in results)}
    (OUTPUT/'HISTORICAL_AUTHORITY_BLIND_RECONCILIATION.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'files': len(results), 'statuses': {s: sum(r['status']==s for r in results) for s in sorted({r['status'] for r in results})}}))


if __name__ == '__main__':
    main()
