import json
import os
from pathlib import Path
import sys

from chanlun_trader.synthetic_batch_resources import run_bounded_worker


def test_full_inventory_scale_runs_under_real_limiter_and_is_batch_order_invariant(tmp_path):
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
        **{key: '1' for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')}}
    env['PYTHONPATH'] = str(root / 'src') + os.pathsep + str(root)
    values = []
    for batch, reverse in [(1, False), (128, True)]:
        command = [sys.executable, str(root / 'scripts/probe_universe_resources_v1.py'), '--worker',
                   '--stocks', '4536', '--batch-size', str(batch)]
        if reverse:
            command.append('--reverse')
        resource = run_bounded_worker(command, root=root, memory_mib=2048, wall_seconds=900,
            environment=env, execution={'purpose': 'FULL_UNIVERSE_ENGINEERING_SCALE'}, on_started=lambda pid: None)
        assert resource['returncode'] == 0 and not resource.get('timed_out'), resource
        value = json.loads(resource['stdout'])
        assert value['target'] == 4536 and value['account_reconciled']
        assert value['each_session_processed'] == [4536] * 5
        assert value['resource_handshake'] and value['synthetic'] and not value['real_acceptance']
        assert value['peak_process_working_set_kib'] > 0
        values.append({'measurements': value, 'resource': {key: val for key, val in resource.items()
            if key not in {'stdout', 'stderr'}}})
    assert values[0]['measurements']['result_identity'] == values[1]['measurements']['result_identity']
    assert values[0]['measurements']['economic_identity'] == values[1]['measurements']['economic_identity']
    (tmp_path / 'SCALE_RESOURCE_EVIDENCE.json').write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding='utf-8')
