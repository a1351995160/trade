"""验收所有者已在上游物理限窗的公司行动包；不访问TQ或gbbq。"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path


@dataclass(frozen=True)
class WindowedCorporateActionDatasetV1:
    dataset_id: str
    version: str
    start: int
    end: int
    events: tuple
    manifest_sha256: str
    coverage_symbols: tuple
    source_identity: str

    @classmethod
    def load(cls, manifest_path, expected_sha256, required_symbols):
        path=Path(manifest_path).resolve()
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=expected_sha256:
            raise ValueError('ACTION_MANIFEST_HASH_CONFLICT')
        m=json.loads(raw)
        if m['version']!='WindowedCorporateActionDatasetV1' or (m['start'],m['end'])!=(20220722,20240731):
            raise ValueError('ACTION_DATASET_WINDOW_CONFLICT')
        if m['dataset_id'] in {None,'','UNKNOWN'} or m['source_identity'] in {None,'','UNKNOWN'}:
            raise ValueError('ACTION_SOURCE_IDENTITY_MISSING')
        attestation=m['physical_window_attestation']
        if not attestation.get('owner') or attestation.get('window_enforced_before_export') is not True:
            raise PermissionError('OWNER_PHYSICAL_WINDOW_ATTESTATION_REQUIRED')
        if attestation.get('not_derived_from_current_incident') is not True:
            raise PermissionError('INDEPENDENT_OWNER_SOURCE_ATTESTATION_REQUIRED')
        if (attestation.get('start'),attestation.get('end'))!=(m['start'],m['end']):
            raise PermissionError('OWNER_ATTESTATION_WINDOW_CONFLICT')
        coverage=m['coverage']
        if coverage.get('complete') is not True or set(coverage['symbols'])!=set(required_symbols):
            raise ValueError('ACTION_COVERAGE_UNKNOWN_NOT_KNOWN_NONE')
        name=m['events_file']
        if Path(name).name!=name or not name.endswith('.jsonl'):
            raise PermissionError('WINDOWED_ACTION_FILE_REQUIRED')
        event_path=path.parent/name
        if event_path.resolve()!=event_path or 'get_divid_factors' in name.lower():
            raise PermissionError('UNSAFE_ACTION_SOURCE')
        data=event_path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=m['events_sha256']:
            raise ValueError('ACTION_EVENTS_HASH_CONFLICT')
        events=[json.loads(line) for line in data.splitlines() if line.strip()]
        seen,pairs=set(),set()
        required={'event_id','symbol','effective_date','event_type','terms','units','source','source_published_at'}
        supported_types={'CASH_DIVIDEND','SPLIT','BONUS','CAPITALIZATION','CONSOLIDATION','RIGHTS','DELISTING','TERMINATION'}
        for e in events:
            if not required<=e.keys() or not isinstance(e['terms'],dict) or not e['source'] or not e['event_id']:
                raise ValueError('ACTION_EVENT_FIELDS_MISSING')
            if e['symbol'] not in required_symbols or type(e['effective_date']) is not int or not m['start']<=e['effective_date']<=m['end']:
                raise ValueError('ACTION_EVENT_OUTSIDE_WINDOW')
            if e['event_type'] not in supported_types:raise ValueError('ACTION_TYPE_UNSUPPORTED')
            pair=(e['symbol'],e['effective_date'],e['event_type'])
            if e['event_id'] in seen:raise ValueError('ACTION_DUPLICATE_EVENT_ID')
            if pair in pairs:raise ValueError('ACTION_SAME_SECURITY_CONFLICT')
            seen.add(e['event_id']); pairs.add(pair)
        return cls(m['dataset_id'],m['version'],m['start'],m['end'],tuple(events),expected_sha256,
                   tuple(sorted(required_symbols)),m['source_identity'])
