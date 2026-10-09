import os
from pathlib import Path
import sys

import pytest

from chanlun_trader.synthetic_batch_resources import run_bounded_worker


def test_long_worker_reports_real_job_peak_without_changing_legacy_receipts(tmp_path):
    source = Path(__file__).resolve().parents[2]
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(source / 'tests/isolation'), str(source / 'src')]))
    options = {'root': tmp_path, 'memory_mib': 96, 'wall_seconds': 10,
               'environment': environment, 'on_started': lambda pid: None}
    command = [sys.executable, str(Path(__file__).with_name('batch_resource_worker.py')), 'normal']
    legacy = run_bounded_worker(command, **options)
    assert legacy['returncode'] == 0 and 'peak_memory_mib' not in legacy
    measured = run_bounded_worker(command, measure_peak_memory=True, **options)
    assert measured['returncode'] == 0
    if os.name == 'nt':
        assert measured['memory_measurement'] == 'WINDOWS_JOB_PEAK_COMMIT'
        assert 0 < measured['peak_memory_mib'] <= 96
    else:
        assert measured['memory_measurement'] == 'NOT_MEASURED_ON_THIS_HOST'
        assert measured['peak_memory_mib'] is None


def test_memory_measurement_option_is_not_an_arbitrary_resource_override(tmp_path):
    with pytest.raises(ValueError, match='MEMORY_MEASUREMENT_OPTION_INVALID'):
        run_bounded_worker([sys.executable], root=tmp_path, memory_mib=96, wall_seconds=1,
                           on_started=lambda pid: None, measure_peak_memory=2048)


def test_new_backend_cannot_drop_profile_and_fall_back_to_old_resource_policy():
    from scripts.run_strategy_account_v1 import validate_sources
    item = {'benchmark_mode': 'NONE'}
    job = {'items': {'case': item}, 'plans': {'case': {'runtime': item,
           'backend': {'backend': 'UNIVERSE_ACCOUNT_BACKEND_V2'}}}}
    with pytest.raises(PermissionError, match='JOB_LONG_HORIZON_PROFILE_REQUIRED'):
        validate_sources(job)
