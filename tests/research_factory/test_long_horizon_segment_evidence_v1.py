"""资源证明区分实际测量与中断上界收费，恢复不能洗掉未知段。"""
from datetime import datetime, timezone
import json

import pytest

from chanlun_trader.research_factory.research_evidence_v1 import _sha, _verify_segment_resources
from test_universe_execution_profile_v1 import governance


def write(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


def case(tmp_path):
    gov = governance(tmp_path)
    root = gov.root
    first = gov.start_segment('FIXED')
    prefix = root / 'FIXED_SEGMENT_000001'
    write(prefix.with_name(prefix.name + '_WORKER.json'), {'pid': 123, 'purpose': 'FIXED',
        'dispatch_id': first['dispatch_id'], 'segment_number': 1})
    gov.end_segment('FIXED', 1, outcome='CONTINUE')
    write(prefix.with_name(prefix.name + '_RESUME.json'), {'dispatch_id': first['dispatch_id'],
        'budget_reused': True, 'charge_basis': 'UNKNOWN_UPPER_BOUND', 'state_identity': 'a' * 64,
        'resumed_at': datetime.now(timezone.utc).isoformat()})
    last = gov.start_segment('FIXED')
    fields = {'dispatch_id': last['dispatch_id'], 'segment_number': 2}
    prefix = root / 'FIXED_SEGMENT_000002'
    resource = {'elapsed_wall_seconds': 4., 'returncode': 0, 'timed_out': False, **fields}
    path = prefix.with_name(prefix.name + '_RESOURCE.json')
    write(path, resource)
    write(prefix.with_name(prefix.name + '_WORKER.json'), {'pid': 124, 'purpose': 'FIXED', **fields})
    write(prefix.with_name(prefix.name + '_INPUT_ACCESS.json'), {'reader_pid': 124, 'purpose': 'FIXED',
        'input_identity': 'FROZEN', 'loader': 'trusted:loader', 'loader_kwargs': {}, **fields})
    gov.end_segment('FIXED', 2, seconds=4., evidence_identity=_sha(path), outcome='COMPLETED')
    state = gov.segment_status('FIXED')
    aggregate = {'elapsed_wall_seconds': state['charged_seconds'], 'active_metering': True,
        'segment_count': 2, 'segments': [row['charge']['charge_id'] for row in state['segments']]}
    job = {'root': str(root), 'budget_path': str(gov.budget_path), 'objective_id': gov.objective_id,
        'plans': gov.plans, 'input_identity': 'FROZEN',
        'items': {'FIXED': {'loader': 'trusted:loader', 'loader_kwargs': {}}}}
    return job, aggregate, {'wall_seconds': state['charged_seconds']}


def test_interrupted_upper_bound_is_kept_separate_from_measured_success(tmp_path):
    job, aggregate, settlement = case(tmp_path)
    proof = _verify_segment_resources(job, 'FIXED', aggregate, settlement)
    assert proof['measured_seconds'] == 4.
    assert proof['conservatively_charged_seconds'] == 900.
    assert proof['charged_seconds'] == 904.


@pytest.mark.parametrize('change', ['missing_resume', 'dispatch', 'budget', 'late_time', 'fake_measurement'])
def test_unknown_segment_cannot_erase_or_fabricate_recovery_proof(tmp_path, change):
    job, aggregate, settlement = case(tmp_path)
    root = tmp_path / 'account'
    resume = root / 'FIXED_SEGMENT_000001_RESUME.json'
    value = json.loads(resume.read_text(encoding='utf-8'))
    if change == 'missing_resume':
        resume.unlink()
    elif change == 'fake_measurement':
        write(root / 'FIXED_SEGMENT_000001_RESOURCE.json', {'elapsed_wall_seconds': 0})
    else:
        if change == 'dispatch':
            value['dispatch_id'] = 'other'
        elif change == 'budget':
            value['budget_reused'] = False
        else:
            value['resumed_at'] = '2099-01-01T00:00:00+00:00'
        write(resume, value)
    with pytest.raises((ValueError, FileNotFoundError)):
        _verify_segment_resources(job, 'FIXED', aggregate, settlement)
